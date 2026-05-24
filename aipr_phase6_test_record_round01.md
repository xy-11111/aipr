# Phase 6 Test Record Round 01

## Summary

本轮完成了 `Phase 6` 的两条主线：

1. `Phase 6` 泛化实验矩阵
   - `normal_steady_state`: `low/medium/high x local/remote` 共 `6` 轮
   - `backend_churn`: `medium/high x local/remote` 共 `4` 轮
   - `conntrack_pressure`: `medium/high x local/remote` 共 `4` 轮
   - `path_degradation`: `medium/high x remote` 共 `2` 轮
2. `Phase 6` 特征消融
   - `all_features`
   - `minus_datapath_stats`
   - `minus_ct_gc`
   - `minus_k8s_events`
   - `minus_topology_backend`

结果：

- `16` 轮泛化实验全部通过，无场景被排除。
- 已构建 `datasets/phase6_generalization_v1/`，总计 `1896` 行窗口样本、`16` 个实验。
- 已完成固定 `Phase 4` 最优模型的跨负载、跨拓扑推理，结果写入 `results/phase6_generalization_v1/`。
- 已完成特征消融，结果写入 `results/phase6_ablation_v1/`。
- 已导出 `paper_assets/phase6_v1/` 论文资产。

## Test Time

- Date: `2026-05-24`
- Workspace: `/home/ubuntu/wjy/ebpf_nodeport`

## Scope

### Generalization

- ingress 固定为 `worker1`
- `local` 表示唯一 backend 在 `worker1`
- `remote` 表示唯一 backend 在 `worker2`
- `path_degradation` 只在 `remote` 拓扑执行，因为 `local_single` 下对 `flannel.1` 注入退化缺少清晰语义

### Ablation

- 训练/验证/测试集固定使用 `datasets/phase3_v1/`
- 模型配置固定复用 `Phase 4` 最优 RF：
  - binary: `rf_n100_dnone_leaf5_cwnone`
  - multiclass: `rf_n100_dnone_leaf5_cwbalanced`

## Commands

### Generalization Matrix

```bash
bash experiments/phase6_run_matrix.sh
python3 scripts/phase3_build_dataset.py \
  --artifacts-dir artifacts \
  --output-dir datasets/phase6_generalization_v1 \
  --selection-file datasets/phase6_generalization_v1/experiment_selection.json
./.venv-phase4/bin/python scripts/phase6_run_generalization.py
```

### Ablation

```bash
./.venv-phase4/bin/python scripts/phase6_run_ablation.py
```

### Paper Assets

```bash
./.venv-phase4/bin/python scripts/phase6_export_paper_assets.py
```

## Included Experiment IDs

`datasets/phase6_generalization_v1/experiment_selection.json` 最终纳入了全部 `16` 个实验：

- `phase6-local-low-normal-20260524T065946Z`
- `phase6-remote-low-normal-20260524T070146Z`
- `phase6-local-medium-normal-20260524T070311Z`
- `phase6-remote-medium-normal-20260524T070442Z`
- `phase6-local-high-normal-20260524T070608Z`
- `phase6-remote-high-normal-20260524T070734Z`
- `phase6-local-medium-backend-churn-20260524T070925Z`
- `phase6-remote-medium-backend-churn-20260524T071048Z`
- `phase6-local-high-backend-churn-20260524T071203Z`
- `phase6-remote-high-backend-churn-20260524T071316Z`
- `phase6-local-medium-conntrack-pressure-20260524T071431Z`
- `phase6-remote-medium-conntrack-pressure-20260524T071524Z`
- `phase6-local-high-conntrack-pressure-20260524T071629Z`
- `phase6-remote-high-conntrack-pressure-20260524T071751Z`
- `phase6-remote-medium-path-degradation-20260524T071903Z`
- `phase6-remote-high-path-degradation-20260524T072035Z`

排除实验数：`0`

## Dataset Build Result

- output: `datasets/phase6_generalization_v1/`
- rows: `1896`
- experiments: `16`
- normal rows: `827`
- anomaly rows: `1069`
- anomaly-only rows: `239`

label 分布：

- `normal`: `1657`
- `backend_churn`: `24`
- `conntrack_pressure`: `143`
- `path_degradation`: `72`

## Generalization Results

### A. 正常稳态误报

`normal_binary_summary.csv` 显示固定 `Phase 4` binary RF 在新负载/新拓扑下的误报偏高：

- `local` false positive rate：
  - `low`: `0.3731`
  - `medium`: `0.3759`
  - `high`: `0.3780`
- `remote` false positive rate：
  - `low`: `0.7008`
  - `medium`: `0.5078`
  - `high`: `0.4719`

