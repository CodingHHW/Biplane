# Biplane

[中文](#中文) | [English](#english)

---

## 中文

### 简介

`Biplane` 是一个 3D Slicer Scripted Loadable Module，用于基于三视图（Red/Green/Yellow）上的 2D 标注点反推 3D 空间点，并支持将 3D 点投影回第三视图用于校验，同时提供一个简单的“追踪（Tracing）”功能将 3D 点实时映射到三视图。

> 说明：本仓库包含 `Testing/` 下的示例数据（如 `.mrml`/`.nrrd`/`.h5` 等），用于在 Slicer 中快速打开场景进行验证。

### 运行环境

- 3D Slicer（建议使用较新的稳定版本）
- 本模块在 Slicer 内运行，依赖 Slicer 自带的 `vtk` / `numpy` / `SimpleITK` / `OpenCV(cv2)` 等环境

### 安装（开发者方式）

1. 打开 3D Slicer
2. 将本仓库作为扩展模块加载（两种方式任选其一）：
   - 方式 A：把整个文件夹放到 Slicer 的 Additional module paths
   - 方式 B：用 CMake 构建 Slicer 扩展（本仓库带有 `CMakeLists.txt`）

### 使用步骤（典型流程）

1. 打开示例场景：`Testing/BISlicer/2024-02-06-Scene.mrml`（或加载你自己的数据）
2. 在模块 UI 的 **Input volume** 里选择体数据（Volume）
3. 点击 `showVolume` 显示体渲染（可选）
4. 点击 `showMarker` 生成 markers 模型
5. 依次完成三个视角截图并生成切片：
   - Red：`shot1` → `shot1again` → `shot1show`
   - Green：`shot2` → `shot2again` → `shot2show`
   - Yellow：`shot3` → `shot3again` → `shot3show`
6. 点击 `markersSort`：在三个切片视图中生成并编号 markers
7. 在 Red 视图添加一个 2D 点，选择到 `Red 2D Point`，点击 `redPush`（会在 Green 视图生成一条约束线）
8. 在 Green 视图添加一个 2D 点，选择到 `Green 2D Point`，点击 `greenPush`（计算 3D 交点，并在 Yellow 视图显示对应 2D 点）
9. （可选）Tracing：选择 `knife` 点，点击 `tracingPushButton`，移动 3D 点后会在三视图同步显示投影点

### 输出文件位置

截图与中间文件默认输出到：Slicer 的临时目录下 `Biplane/`（例如 macOS 通常在 `~/Library/.../Slicer-*/` 的 temporary path 下）。

---

## English

### Overview

`Biplane` is a 3D Slicer Scripted Loadable Module that reconstructs a 3D target point from 2D markups across multiple slice views (Red/Green/Yellow). It also projects the computed 3D point back onto the third view for validation, and provides a simple “Tracing” feature to continuously map a moving 3D point onto the slice views.

> Note: Sample data is included under `Testing/` (e.g., `.mrml`/`.nrrd`/`.h5`) for quick validation in Slicer.

### Requirements

- 3D Slicer (a recent stable version is recommended)
- Runs inside Slicer and relies on Slicer-provided Python environment (`vtk`, `numpy`, `SimpleITK`, `OpenCV(cv2)`, etc.)

### Install (Developer Workflow)

1. Open 3D Slicer
2. Load this repository as a scripted module (either option works):
   - Option A: Add the repo folder to Slicer “Additional module paths”
   - Option B: Build as a Slicer extension via CMake (this repo includes `CMakeLists.txt`)

### Typical Workflow

1. Open the sample scene: `Testing/BISlicer/2024-02-06-Scene.mrml` (or load your own data)
2. Select the input volume in **Input volume**
3. Click `showVolume` to enable volume rendering (optional)
4. Click `showMarker` to create the markers model
5. For each view, capture body/markers and generate a slice volume:
   - Red: `shot1` → `shot1again` → `shot1show`
   - Green: `shot2` → `shot2again` → `shot2show`
   - Yellow: `shot3` → `shot3again` → `shot3show`
6. Click `markersSort` to detect/sort markers and display them on each slice view
7. Add one 2D point in Red view, select it in `Red 2D Point`, click `redPush` (creates a constraint line in Green view)
8. Add one 2D point in Green view, select it in `Green 2D Point`, click `greenPush` (computes the 3D intersection and shows the corresponding 2D point in Yellow)
9. (Optional) Tracing: select a `knife` markup point, click `tracingPushButton`, then move the 3D point to see synchronized projections

### Output Location

Screenshots and intermediate outputs are saved under `Biplane/` inside Slicer’s temporary path.
