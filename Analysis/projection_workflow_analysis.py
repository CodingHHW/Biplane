from __future__ import annotations

from typing import Callable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns


METRIC_SPECS = [
    ("tre_value_mm", "TRE (mm)"),
    ("reprojection_error_px", "RE (px)"),
]

MATCHING_SCHEMES = [
    (
        "full_key_r2",
        [
            "input_volume",
            "testpoint_x",
            "testpoint_y",
            "testpoint_z",
            "shot2_angle_deg",
            "shot3_angle_m3_m1_deg",
            "shot3_angle_m3_m2_deg",
        ],
        2,
    ),
    ("volume_coords_r2", ["input_volume", "testpoint_x", "testpoint_y", "testpoint_z"], 2),
    ("volume_coords_r0", ["input_volume", "testpoint_x", "testpoint_y", "testpoint_z"], 0),
    (
        "volume_angles_r2",
        ["input_volume", "shot2_angle_deg", "shot3_angle_m3_m1_deg", "shot3_angle_m3_m2_deg"],
        2,
    ),
]

DISTANCE_ORDER = ["Near", "Mid", "Far"]


def _default_display(obj):
    if hasattr(obj, "to_string"):
        print(obj.to_string())
    else:
        print(obj)


def _iqr(values: pd.Series) -> float:
    if len(values) == 0:
        return np.nan
    return values.quantile(0.75) - values.quantile(0.25)


def _count_matching_pairs(frame: pd.DataFrame, raw_keys: list[str], decimals: int) -> tuple[int, int]:
    keys = [col for col in raw_keys if col in frame.columns]
    if "projection_mode" not in frame.columns or not keys:
        return 0, len(keys)

    temp = frame.copy()
    key_cols: list[str] = []
    for col in keys:
        if pd.api.types.is_numeric_dtype(temp[col]):
            rounded_col = f"{col}_r"
            temp[rounded_col] = temp[col].round(decimals)
            key_cols.append(rounded_col)
        else:
            key_cols.append(col)

    grouped = temp.groupby(key_cols + ["projection_mode"]).size().unstack(fill_value=0)
    if "Perspective" not in grouped.columns or "Orthographic" not in grouped.columns:
        return 0, len(keys)
    paired_rows = int(((grouped["Perspective"] > 0) & (grouped["Orthographic"] > 0)).sum())
    return paired_rows, len(keys)


def _bootstrap_difference(
    a: pd.Series | np.ndarray,
    b: pd.Series | np.ndarray,
    statistic: Callable[[np.ndarray], float],
    iterations: int,
    rng: np.random.Generator,
) -> tuple[float, float, float]:
    arr_a = np.asarray(pd.Series(a).dropna(), dtype=float)
    arr_b = np.asarray(pd.Series(b).dropna(), dtype=float)
    if len(arr_a) == 0 or len(arr_b) == 0:
        return np.nan, np.nan, np.nan

    observed = float(statistic(arr_a) - statistic(arr_b))
    boot = np.empty(iterations, dtype=float)
    for i in range(iterations):
        sample_a = rng.choice(arr_a, size=len(arr_a), replace=True)
        sample_b = rng.choice(arr_b, size=len(arr_b), replace=True)
        boot[i] = statistic(sample_a) - statistic(sample_b)

    low, high = np.percentile(boot, [2.5, 97.5])
    return observed, float(low), float(high)


def _mann_whitney_pvalue(
    a: pd.Series,
    b: pd.Series,
    scipy_available: bool,
    stats_module,
) -> float:
    if not scipy_available or stats_module is None:
        return np.nan
    if len(a) == 0 or len(b) == 0:
        return np.nan
    try:
        return float(stats_module.mannwhitneyu(a, b, alternative="two-sided").pvalue)
    except Exception:
        return np.nan


