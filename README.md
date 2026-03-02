# Biplane

A 3D Slicer scripted module for biplane-guided 2D-3D reconstruction.

[中文](#中文) | [English](#english)

---

## 中文

### 简介

**Biplane** 是一个 3D Slicer 的 Scripted Loadable Module，用于在多视角投影下进行 2D-3D 重建与验证。模块通过两层标记球（Big/Small）完成标定，并支持从 Red/Green 视图重建 3D 目标点，再投影到 Yellow 视图做验证。

### 核心功能

1. **2D -> 3D 重建**
   - 在 Red 视图点击目标点后生成 Green 约束线。
   - 在 Green 视图沿约束线点击目标点后重建 `TargetP3D`。
2. **3D -> 2D 验证**
   - 将 `TargetP3D` 投影到 Yellow 视图，生成 `TargetP2DYellow`。
3. **实时追踪（Tracing）**
   - 拖动 3D `knife` 点，实时同步到 Red/Green/Yellow 三个 2D 视图。
4. **误差评估**
   - TRE（mm）、Reprojection Error（px）、Ray Gap（mm）。
5. **实验结果记录**
   - 一键追加保存当前状态到 CSV（包含模式、角度、误差、节点选择、调试参数等）。

### 当前实现的投影模式

- **Perspective（默认）**
  - 通过 `solvePnP` 估计相机位姿。
  - 用相机模型进行像素反投影与 3D 点投影。
- **Orthographic**
  - 拟合 `2x4` 线性投影矩阵。
  - 使用正交投影模型进行反投影和投影。

### 运行环境

- 3D Slicer（建议 5.2+）
- Slicer Python 环境：
  - `vtk`
  - `numpy`
  - `SimpleITK`
  - `opencv-python`（代码会在缺失时自动安装）

### 安装方式

#### 方式 A：直接加载源码目录（推荐开发）

1. 打开 3D Slicer
2. `Edit -> Application Settings -> Modules -> Additional module paths`
3. 添加本仓库目录
4. 重启 Slicer

#### 方式 B：作为 Slicer Extension 构建

仓库已包含 `CMakeLists.txt`，可按标准 Slicer Extension 方式构建。

### 项目结构

```text
Biplane/
├── Biplane.py                    # 主模块：UI、拍摄、标定、重建、追踪、CSV 导出
├── BiplaneLogics.py              # 几何与投影逻辑（含隐藏兼容模块壳）
├── CMakeLists.txt
├── README.md
├── Resources/
│   ├── Icons/Biplane.png
│   └── UI/Biplane.ui
├── Testing/
│   ├── BIPLANE/                  # 示例 shot 数据
│   └── BISlicer/                 # 示例场景/数据
└── Analysis/
    ├── experiment_results_analysis.ipynb
    └── Analysis/
        ├── experiment_results_cleaned.csv
        ├── analysis_summary.csv
        └── figures/
```

### UI 快速流程（与按钮命名一致）

1. 载入体数据，并在 `Input volume` 选择它。
2. 点击 `showVolume`（可选）。
3. 点击 `showMarker`（必须）。
4. 点击 `showTestPoint`（建议）。
5. 点击 `Markers1 / Markers2 / Markers3`，分别配合 `Transforms` 调整：
   - `LinearTransform`
   - `LinearTransform_1`
   - `LinearTransform_2`
6. 依次点击 `shot1`、`shot2`、`shot3`（对应 `shot1AllButton/shot2AllButton/shot3AllButton`）。
7. 可选点击 `blackCenter` 生成 `blackCenter1/2/3`。
8. 点击 `markersSort` 完成 marker 检测、编号和标定。
9. Red 视图选择 `PointRed`（或自建点）后点击 `redPush`。
10. Green 视图选择 `PointGreen`（或自建点）后点击 `greenPush`。
11. 查看重建结果：
    - 3D 视图：`TargetP3D`
    - Yellow 视图：`TargetP2DYellow`
12. 可选：点击 `showKnife` 后点击 `tracing` 启用实时追踪。
13. 可选：点击 `Calculate TRE` / `Calculate Reprojection Error`。
14. 设置 `CSV Path`，点击 `Save Current Results to CSV`。

### 输出文件

默认输出目录：`<Slicer temporaryPath>/Biplane/`

每个视角会生成：

- `shot{1|2|3}Body.png`
- `shot{1|2|3}Markers.png`
- `shot{1|2|3}TestPoint.png`
- `shot{1|2|3}.nii.gz`

合成切片中采用约定像素值：

- marker 区域：`-1000`
- testPoint 区域：`-100`

### CSV 结果记录（`Save Current Results to CSV`）

CSV 会追加记录以下信息（代码字段）：

- 基础信息：时间戳、输入体数据、投影模式、模式状态
- 标定状态：`markers_sorted`、perspective/orthographic 是否完成
- shot 可用性：`shot1_available/shot2_available/shot3_available`
- 角度：`shot2_angle_deg`、`shot3_angle_m3_m1_deg`、`shot3_angle_m3_m2_deg`
- 指标：`tre_value_mm`、`reprojection_error_px`、`ray_gap_mm`
- UI 选择节点：点选择器、2D 点选择器、knife 选择器
- `testPoint` 三维坐标及到三个 marker 分布中心的距离
- 调试参数：`debug_visualization`、`debug_plane_scale`、`debug_ray_scale`

### Marker 与标定要点

- 2D 分割阈值（SimpleITK）：`[-1050, -950]`
- 至少应检测到 10 个连通域（Big 5 + Small 5）
- 编号策略结合：
  - 单应重投影误差
  - PnP 重投影误差
  - 坐标翻转尝试
- 标定质量门限：Perspective / Orthographic 的 RMS 默认要求 `<= 5.0 px`

### Debug 可视化

UI 提供：

- `debugVisCheckBox`
- `debugPlaneScaleSpinBox`
- `debugRayScaleSpinBox`

开启后会生成辅助节点（例如 `vis_RedRay`、`vis_GreenRay`、`vis_TargetP3DMidpoint`、各视角大/小平面等）以便排查几何关系。

### Analysis 结果分析

分析脚本：`Analysis/experiment_results_analysis.ipynb`

主要用途：

- 读取 `experiment_results.csv`
- 清洗数据并输出 `experiment_results_cleaned.csv`
- 输出 `analysis_summary.csv`
- 生成统计图到 `Analysis/Analysis/figures/`

> 说明：Notebook 中包含 `manual_csv_path` 变量，使用前建议改为你的 CSV 路径，或关闭手动路径让脚本自动查找。

### 测试现状

- 根 `CMakeLists.txt` 启用了 `WITH_GENERIC_TESTS`
- `Testing/Python/CMakeLists.txt` 中独立 Python 测试脚本目前仍为注释状态

---

## English

### Overview

**Biplane** is a 3D Slicer scripted module for biplane-guided 2D-3D reconstruction.
It uses a dual-layer marker setup for calibration, reconstructs a 3D target from Red/Green 2D annotations, and validates the result in Yellow view.

### Features

1. 2D-to-3D reconstruction (`redPush` + `greenPush`)
2. 3D-to-2D validation (`TargetP2DYellow`)
3. Real-time tracing from a 3D `knife` point
4. TRE / Reprojection Error / Ray Gap evaluation
5. One-click CSV logging of experiment status and metrics

### Requirements

- 3D Slicer 5.2+
- Slicer Python packages: `vtk`, `numpy`, `SimpleITK`, `opencv-python`

### Install

1. Open 3D Slicer
2. Add this repository path to:
   `Edit -> Application Settings -> Modules -> Additional module paths`
3. Restart Slicer

### Quick Start

1. Select `Input volume`
2. Click `showMarker` (required), optionally `showVolume` / `showTestPoint`
3. Adjust marker poses via `Markers1/2/3` + three transform nodes
4. Capture `shot1`, `shot2`, `shot3`
5. Click `markersSort`
6. Annotate in Red and click `redPush`
7. Annotate in Green and click `greenPush`
8. Inspect `TargetP3D` and `TargetP2DYellow`
9. (Optional) `showKnife` + `tracing`
10. Save metrics via `Save Current Results to CSV`

### Outputs

Default output directory: `<Slicer temporaryPath>/Biplane/`

- `shot1/2/3Body.png`
- `shot1/2/3Markers.png`
- `shot1/2/3TestPoint.png`
- `shot1/2/3.nii.gz`
- `experiment_results.csv`

### Analysis

Use `Analysis/experiment_results_analysis.ipynb` to clean CSV data, generate summaries, and export figures under `Analysis/Analysis/figures/`.

---

If this module is used in academic work, please cite your paper/project accordingly.
