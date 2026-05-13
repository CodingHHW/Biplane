# 审稿源码发布前检查清单

本文件用于记录仓库在提交给审稿人或公开发布前需要确认的事项。它放在 `docs/` 中，而不是主 README 中，是为了让 GitHub 首页保持简洁。

## 论文信息

- 确认最终论文题目。
- 确认作者列表与单位信息。
- 确认投稿期刊/会议或发表状态。
- 如已有 DOI、IEEE Xplore 链接、arXiv 链接或 Zenodo DOI，在发布版中补充。
- 论文发表后补充最终 BibTeX。

## 软件环境

- 使用 fresh clone 验证 README 中的安装和运行流程。

## 审稿演示

- 录制 `demo_videos/biplane_quick_start.mp4` 或 GIF。
- 使用 fresh clone 验证演示流程。
- 确认推荐给审稿人的 sample volume 或 sample scene。
- 确认一条不依赖本地隐藏状态的最小可运行流程。

## 可复现性

- 选定一到两个可以稳定 replay 的 CSV 行号。
- 确认对应的 transform snapshots 已包含在仓库或外部归档中。
- 将最终论文中的 figures / tables 与 `Analysis/`、`manuscript/` 下的文件建立对应关系。
- 决定 `experiment/transform_snapshots/` 等较大的生成文件是否继续放在 Git 中，还是移动到 release archive 或 Zenodo。

## 元数据与许可证

- 替换 `Biplane.py` 中模板遗留的 contributor、category、help text 和 acknowledgement 字段。
- 说明测试数据、截图、视频与生成资源的来源和许可证。
- 如有需要，补充单独的数据许可证说明。