def _cliffs_delta(a: pd.Series, b: pd.Series) -> float:
    arr_a = np.asarray(pd.Series(a).dropna(), dtype=float)
    arr_b = np.asarray(pd.Series(b).dropna(), dtype=float)
    if len(arr_a) == 0 or len(arr_b) == 0:
        return np.nan
    comparison = arr_a[:, None] - arr_b[None, :]
    greater = np.sum(comparison > 0)
    lower = np.sum(comparison < 0)
    return float((greater - lower) / (len(arr_a) * len(arr_b)))


def _cliffs_magnitude(delta: float) -> str:
    if not np.isfinite(delta):
        return "NA"
    abs_delta = abs(delta)
    if abs_delta < 0.147:
        return "negligible"
    if abs_delta < 0.33:
        return "small"
    if abs_delta < 0.474:
        return "medium"
    return "large"


def _build_matching_diagnostics(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scheme_name, raw_keys, decimals in MATCHING_SCHEMES:
        paired_rows, used_keys = _count_matching_pairs(frame, raw_keys, decimals)
        rows.append(
            {
                "scheme": scheme_name,
                "decimals": decimals,
                "used_keys": used_keys,
                "paired_rows": paired_rows,
            }
        )
    return pd.DataFrame(rows)


def _build_mode_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (volume, mode), sub in frame.groupby(["input_volume", "projection_mode"]):
        row = {
            "input_volume": volume,
            "projection_mode": mode,
            "n": len(sub),
        }
        for metric, _ in METRIC_SPECS:
            values = sub[metric].dropna()
            metric_prefix = metric.replace("_value", "").replace("reprojection_error", "re")
            row[f"{metric_prefix}_mean"] = values.mean() if len(values) else np.nan
            row[f"{metric_prefix}_median"] = values.median() if len(values) else np.nan
            row[f"{metric_prefix}_iqr"] = _iqr(values)
        if "testpoint_marker_distance_mean_mm" in sub.columns:
            dist = sub["testpoint_marker_distance_mean_mm"].dropna()
            row["distance_mean_mm"] = dist.mean() if len(dist) else np.nan
            row["distance_median_mm"] = dist.median() if len(dist) else np.nan
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["input_volume", "projection_mode"]).reset_index(drop=True)


def _build_comparison_summary(
    frame: pd.DataFrame,
    scipy_available: bool,
    stats_module,
    bootstrap_iterations: int,
    equivalence_margins: dict[str, float | None],
) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    rows = []
    for volume, sub in frame.groupby("input_volume"):
        perspective = sub[sub["projection_mode"] == "Perspective"]
        orthographic = sub[sub["projection_mode"] == "Orthographic"]
        if perspective.empty or orthographic.empty:
            continue

        for metric, _ in METRIC_SPECS:
            p_values = perspective[metric].dropna()
            o_values = orthographic[metric].dropna()
            median_diff, median_low, median_high = _bootstrap_difference(
                p_values, o_values, np.median, bootstrap_iterations, rng
            )
            mean_diff, mean_low, mean_high = _bootstrap_difference(
                p_values, o_values, np.mean, bootstrap_iterations, rng
            )
            cliff_delta = _cliffs_delta(p_values, o_values)
            margin = equivalence_margins.get(metric)
            rows.append(
                {
                    "input_volume": volume,
                    "metric": metric,
                    "perspective_n": len(p_values),
                    "orthographic_n": len(o_values),
                    "perspective_mean": p_values.mean() if len(p_values) else np.nan,
                    "orthographic_mean": o_values.mean() if len(o_values) else np.nan,
                    "perspective_median": p_values.median() if len(p_values) else np.nan,
                    "orthographic_median": o_values.median() if len(o_values) else np.nan,
                    "median_diff_p_minus_o": median_diff,
                    "median_diff_ci_low": median_low,
                    "median_diff_ci_high": median_high,
                    "mean_diff_p_minus_o": mean_diff,
                    "mean_diff_ci_low": mean_low,
                    "mean_diff_ci_high": mean_high,
                    "mannwhitney_p": _mann_whitney_pvalue(
                        p_values, o_values, scipy_available, stats_module
                    ),
                    "cliffs_delta": cliff_delta,
                    "cliffs_magnitude": _cliffs_magnitude(cliff_delta),
                    "equivalence_margin": margin,
                    "ci_within_margin": (
                        np.nan
                        if margin is None
                        else bool(median_low >= -margin and median_high <= margin)
                    ),
                }
            )

    return pd.DataFrame(rows).sort_values(["metric", "input_volume"]).reset_index(drop=True)


