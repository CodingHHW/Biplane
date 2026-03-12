# Biplane

A 3D Slicer scripted module for biplane-guided 2D-3D reconstruction, calibration, projection, and simulation-based error analysis.

[中文](#中文) | [English](#english)

---

## 中文

### 简介

**Biplane** 是一个 3D Slicer 的 Scripted Loadable Module，用于多视角投影条件下的 2D-3D 重建与验证。模块通过 marker 标定恢复投影几何，再从 2D 点恢复 3D 目标点，并将其重新投影到验证视图中评估误差。

这个仓库除了 Slicer 模块本体，也包含一套实验记录和结果分析流程，用于比较不同投影模型下的误差表现。

### 核心功能

1. 2D -> 3D 重建
   - 在 Red 视图选点后生成 Green 约束线
   - 在 Green 视图确认后重建 `TargetP3D`
2. 3D -> 2D 验证
   - 将 `TargetP3D` 投影到 Yellow 视图，生成 `TargetP2DYellow`
3. 实时追踪
   - 拖动 3D `knife` 点时，同步更新多个 2D 视图
4. 误差评估
   - TRE (`mm`)
   - Reprojection Error (`px`)
   - Ray Gap (`mm`)
5. 实验记录
   - 将当前实验状态、投影模式、角度、误差、选点信息和调试参数追加保存到 CSV

### 当前投影模式

- `Perspective`
  - 默认模式
  - 更接近真实 C-arm 成像模型
  - 研究和实际应用中的主目标模型
- `Orthographic`
  - 只在仿真中可用
  - 作为理想化投影近似/对照组使用
  - 主要用于做 sensitivity / approximation study，而不是替代真实成像模型

### 运行环境

- 3D Slicer 5.2+
- Slicer Python 环境
  - `vtk`
  - `numpy`
  - `SimpleITK`
  - `opencv-python`（缺失时模块可尝试自动安装）

### 安装方式

#### 方式 A：直接加载源码目录

1. 打开 3D Slicer
2. 进入 `Edit -> Application Settings -> Modules -> Additional module paths`
3. 添加本仓库根目录
4. 重启 Slicer

#### 方式 B：作为 Slicer Extension 构建

仓库包含 `CMakeLists.txt`，可按标准 Slicer Extension 流程构建。

### 项目结构

```text
Biplane/
|-- Biplane.py
|-- BiplaneLogics.py
|-- CMakeLists.txt
|-- README.md
|-- Resources/
|   |-- Icons/Biplane.png
|   `-- UI/Biplane.ui
|-- Testing/
|   |-- BIPLANE/
|   `-- BISlicer/
|-- experiment/
|   `-- experiment_results.csv
`-- Analysis/
    |-- experiment_results_analysis.ipynb
    |-- projection_workflow_analysis.py
    `-- Analysis/
        |-- experiment_results_cleaned.csv
        |-- analysis_summary.csv
        `-- figures/
```

### UI 快速流程

1. 载入体数据并选择 `Input volume`
2. 依次执行 `showVolume`、`showMarker`、`showTestPoint`（后两者更关键）
3. 配置 `Markers1 / Markers2 / Markers3` 对应的 transform
4. 采集 `shot1`、`shot2`、`shot3`
5. 执行 `markersSort`
6. 在 Red 视图选点后点击 `redPush`
7. 在 Green 视图确认点后点击 `greenPush`
8. 查看 3D 结果 `TargetP3D`
9. 查看 Yellow 视图中的验证点 `TargetP2DYellow`
10. 可选：使用 `showKnife` + `tracing`
11. 可选：计算 TRE / Reprojection Error
12. 点击 `Save Current Results to CSV`

### 默认输出

默认输出目录通常位于：

- `<Slicer temporaryPath>/Biplane/`

每个视角通常会生成：

- `shot1Body.png`, `shot2Body.png`, `shot3Body.png`
- `shot1Markers.png`, `shot2Markers.png`, `shot3Markers.png`
- `shot1TestPoint.png`, `shot2TestPoint.png`, `shot3TestPoint.png`
- `shot1.nii.gz`, `shot2.nii.gz`, `shot3.nii.gz`

实验日志通常记录到：

- `experiment_results.csv`

### CSV 记录内容

CSV 会追加记录以下信息：

- 基础信息：时间戳、输入体数据、投影模式、模式状态
- 标定状态：`markers_sorted`、Perspective / Orthographic calibration 是否完成
- shot 可用性：`shot1_available`、`shot2_available`、`shot3_available`
- 角度信息：`shot2_angle_deg`、`shot3_angle_m3_m1_deg`、`shot3_angle_m3_m2_deg`
- 指标：`tre_value_mm`、`reprojection_error_px`、`ray_gap_mm`
- 选点器状态：多个 2D / 3D selector 与 knife selector
- `testpoint` 三维坐标与 marker 距离
- 调试参数：`debug_visualization`、`debug_plane_scale`、`debug_ray_scale`

---

## Analysis

### 分析入口

主入口：

- [experiment_results_analysis.ipynb](Analysis/experiment_results_analysis.ipynb)

Figure 4 的核心逻辑已经抽到独立模块：

- [projection_workflow_analysis.py](Analysis/projection_workflow_analysis.py)

这样做的目的有两个：

1. 不再把大段统计逻辑硬编码在 notebook JSON 里
2. 以后如果要继续调整统计口径、等效性阈值或图形样式，只需要改 Python 模块

### 分析产物

Notebook 会清洗 CSV 并输出：

- `experiment_results_cleaned.csv`
- `analysis_summary.csv`
- 多张图像到 `Analysis/Analysis/figures/`

Figure 4 相关的新图包括：

- `05_projection_workflow_boxplots.png`
- `06_projection_workflow_difference_ci.png`
- `08_projection_workflow_by_distance.png`

### Perspective vs Orthographic 这一节现在怎么定义

这一节不再定义为“严格公平的一对一 paired comparison”，而是定义为：

- `Perspective vs Orthographic workflow-level sensitivity comparison`

原因如下：

1. `Perspective` 才是更接近真实 C-arm 的物理成像模型
2. `Orthographic` 只是仿真中可用的理想化近似模型
3. 当前实验设计下，两种模式的 `testpoint` 无法一一重合
4. 当前实验设计下，两种模式的拍摄角度也无法一一重合
5. 因此无法做真正有意义的 paired-sample test

这意味着 Figure 4 现在回答的问题是：

- 当真实投影模型 `Perspective` 与理想化近似 `Orthographic` 分别作为完整 workflow 运行时，误差分布是否发生明显变化？
- 你的方法对投影模型假设是否敏感？

而不是：

- 在完全相同几何条件下，单独隔离出的 “projection mode causal effect” 是多少？

### Figure 4 的当前实现

Figure 4 现在会执行以下步骤：

1. 先做 exact matching feasibility check
   - 检查是否存在可用于 paired comparison 的样本
   - 只做诊断，不再强行进行 paired test
2. 输出每个 `input_volume × projection_mode` 的样本数与描述统计
   - mean
   - median
   - IQR
3. 对 `TRE` 和 `RE` 分别做独立样本比较
   - median difference (`Perspective - Orthographic`)
   - mean difference
   - bootstrap 95% CI
   - Mann-Whitney U p-value
   - Cliff's delta effect size
4. 生成 3 张图
   - workflow boxplot
   - 差值置信区间图
   - 按距离分层的比较图

### 等效性阈值配置

Figure 4 单元里保留了：

```python
projection_equivalence_margins = {
    'tre_value_mm': None,
    'reprojection_error_px': None,
}
```

当前默认不做 practical equivalence 判定。

如果后续你想在工程意义上判断“两种模型是否足够接近”，可以把 `None` 改成阈值，例如：

```python
projection_equivalence_margins = {
    'tre_value_mm': 0.05,
    'reprojection_error_px': 0.05,
}
```

然后 Figure 4 会额外报告 bootstrap 中位数差的 95% CI 是否完全落在这个容忍区间内。

### 当前结论的解释边界

这一节现在只能被解释为：

- independent-sample workflow comparison
- projection-model sensitivity analysis
- approximation study

不能被解释为：

- 严格配对消融实验
- 纯粹剥离掉 `testpoint` 和 `angle` 影响后的因果效应

因为在当前数据中，`projection_mode`、`testpoint`、`angle` 是一起变化的。

### 当前数据快照

以下结果基于当前仓库中的：

- `experiment/experiment_results.csv`

并以 `2026-03-12` 时的分析代码为准。

#### 1. 配对可行性诊断

| Scheme | Meaning | Paired rows |
|---|---|---:|
| `full_key_r2` | `input_volume + testpoint_xyz + angle trio`，数值四舍五入到 2 位 | 0 |
| `volume_coords_r2` | `input_volume + testpoint_xyz`，四舍五入到 2 位 | 0 |
| `volume_coords_r0` | `input_volume + testpoint_xyz`，四舍五入到整数 | 1 |
| `volume_angles_r2` | `input_volume + angle trio`，四舍五入到 2 位 | 0 |

结论：

- 当前数据不支持稳定的 paired comparison
- 即便极度放宽，只能凑出 1 对样本，不具备统计意义

#### 2. 当前 workflow-level 结果摘要

| Volume | Metric | Perspective (n, mean / median) | Orthographic (n, mean / median) | Median diff `P - O` | 95% CI | Mann-Whitney p | Cliff's delta |
|---|---|---|---|---:|---|---:|---:|
| `CTChest` | TRE | 71, `0.2215 / 0.180` | 74, `0.1941 / 0.150` | `+0.030` | `[-0.030, 0.100]` | `0.6083` | `+0.0495` |
| `CTChest` | RE | 71, `0.1637 / 0.120` | 74, `0.2614 / 0.190` | `-0.070` | `[-0.130, -0.005]` | `0.0068` | `-0.2604` |
| `Panoramix-cropped` | TRE | 70, `0.1590 / 0.125` | 69, `0.1239 / 0.100` | `+0.025` | `[-0.010, 0.060]` | `0.0892` | `+0.1671` |
| `Panoramix-cropped` | RE | 70, `0.1349 / 0.105` | 69, `0.1828 / 0.130` | `-0.025` | `[-0.070, 0.020]` | `0.0705` | `-0.1778` |

解释规则：

- `Median diff (P - O) > 0`：Perspective 误差更大
- `Median diff (P - O) < 0`：Perspective 误差更小

#### 3. 当前可读结论

从当前数据看，趋势比较稳定：

- `Orthographic` 在两个 volume 上都表现出略低的 `TRE`
- `Perspective` 在两个 volume 上都表现出略低的 `RE`
- 两种模型更像是在不同指标上有轻微 trade-off，而不是某一种全面更优

更具体地说：

- `CTChest`
  - TRE 差异不明显
  - RE 上 Perspective 更低，且统计证据相对更强
- `Panoramix-cropped`
  - TRE 仍然是 Orthographic 略低
  - RE 仍然是 Perspective 略低
  - 但两项都没有达到很强的显著性证据

#### 4. 目前最稳妥的总体结论

当前最稳妥的写法是：

- 在本仿真设置下，`Orthographic` 作为理想化投影近似，并未导致明显的整体性能崩溃
- `Perspective` 与 `Orthographic` 的误差水平总体接近
- 方法对投影模型变化表现出一定鲁棒性
- 但该结论应理解为 workflow-level sensitivity result，而不是严格的纯 mode causal comparison

### 为什么 README 要把这些写下来

这部分结论容易随着时间遗忘，但后续写论文、答辩、补实验时会反复用到。README 里保留这些内容，主要是为了固定住以下事实：

1. 为什么 Figure 4 不能再做 paired scatter / delta histogram
2. 为什么新的分析方法是合理的
3. 当前数据到底支持了什么结论，不支持什么结论
4. 以后如果重新跑实验，应该优先关注哪些图和统计表

---

## English

### Overview

**Biplane** is a 3D Slicer scripted module for marker-based biplane 2D-3D reconstruction, validation, and experiment logging.

Besides the Slicer module itself, this repository includes an analysis workflow for comparing projection models under simulation.

### Projection Models

- `Perspective`
  - The physically realistic model for C-arm style imaging
  - The default and target deployment model
- `Orthographic`
  - A simulation-only idealized approximation
  - Used as a reference / sensitivity baseline rather than a deployment model

### Main Analysis Entry Points

- [experiment_results_analysis.ipynb](Analysis/experiment_results_analysis.ipynb)
- [projection_workflow_analysis.py](Analysis/projection_workflow_analysis.py)

### Updated Perspective vs Orthographic Analysis

The repository no longer treats this section as a paired-sample ablation.

It is now explicitly implemented as a:

- workflow-level independent-sample comparison
- projection-model sensitivity analysis
- approximation study

This change was necessary because, in the current experiment design:

- test points are not exactly matched across `Perspective` and `Orthographic`
- acquisition angles are also not matched across the two modes

Therefore, a strict paired comparison is not statistically valid.

### What Figure 4 Now Does

Figure 4 now performs:

1. exact matching feasibility diagnostics
2. per-volume descriptive summaries
3. independent-sample comparisons for `TRE` and `RE`
4. bootstrap 95% confidence intervals
5. Mann-Whitney U tests
6. Cliff's delta effect sizes
7. distance-stratified comparison plots

Generated figures:

- `05_projection_workflow_boxplots.png`
- `06_projection_workflow_difference_ci.png`
- `08_projection_workflow_by_distance.png`

### Current Snapshot

Based on the current `experiment/experiment_results.csv` and the analysis state checked on **March 12, 2026**:

- exact matching is effectively unavailable
- `Orthographic` shows slightly lower `TRE` on both tested volumes
- `Perspective` shows slightly lower `RE` on both tested volumes
- the overall interpretation is that the method is relatively robust to the projection-model change, but this remains a workflow-level result rather than a pure isolated mode effect

---

If this module is used in academic work, please cite your paper or project accordingly.
