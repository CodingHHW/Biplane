# Controlled Perturbation Experiment Plan

更新日期：2026-04-21

## 1. 实验目的

本实验用于补充 2D 点扰动对当前双平面几何恢复流程的影响分析，重点回答以下问题：

- 在受控 2D 噪声下，`TRE`、`RE`、`ray gap` 如何随噪声水平变化。
- 不同视角分离条件下，噪声传播是否存在明显差异。
- `marker-only` 噪声与 `target-only` 噪声对误差指标的影响是否不同。

本实验定位为：

- 代表性 `controlled perturbation study`
- 噪声传播分析
- 对主实验结果的补充验证，而不是新的大规模主实验

---

## 2. 最终实验设计

### 2.1 固定条件

- 数据集：`CTChest`
- 投影模式：`Perspective`

### 2.2 视角分离分组

保留三组视角分离条件：

- `Low`
- `Medium`
- `High`

### 2.3 点位选择

每个视角组选择 3 个代表性点，覆盖：

- `Near`
- `Mid`
- `Far`

因此总共使用：

- `3 angle groups × 3 points = 9` 个 `base conditions`

每个 `base condition` 应固定以下内容：

- 相同的 volume
- 相同的 3 个 transform
- 相同的 test point
- 相同的投影模式（`Perspective`）

---

## 3. 扰动设置

### 3.1 噪声类型

保留两类扰动：

- `marker-only`
- `target-only`

### 3.2 噪声水平

采用以下 4 档噪声水平：

- `0 px`
- `0.5 px`
- `1 px`
- `2 px`

其中：

- `0 px` 作为无噪声基线
- `0.5 / 1 / 2 px` 作为正式扰动档位

### 3.3 噪声模型

推荐使用二维各向同性高斯噪声：

- 对每个 2D 点的 `u`、`v` 坐标分别加入独立噪声
- `delta_u, delta_v ~ N(0, sigma^2)`

这里的 `sigma` 取值为：

- `0`
- `0.5`
- `1`
- `2`

---

## 4. 扰动注入规则

### 4.1 marker-only

在 `marker-only` 条件下：

- 仅对用于标定的 marker 2D 点加入噪声
- target 2D 点保持干净
- 重新执行标定与三维恢复
- 计算新的 `TRE`、`RE`、`ray gap`

目的：

- 观察标定误差传播对恢复结果的影响

### 4.2 target-only

在 `target-only` 条件下：

- marker 2D 点保持干净
- 仅对用于三维恢复的 target 2D 点加入噪声
- 建议对 `view1` 和 `view2` 的 target 点分别独立加入噪声
- 使用干净标定结果进行三维恢复
- 计算新的 `TRE`、`RE`、`ray gap`

目的：

- 观察目标点定位误差对恢复结果的影响

### 4.3 基线

在 `sigma = 0 px` 条件下：

- 每个 `base condition` 仅记录 1 条 clean baseline
- 不需要重复区分 `marker-only` 和 `target-only`

---

## 5. 重复次数

对每个非零噪声档位：

- 每个条件重复 `20` 次

即：

- `sigma = 0.5 px`：20 次
- `sigma = 1 px`：20 次
- `sigma = 2 px`：20 次

`sigma = 0 px`：

- 每个 `base condition` 记录 1 次基线

---

## 6. 实验总记录数

### 6.1 非零噪声部分

总记录数为：

`3 angle groups × 3 points × 2 noise types × 3 nonzero sigma levels × 20 repeats`

即：

`3 × 3 × 2 × 3 × 20 = 1080`

### 6.2 基线部分

每个 `base condition` 记录 1 条无噪声基线：

- `3 angle groups × 3 points = 9`

### 6.3 总计

推荐总记录数：

- `1080 + 9 = 1089`

说明：

- 这里默认 `sigma = 0` 不按 `marker-only` 和 `target-only` 重复记两遍
- 如果后续实现中把 `sigma = 0` 也拆成两类记录，则总数会变成 `1098`
- 当前推荐使用 `1089` 条作为正式实验规模

---