def _plot_mode_boxplots(frame: pd.DataFrame, save_fig):
    volume_order = sorted(frame["input_volume"].dropna().unique().tolist())
    fig, axes = plt.subplots(1, len(METRIC_SPECS), figsize=(18, 6))
    if len(METRIC_SPECS) == 1:
        axes = [axes]

    for idx, (ax, (metric, ylabel)) in enumerate(zip(axes, METRIC_SPECS)):
        sub = frame[["input_volume", "projection_mode", metric]].dropna().copy()
        sns.boxplot(
            data=sub,
            x="input_volume",
            y=metric,
            hue="projection_mode",
            order=volume_order,
            ax=ax,
            fliersize=0,
        )
        sns.stripplot(
            data=sub,
            x="input_volume",
            y=metric,
            hue="projection_mode",
            order=volume_order,
            dodge=True,
            ax=ax,
            alpha=0.35,
            size=3,
            linewidth=0,
        )
        ax.set_xlabel("Input Volume")
        ax.set_ylabel(ylabel)
        ax.set_title(f"{ylabel} by Volume and Projection Workflow")
        handles, labels = ax.get_legend_handles_labels()
        if idx == 0:
            ax.legend(handles[:2], labels[:2], title="Projection Mode")
        else:
            ax.legend_.remove()

    fig.tight_layout()
    save_fig(fig, "05_projection_workflow_boxplots.png")
    plt.show()


def _plot_difference_forest(
    comparison_summary: pd.DataFrame,
    equivalence_margins: dict[str, float | None],
    save_fig,
):
    fig, axes = plt.subplots(1, len(METRIC_SPECS), figsize=(18, 6))
    if len(METRIC_SPECS) == 1:
        axes = [axes]

    for ax, (metric, ylabel) in zip(axes, METRIC_SPECS):
        sub = comparison_summary[comparison_summary["metric"] == metric].copy()
        if sub.empty:
            ax.text(0.5, 0.5, f"No data for {metric}", ha="center", va="center")
            ax.set_axis_off()
            continue

        sub = sub.sort_values("input_volume").reset_index(drop=True)
        y_pos = np.arange(len(sub))
        x = sub["median_diff_p_minus_o"].to_numpy(dtype=float)
        xerr = np.vstack(
            [
                x - sub["median_diff_ci_low"].to_numpy(dtype=float),
                sub["median_diff_ci_high"].to_numpy(dtype=float) - x,
            ]
        )

        margin = equivalence_margins.get(metric)
        if margin is not None:
            ax.axvspan(-margin, margin, color="tab:green", alpha=0.12)

        ax.errorbar(x, y_pos, xerr=xerr, fmt="o", color="tab:blue", capsize=4)
        ax.axvline(0, color="black", linestyle="--", linewidth=1)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(sub["input_volume"].tolist())
        ax.invert_yaxis()
        ax.set_xlabel("Median difference (Perspective - Orthographic)")
        ax.set_title(f"{ylabel} bootstrap 95% CI")

    fig.tight_layout()
    save_fig(fig, "06_projection_workflow_difference_ci.png")
    plt.show()


