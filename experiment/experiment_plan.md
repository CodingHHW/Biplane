# Biplane 实验方案

更新日期：2026-03-16

## 1. 目标

当前项目的实验目标，不应该再是“继续随机多采一些点”，而应该转为“控制变量下的可复现实验”。  
现在系统已经具备以下能力：

- 记录完整的 `experiment_results.csv`
- 为每条记录保存 3 个 marker 的 transform 快照
- 通过导入 CSV 某一行，恢复该行的 `testPoint`
- 通过导入 CSV 某一行，恢复该行的 3 个 transform 快照

这意味着现在已经可以做严格得多的配对实验。

---

## 2. 当前最优先要回答的问题

论文阶段最应该优先回答的是这 4 个问题：

1. 在完全相同的 volume、相同的 3 个 transform、相同的 testPoint 下，`Perspective` 与 `Orthographic` 的误差差异到底有多大？
2. 角度配置变化是否会显著影响 `TRE` 和 `RE`？
3. testPoint 到 marker 的距离是否会系统性影响误差？
4. 同一条件下重复采集时，结果是否稳定？

---

## 3. 当前建议的实验优先级

### 第一优先级：严格配对的 Projection Mode 对比

这是现在最重要的实验。

目的：

- 得到真正的 `Perspective vs Orthographic` 配对比较
- 避免过去那种“不同点、不同 transform、不同角度”混在一起的 workflow-level comparison

现在已经可行的做法：

1. 选择一个 volume
2. 手动设置 3 个 transform
3. 手动设置一个 testPoint
4. 在模式 A 下完整跑一遍并保存 CSV
5. 切换到模式 B
6. 导入刚才那条 CSV 记录，恢复同一个 `testPoint` 和同一组 3 个 transform
7. 在模式 B 下重新拍摄、重新排序 marker、重新标定、重新计算误差
8. 再保存一条新记录

这样得到的两条记录，就构成一个真正的几何配对样本。

### 第二优先级：角度分层实验

目的：

- 看 `shot2_angle_deg`
- 看 `shot3_angle_m3_m1_deg`
- 看 `shot3_angle_m3_m2_deg`
- 看不同 baseline 下误差是否变化

建议每个 volume 至少做 3 组角度配置：

- 小角度组
- 中角度组
- 大角度组

不要求死卡某个绝对角度值，但每一组内部要相对稳定，并在 CSV 中保留真实角度用于后续分析。

### 第三优先级：距离分层实验

目的：

- 分析 `testpoint_marker_distance_mean_mm` 与误差的关系
- 避免距离混杂掩盖 projection mode 或角度效应

建议将点分成 3 个距离层：

- Near
- Mid
- Far

更稳妥的做法不是先拍很多随机点再事后分层，而是先做一个 pilot，用少量点估计每个 volume 的距离分位数，再按分层去采样。

### 第四优先级：重复性实验

目的：

- 估计同一条件下的人为操作方差
- 区分“方法本身误差”和“操作波动”

建议：

- 选择一小批代表性条件
- 同一条件重复 5 到 10 次
- 统计 `TRE`、`RE` 的均值、标准差和组内变异

---

## 4. 推荐的正式实验矩阵

### 4.1 最小可执行版

适合当前论文推进，性价比最高。

- Volume：2 个
  - `CTChest`
  - `Panoramix-cropped`
- Angle group：3 组
  - Small
  - Medium
  - Large
- Distance group：3 组
  - Near
  - Mid
  - Far
- 每个 `volume × angle group × distance group`：8 个 testPoint
- Projection mode：2 个
  - `Perspective`
  - `Orthographic`
- 要求：每个点都必须是严格配对采集

总记录数：

- `2 × 3 × 3 × 8 × 2 = 288` 条

如果每组做到 10 个点，则总记录数为：

- `2 × 3 × 3 × 10 × 2 = 360` 条

### 4.2 推荐扩展版

在最小可执行版完成后再做。

- 重复性子实验：
  - 选择 10 个代表性点
  - 每个点重复 5 次
  - 两种 mode 都做
- 目的：
  - 估计操作重复性
  - 作为论文中“稳定性”部分

---

## 5. 具体采集流程

### 5.1 每个 volume 的准备阶段

1. 载入 volume
2. 确认渲染显示设置固定
3. 确认 marker 模型可见
4. 选定一组角度配置，对应 3 个 transform
5. 在这一组 transform 固定不变的前提下，开始采集 testPoint

### 5.2 每个 angle group 的采集规则

对同一个 `volume + angle group`：

- transform 固定
- 只改变 testPoint
- testPoint 按 Near / Mid / Far 三层采样

这样角度效应与距离效应就不会互相严重混杂。

### 5.3 每个配对样本的采集流程

建议固定采集顺序，避免混乱。

推荐顺序：

1. 设定 volume
2. 设定 3 个 transform
3. 设定 testPoint
4. 在 `Perspective` 下完成：
   - shot1 / shot2 / shot3
   - blackCenter
   - markersSort
   - 标定
   - 重建
   - 误差计算
   - 保存 CSV
5. 切换到 `Orthographic`
6. 导入上一步那条 CSV 记录
7. 自动恢复该行的：
   - testPoint
   - `LinearTransform`
   - `LinearTransform_1`
   - `LinearTransform_2`
8. 在 `Orthographic` 下重新完整跑一次
9. 保存 CSV

也可以反过来先做 `Orthographic` 再做 `Perspective`，但一个实验批次内建议固定顺序。

### 5.4 每个批次采完后应检查