## 7. 每条数据建议记录的字段

建议为本实验新增或明确保存以下字段：

- `experiment_family`
- `base_condition_id`
- `dataset`
- `projection_mode`
- `angle_group`
- `distance_group`
- `point_id`
- `noise_type`
- `noise_sigma_px`
- `repeat_index`
- `tre_mm_raw`
- `re_px_raw`
- `ray_gap_mm_raw`
- `success_flag`
- `failure_reason`

可选补充字段：

- `shot2_angle_deg`
- `shot3_angle_m3_m1_deg`
- `shot3_angle_m3_m2_deg`
- `testpoint_marker_distance_mean_mm`
- `marker_sort_rms`
- `calibration_reproj_rms`

---

## 8. 数据记录要求

本实验前必须满足以下要求：

### 8.1 保存原始浮点值

`TRE`、`RE`、`ray gap` 必须保存原始浮点值，不应从 UI 的两位小数字符串回填 CSV。

原因：

- 小噪声下的 `ray gap` 可能远小于 `0.01 mm`
- 如果只保留 UI 文本，很多值会被截断为 `0.00`
- 会直接削弱该实验的解释力

### 8.2 噪声应在已匹配点坐标后注入

本实验应聚焦于：

- 标定误差传播
- 目标点定位误差传播

因此建议：

- 在 marker 排序和 target 对应关系确定之后注入噪声
- 不把 marker 排序错误混入本实验

如果后续需要，可以另做一个 end-to-end 鲁棒性实验。

---

## 9. 每个 base condition 的执行流程

对每个 `base condition`：

1. 回放到干净的几何条件
2. 记录 `sigma = 0` 的 baseline
3. 对 `marker-only` 运行：
   - `0.5 px × 20`
   - `1 px × 20`
   - `2 px × 20`
4. 对 `target-only` 运行：
   - `0.5 px × 20`
   - `1 px × 20`
   - `2 px × 20`
5. 保存所有运行结果

单个 `base condition` 的记录数为：

- `1 + (2 × 3 × 20) = 121`

全部 9 个 `base conditions` 的记录数为：

- `9 × 121 = 1089`

---

## 10. 结果汇总建议

### 10.1 主汇总方式

建议按 `base condition` 先汇总，再跨条件比较，避免把所有重复运行都当成完全独立样本。

每个 `base condition × noise_type × sigma` 建议汇总：

- median
- IQR
- success rate

### 10.2 主比较维度

建议重点比较：

- `Low / Medium / High angle`
- `marker-only / target-only`
- `0 / 0.5 / 1 / 2 px`

### 10.3 主图建议

建议至少输出以下图：

- `TRE vs noise sigma`
- `RE vs noise sigma`
- `ray gap vs noise sigma`

推荐展示方式：

- 横轴：噪声水平
- 颜色：视角组
- 分面：噪声类型

---

## 11. 论文中建议的结果表述

该实验更适合支撑如下表述：

- 在代表性的 `CTChest`-`Perspective` 条件下，`TRE`、`RE` 和 `ray gap` 随 2D 点扰动增大而上升。
- 低视角分离条件对 2D 点扰动更敏感，高视角分离条件更稳健。
- `marker-only` 与 `target-only` 扰动对误差传播的影响模式不同。

不建议过度表述为：

- 全面鲁棒性验证
- 所有数据场景下的一般性结论

更稳妥的定位是：

- representative controlled perturbation analysis
- noise propagation study under representative geometry

---

## 12. 当前推荐的最终实验规模

当前正式执行版本如下：

- 数据集：`CTChest`
- 模式：`Perspective`
- 视角组：`Low + Medium + High`
- 点位：每组 `Near + Mid + Far`
- 噪声类型：`marker-only + target-only`
- 噪声水平：`0, 0.5, 1, 2 px`
- 非零噪声重复次数：每档 `20` 次
- 推荐总记录数：`1089`

这是一套在当前论文阶段：

- 工作量可控
- 结构完整
- 可以写入论文
- 能支撑视角几何与噪声传播关系分析

的最小正式方案。