def _plot_distance_stratified(frame: pd.DataFrame, save_fig):
    sub = frame[
        frame["distance_group"].notna() & frame["projection_mode"].isin(["Perspective", "Orthographic"])
    ].copy()
    if sub.empty:
        print("Distance-stratified comparison skipped: no usable distance groups.")
        return

    volumes = sorted(sub["input_volume"].dropna().unique().tolist())
    fig, axes = plt.subplots(len(volumes), len(METRIC_SPECS), figsize=(18, 5 * len(volumes)), squeeze=False)

    for row_idx, volume in enumerate(volumes):
        volume_sub = sub[sub["input_volume"] == volume].copy()
        present_groups = [group for group in DISTANCE_ORDER if group in volume_sub["distance_group"].astype(str).unique()]

        for col_idx, (metric, ylabel) in enumerate(METRIC_SPECS):
            ax = axes[row_idx, col_idx]
            metric_sub = volume_sub[["distance_group", "projection_mode", metric]].dropna().copy()
            if metric_sub.empty:
                ax.text(0.5, 0.5, f"No data for {metric}", ha="center", va="center")
                ax.set_axis_off()
                continue

            sns.boxplot(
                data=metric_sub,
                x="distance_group",
                y=metric,
                hue="projection_mode",
                order=present_groups,
                ax=ax,
                fliersize=0,
            )
            ax.set_xlabel("Distance Group")
            ax.set_ylabel(ylabel)
            ax.set_title(f"{volume}: {ylabel} by Distance Group")
            if row_idx == 0 and col_idx == 0:
                ax.legend(title="Projection Mode")
            else:
                ax.legend_.remove()

    fig.tight_layout()
    save_fig(fig, "08_projection_workflow_by_distance.png")
    plt.show()


def run_projection_workflow_analysis(
    df_q: pd.DataFrame,
    save_fig,
    display_fn: Callable | None = None,
    scipy_available: bool = False,
    stats_module=None,
    bootstrap_iterations: int = 4000,
    equivalence_margins: dict[str, float | None] | None = None,
):
    display_fn = display_fn or _default_display
    equivalence_margins = equivalence_margins or {
        "tre_value_mm": None,
        "reprojection_error_px": None,
    }

    frame = df_q[df_q["projection_mode"].isin(["Perspective", "Orthographic"])].copy()
    if frame.empty:
        print("Perspective/Orthographic comparison skipped: no usable rows after filtering.")
        return {
            "matching_diagnostics": pd.DataFrame(),
            "counts_by_group": pd.DataFrame(),
            "mode_summary": pd.DataFrame(),
            "comparison_summary": pd.DataFrame(),
        }

    available_modes = set(frame["projection_mode"].dropna().unique())
    if not {"Perspective", "Orthographic"}.issubset(available_modes):
        print("Perspective/Orthographic comparison skipped: both modes are not present.")
        return {
            "matching_diagnostics": pd.DataFrame(),
            "counts_by_group": pd.DataFrame(),
            "mode_summary": pd.DataFrame(),
            "comparison_summary": pd.DataFrame(),
        }

    print("Projection workflow comparison")
    print("This section is treated as an independent-sample workflow comparison, not a paired-sample ablation.")
    print("Positive Perspective - Orthographic difference means Perspective has larger error.")

    matching_diagnostics = _build_matching_diagnostics(frame)
    print("Exact matching feasibility check:")
    display_fn(matching_diagnostics)

    counts_by_group = frame.groupby(["input_volume", "projection_mode"]).size().unstack(fill_value=0)
    print("Counts by volume and mode:")
    display_fn(counts_by_group)

    mode_summary = _build_mode_summary(frame)
    print("Workflow summary by volume and mode:")
    display_fn(mode_summary.round(4))

    comparison_summary = _build_comparison_summary(
        frame,
        scipy_available=scipy_available,
        stats_module=stats_module,
        bootstrap_iterations=bootstrap_iterations,
        equivalence_margins=equivalence_margins,
    )
    print("Independent-sample comparison summary:")
    display_fn(comparison_summary.round(4))

    _plot_mode_boxplots(frame, save_fig)
    _plot_difference_forest(comparison_summary, equivalence_margins, save_fig)

    if "distance_group" in frame.columns and frame["distance_group"].notna().any():
        _plot_distance_stratified(frame, save_fig)
    else:
        print("Distance-stratified comparison skipped: distance_group is unavailable.")

    return {
        "matching_diagnostics": matching_diagnostics,
        "counts_by_group": counts_by_group,
        "mode_summary": mode_summary,
        "comparison_summary": comparison_summary,
    }
