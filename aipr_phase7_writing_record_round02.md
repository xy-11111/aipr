# Phase 7 Writing Record Round 02

## Summary

本轮对 `paper_draft/phase7_v1/paper_zh.md` 做中文初稿逻辑增强，不新增实验、不回改 Phase 3-6 数据和模型。

本轮目标是把 Round 01 的“完整中文初稿”补强为更适合后续英文化和模板化的论文底稿。

## Writing Time

- Date: `2026-05-26`
- Workspace: `/home/ubuntu/wjy/ebpf_nodeport`

## Changes

本轮完成以下修订：

1. 新增“相关工作”章节，覆盖 Kubernetes Service 数据面、云原生监控与故障诊断、异常检测与模式识别。
2. 新增系统架构图 `paper_draft/phase7_v1/figures/fig_system_overview.svg`，展示 tc eBPF datapath、BPF maps、`nodeport-syncer`、telemetry collector、dataset 和模型链路。
3. 新增“特征选择与防泄漏”小节，明确排除 `label`、`anomaly_active`、`recovery_active`、`label_source`、实验 ID、节点/Service 标识、时间戳和 traffic probe 结果等泄漏字段。
4. 修正 Phase 5 开销叙事，把结论限定为“remote backend 场景吞吐下降约 `6.21%` 且尾延迟未观察到恶化”，同时保留 agent CPU 成本仍需优化的表述。
5. 补充 Phase 6 多分类泛化指标口径：Phase 6 输入只覆盖三类异常，但评估仍使用 Phase 4 的六类异常输出空间。
6. 将“低开销”相关表述改为更谨慎的“可在线采集特征”和“当前实现仍有 CPU 优化空间”。
7. 更新 `asset_map.md`，把系统架构图和新的章节编号纳入图表索引。

## Data And Model Scope

本轮未修改：

- `datasets/phase3_v1/`
- `datasets/phase6_generalization_v1/`
- `results/phase4_baselines_v1/`
- `results/phase6_generalization_v1/`
- `results/phase6_ablation_v1/`
- `paper_assets/phase4_v1/`
- `paper_assets/phase5_v1/`
- `paper_assets/phase6_v1/`

本轮仍沿用 Phase 4 最佳模型：

- Binary: `rf_n100_dnone_leaf5_cwnone`
- Multiclass: `rf_n100_dnone_leaf5_cwbalanced`

## Remaining Issues Before English Draft

后续英文化前仍需处理：

- 将候选参考文献整理为正式 BibTeX。
- 将 SVG 系统图转换为最终模板接受的 PDF/PNG 格式。
- 根据 AIPR full paper 页数要求压缩正文和图表数量。
- 决定是否把 Phase 5 local backend 只作为补充结果，还是放入主表。
- 在英文稿中进一步精炼 Phase 6 泛化退化的叙事，避免显得像实验失败。

## Static Checks

本轮要求后续验证：

- 中文初稿没有未替换的英文临时占位词。
- 中文初稿没有把 `externalTrafficPolicy=Local` 写成已支持。
- 中文初稿没有把 Phase 6 泛化写成所有场景均表现良好。
- `asset_map.md` 中的图表路径均存在。
