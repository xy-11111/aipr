# Phase 7 Writing Record Round 01

## Summary

本轮完成 Phase 7 中文完整初稿，用于内部审阅和后续英文化。

本轮产物：

- `paper_draft/phase7_v1/paper_zh.md`
- `paper_draft/phase7_v1/asset_map.md`

本轮不产出最终英文投稿稿，也不套 AIPR LaTeX 模板。

## Writing Time

- Date: `2026-05-24`
- Workspace: `/home/ubuntu/wjy/ebpf_nodeport`

## External Submission Context

已复核 AIPR2026 submission 页面：`https://www.aipr.net/pages/sub.html`

- full paper 需要英文稿
- manuscript 至少 `8` full pages
- 正式投稿需包含 title、author information、abstract、keywords、conference topic、introduction、body、figures/tables、conclusion、references
- references 不少于 `10`

本轮中文初稿是内部写作中间产物，下一阶段需要英文化和模板化。

## Data Sources

中文初稿使用以下冻结资产：

- `datasets/phase3_v1/`
- `datasets/phase6_generalization_v1/`
- `results/phase4_baselines_v1/`
- `results/phase6_generalization_v1/`
- `results/phase6_ablation_v1/`
- `paper_assets/phase4_v1/`
- `paper_assets/phase5_v1/`
- `paper_assets/phase6_v1/`

未新增实验，未回改 Phase 3-6 数据。

## Key Claims Written Into Draft

1. 本文定位为基于 eBPF telemetry 的 NodePort 异常检测，不定位为完整替代 kube-proxy 或 Cilium 的系统论文。
2. 主数据集 `phase3_v1` 上，最佳 binary RF 的 test anomaly F1 为 `0.7774`，PR-AUC 为 `0.8449`。
3. 多分类结果更弱，最佳 multiclass RF 的 test macro-F1 为 `0.4181`。
4. `ETP=Cluster` 下 remote backend 场景 telemetry 打开后 throughput 下降约 `6.21%`，延迟未恶化。
5. `ETP=Cluster` 下 local backend 路径可工作，但测量更依赖 readiness gating。
6. Phase 6 泛化结果显示固定 Phase 4 模型在新负载/新拓扑下误报较高，多分类泛化明显退化。
7. 消融显示 `datapath_stats` 是最关键特征组。

## Known Limitations Preserved

中文初稿明确写入以下局限：

- 不支持或未评估 UDP
- 不支持或未评估 IPv6
- 不研究 `externalTrafficPolicy=Local`
- 未覆盖第二种 CNI 或 direct-routing 网络环境
- 当前是离线推理，不是在线检测闭环
- 部分类别样本较少，尤其是 `backend_churn` 和控制面类异常

## Static Checks

本轮要求后续验证：

- Phase 7 文档中没有未替换的英文占位词。
- Phase 7 文档中没有把 `externalTrafficPolicy=Local` 写成已支持的表述。
- Phase 7 文档中没有把 Phase 6 泛化结果写成全场景成功的表述。

预期：上述检查均无命中。

## Next Step

下一阶段建议进入：

- 英文化
- AIPR LaTeX 模板化
- 正式 BibTeX 引用整理
- 根据 8-10 页目标压缩图表和正文