- 当前 CSV 是否成功新增 2 条配对记录
- 两条记录的：
  - `input_volume` 一致
  - `testpoint_x/y/z` 一致
  - 角度字段基本一致
  - transform 快照路径存在

---

## 6. 距离分层建议

建议先做一个小型 pilot，再正式开采。

### 6.1 Pilot

每个 volume 随机做 15 到 20 个点，记录：

- `testpoint_marker_distance_mean_mm`
- `TRE`
- `RE`

### 6.2 用 pilot 结果确定距离层

推荐按每个 volume 内的分位数划分：

- Near：下 1/3
- Mid：中间 1/3
- Far：上 1/3

这样比分固定毫米阈值更稳妥，因为不同 volume 的空间尺度不完全一致。

---

## 7. 重复性实验建议

严格配对实验完成后，再补重复性。

建议选：

- 2 个 volume
- 每个 volume 选 1 个中等角度组
- 每个角度组选 5 个代表性点
- 每个点重复 5 次
- 两种 mode 都做

总记录数：

- `2 × 1 × 5 × 5 × 2 = 100` 条

论文里这部分可以回答：

- 同一条件下误差波动范围有多大
- 操作者工作流本身是否稳定

---

## 8. 质控规则

建议把以下字段作为实验有效性检查项。

### 8.1 基本通过条件

- `markers_sorted = 1`
- 对应 mode 的 calibration ready = 1
- `shot1_available / shot2_available / shot3_available = 1`

### 8.2 建议重点查看的诊断字段

- `marker_sort_view{1..3}_rms_px`
- `marker_sort_view{1..3}_rms_gap_px`
- `perspective_calibration_view{1..3}_reproj_rms_px`
- `orthographic_calibration_view{1..3}_reproj_rms_px`
- `timing_*`

### 8.3 对异常样本的处理建议

出现以下情况时，建议标记为可疑样本：

- 某一 view 的 calibration reprojection RMS 明显高于该批次大多数样本
- marker sorting RMS 明显异常
- transform 快照缺失
- 导入恢复后实际几何条件与原记录不符

建议不要立刻删除异常样本，而是先单独做 QC 标记，后续在统计时做：

- 全样本分析
- 去除可疑样本后的敏感性分析

---

## 9. 统计分析建议

### 9.1 主分析

对于严格配对的 `Perspective vs Orthographic` 数据：

- 主要指标：
  - `TRE`
  - `RE`
- 建议方法：
  - 配对差值分析
  - Wilcoxon signed-rank test 或 paired t-test
  - 95% CI
  - 配对差值图

重点看：

- `Perspective - Orthographic` 的差值方向
- 差值是否稳定
- 差值是否受距离或角度分层影响

### 9.2 次分析

对 angle group 和 distance group 做分层：

- 按 volume 分开看
- 按 angle group 分开看
- 按 distance group 分开看

建议输出：

- 分层箱线图
- 分层差值图
- 每层的均值 / 中位数 / 标准差 / 样本量

### 9.3 重复性分析

对于重复实验：

- 计算每个条件下的均值、标准差、变异系数
- 如果写论文，可用来支持“操作稳定性”或“方法重复性”

---

## 10. 当前不建议优先做的事情

以下内容不是不能做，而是当前阶段优先级不如前面几项：

- 继续在同样条件下随机多采点，但不做严格配对
- 只增加总样本量，不控制角度和距离
- 先做很多复杂图，而核心配对数据还没采够

原因很简单：

- 样本量增加不等于结论更强
- 如果设计上仍然混杂，后面的统计分析依然解释力有限

---

## 11. 当前最推荐的执行顺序

### 阶段 A：Pilot

- 每个 volume 做少量点
- 确定距离三层
- 确定 3 组角度配置

### 阶段 B：主实验

- 按 `volume × angle group × distance group`
- 每格采 8 到 10 个点
- 每个点做 `Perspective / Orthographic` 严格配对

### 阶段 C：重复性

- 选择代表性条件
- 做重复采集

### 阶段 D：可选扩展

如果论文还需要更强的“鲁棒性”部分，再加：

- 2D 点扰动实验
- 人工点选误差实验
- marker 检测噪声 / 模糊 / 对比度变化实验

---

## 12. 与当前系统实现直接相关的操作建议

### 12.1 已经可以直接用的能力

- 保存实验记录到 `experiment/experiment_results.csv`
- 保存对应 transform 快照到 `experiment/transform_snapshots/`
- 导入 CSV 某一行恢复：
  - `testPoint`
  - `LinearTransform`
  - `LinearTransform_1`
  - `LinearTransform_2`

### 12.2 当前仍然存在的一个管理问题

虽然现在已经可以恢复同一几何状态，但配对记录之间还没有一个显式的 `pair_id` 字段。

因此当前建议：

- 主分析时，先用以下字段匹配配对样本：
  - `input_volume`
  - `testpoint_x`
  - `testpoint_y`
  - `testpoint_z`
  - `shot2_angle_deg`
  - `shot3_angle_m3_m1_deg`
  - `shot3_angle_m3_m2_deg`

后续如果需要，可以再补一个明确的：

- `import_source_record_id`
- 或 `pair_id`

这样后处理会更稳。

---

## 13. 最终建议

如果你现在只做一件最重要的事，那就是：

先完成“严格配对的 Projection Mode 主实验”。

建议的最小目标是：

- 2 个 volume
- 3 个 angle group
- 3 个 distance group
- 每格 8 个点
- 两种 mode 全部配对

也就是：

- 288 条高质量记录

这批数据的价值，会明显高于继续在当前方式下随机多采一批非配对样本。