这说明 Phase 4 固定模型在新正常稳态上**存在明显误报**，尤其是 `remote + low`。

### B. 异常二分类

`anomaly_binary_metrics.json` 的总体结果：

- accuracy: `0.4406`
- balanced accuracy: `0.5310`
- anomaly precision: `0.2402`
- anomaly recall: `0.6946`
- anomaly F1: `0.3570`
- PR-AUC: `0.2938`
- ROC-AUC: `0.5812`

分场景看：

- `backend_churn` 最弱：
  - F1 约 `0.0833 ~ 0.1311`
- `conntrack_pressure` 最稳：
  - `remote + medium`: `0.6526`
  - `remote + high`: `0.6261`
  - `local + medium`: `0.5313`
- `path_degradation` 中等：
  - `remote + high`: `0.5373`
  - `remote + medium`: `0.1833`

### C. 异常多分类

`anomaly_multiclass_metrics.json` 的总体结果：

- accuracy: `0.1004`
- balanced accuracy: `0.1020`
- macro-F1: `0.0697`
- weighted-F1: `0.1401`

分场景主标签表现：

- `backend_churn`
  - 大多数场景 `primary_label_f1 = 0`
- `conntrack_pressure`
  - 最好约 `0.1579`
- `path_degradation`
  - `remote + medium`: `0.5714`
  - `remote + high`: `0.0526`

结论是：**Phase 4 固定 multiclass 模型在 Phase 6 新负载/新拓扑上的泛化明显不足。**

## Ablation Results

### Binary

`binary_variant_summary.csv` 结果如下：

- `all_features`: test anomaly F1 `0.7774`
- `minus_datapath_stats`: `0.4982`
- `minus_ct_gc`: `0.7034`
- `minus_k8s_events`: `0.7375`
- `minus_topology_backend`: `0.6988`

最明显的退化来自 `minus_datapath_stats`，说明 **datapath 统计特征是二分类主力**。

### Multiclass

`multiclass_variant_summary.csv` 结果如下：

- `all_features`: test macro-F1 `0.4181`
- `minus_datapath_stats`: `0.2339`
- `minus_ct_gc`: `0.3949`
- `minus_k8s_events`: `0.4082`
- `minus_topology_backend`: `0.3748`

同样是 `minus_datapath_stats` 掉幅最大，`topology_backend` 与 `ct_gc` 也有可见贡献，`k8s_events` 去除后的下降最小。

## Artifact Outputs

### Results

- `results/phase6_generalization_v1/`
- `results/phase6_ablation_v1/`

### Paper Assets

- `paper_assets/phase6_v1/tables/table_phase6_generalization_binary.csv`
- `paper_assets/phase6_v1/tables/table_phase6_generalization_multiclass.csv`
- `paper_assets/phase6_v1/tables/table_phase6_ablation_binary.csv`
- `paper_assets/phase6_v1/tables/table_phase6_ablation_multiclass.csv`
- `paper_assets/phase6_v1/figures/fig_phase6_generalization_trend.png`
- `paper_assets/phase6_v1/figures/fig_phase6_ablation_drop.png`

## Verification Notes

- `Phase 6` 运行结束后，`failure_reason` 检查结果为空。
- `preflight_experiment_topology balanced` 复核通过，说明矩阵结束后集群已恢复到 balanced topology。
- `paper_assets/phase6_v1/source/` 中已拆分：
  - `generalization_run_manifest.json`
  - `ablation_run_manifest.json`

## Interpretation

本轮最重要的结论不是“模型一切都很好”，而是：

1. **消融证据很强。**
   - `datapath_stats` 是最关键的特征组。
   - `ct_gc` 与 `topology_backend` 也有真实贡献。
2. **固定 Phase 4 模型的跨负载/跨拓扑泛化有限。**
   - 正常稳态误报偏高。
   - 多分类在新场景下退化明显。
3. **异常类型之间的泛化难度不同。**
   - `conntrack_pressure` 最容易迁移。
   - `backend_churn` 最难。
   - `path_degradation` 对负载条件更敏感。

这意味着论文在 `Phase 6` 章节里应采用更诚实也更有价值的叙事：

- 不把当前模型包装成“已经全面泛化”
- 而是明确写成“当前特征设计有效，但固定模型在跨场景迁移上仍有明显边界，这也是后续改进方向”

## Exit Criteria

- 16 轮泛化实验完成：`passed`
- `phase6_generalization_v1` 构建完成：`passed`
- 固定 Phase 4 模型推理完成：`passed`
- 5 组特征消融完成：`passed`
- `paper_assets/phase6_v1` 导出完成：`passed`
