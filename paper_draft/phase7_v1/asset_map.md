# Phase 7 图表索引

本文档记录中文初稿 `paper_zh.md` 中建议引用的图表来源。检测效果、开销、泛化和消融图表只来自已经冻结的 `paper_assets/phase4_v1`、`paper_assets/phase5_v1` 和 `paper_assets/phase6_v1`；系统架构图作为 Phase 7 写作辅助图放在 `paper_draft/phase7_v1/figures/`。

## 论文结构与图表放置

| 论文位置 | 建议编号 | 文件 | 用途 |
| --- | --- | --- | --- |
| 第 4.1 节 总体架构 | 图 1 | `paper_draft/phase7_v1/figures/fig_system_overview.svg` | 展示 eBPF datapath、syncer、collector、dataset 和模型链路 |
| 第 7.1 节 主数据集检测效果 | 表 1 | `paper_assets/phase4_v1/tables/table_phase4_binary_main.csv` | 展示二分类 baseline 对比 |
| 第 7.1 节 主数据集检测效果 | 表 2 | `paper_assets/phase4_v1/tables/table_phase4_multiclass_main.csv` | 展示多分类 baseline 对比 |
| 第 7.1 节 主数据集检测效果 | 表 3 | `paper_assets/phase4_v1/tables/table_phase4_multiclass_per_class.csv` | 展示各异常类别表现 |
| 第 7.1 节 主数据集检测效果 | 图 2 | `paper_assets/phase4_v1/figures/fig_phase4_binary_confusion.png` | 展示二分类混淆矩阵 |
| 第 7.1 节 主数据集检测效果 | 图 3 | `paper_assets/phase4_v1/figures/fig_phase4_multiclass_confusion.png` | 展示多分类混淆矩阵 |
| 第 7.1 节 主数据集检测效果 | 图 4 | `paper_assets/phase4_v1/figures/fig_phase4_per_class_f1.png` | 展示每类异常 F1 |
| 第 7.2 节 运行时开销 | 表 4 | `paper_assets/phase5_v1/tables/table_phase5_overhead_matrix.csv` | 展示 telemetry off/on 与 local/remote backend 的 2x2 结果 |
| 第 7.2 节 运行时开销 | 表 5 | `paper_assets/phase5_v1/tables/table_phase5_overhead_delta.csv` | 展示 telemetry on 相对 off 的变化 |
| 第 7.2 节 运行时开销 | 图 5 | `paper_assets/phase5_v1/figures/fig_phase5_throughput_latency.png` | 展示吞吐与延迟 |
| 第 7.2 节 运行时开销 | 图 6 | `paper_assets/phase5_v1/figures/fig_phase5_cpu_overhead.png` | 展示 CPU 开销 |
| 第 7.3 节 泛化评估 | 表 6 | `paper_assets/phase6_v1/tables/table_phase6_generalization_binary.csv` | 展示正常误报与异常二分类泛化 |
| 第 7.3 节 泛化评估 | 表 7 | `paper_assets/phase6_v1/tables/table_phase6_generalization_multiclass.csv` | 展示异常多分类泛化 |
| 第 7.3 节 泛化评估 | 图 7 | `paper_assets/phase6_v1/figures/fig_phase6_generalization_trend.png` | 展示负载与拓扑变化下的泛化趋势 |
| 第 7.4 节 特征消融 | 表 8 | `paper_assets/phase6_v1/tables/table_phase6_ablation_binary.csv` | 展示二分类特征消融 |
| 第 7.4 节 特征消融 | 表 9 | `paper_assets/phase6_v1/tables/table_phase6_ablation_multiclass.csv` | 展示多分类特征消融 |
| 第 7.4 节 特征消融 | 图 8 | `paper_assets/phase6_v1/figures/fig_phase6_ablation_drop.png` | 展示去除特征组后的性能变化 |

## 写作注意事项

- `local backend` 和 `remote backend` 必须写成 `ETP=Cluster` 前提下的后端拓扑，不表示 `externalTrafficPolicy=Local`。
- Phase 6 泛化结果不能写成全场景都表现良好，应写成揭示了固定模型的泛化边界。
- Phase 6 多分类泛化只覆盖三类异常输入，但模型输出空间保持 Phase 4 的六类异常，需要在正文中说明该指标口径。
- Phase 5 local backend 的 throughput 上升不应解释为 telemetry 带来性能提升，应写成测量波动与拓扑收敛敏感性的结果。
- Phase 5 agent CPU 是 pod-level one-core percentage，超过 `100` 不表示百分比写错，而表示超过一个 CPU core 的时间份额。
- Phase 4 的 binary 结果是最强检测证据；Phase 6 的 ablation 是最强特征有效性证据。
