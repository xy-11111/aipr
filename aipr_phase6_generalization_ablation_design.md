# `ebpf_nodeport` Phase 6 泛化与消融设计文档

## 1. 目标

`Phase 6` 的任务不是再造一套新系统，而是在已经完成的 `Phase 3-5` 基础上，补足论文最容易被审稿人追问的两类证据：

1. **泛化性**：当前异常检测结果是否只在一组固定负载和单一场景下成立。
2. **消融性**：当前效果是否依赖某一类特征，去掉不同特征组后结论是否还能站住。

本阶段完成后，论文将不仅有“能工作”的证据，也会有“为什么有效、在多大范围内有效”的证据。

## 2. 当前前提

截至 `2026-05-24`，已具备如下固定输入：

- 冻结数据集：`datasets/phase3_v1/`
- baseline 结果：`results/phase4_baselines_v1/`
- 论文图表资产：
  - `paper_assets/phase4_v1/`
  - `paper_assets/phase5_v1/`

因此 `Phase 6` 不再回改：

- `phase3_v1` 数据集定义
- `Phase 4` baseline 模型集合
- `Phase 5` 最小 `2x2` 开销矩阵

若后续需要扩充数据，统一采用“附加实验 + 新结果目录”的方式，不覆盖现有冻结资产。

## 3. 研究问题

`Phase 6` 聚焦 3 个问题：

1. 当前检测结果在不同负载强度下是否稳定？
2. 当前检测结果在 `local backend` 与 `remote backend` 场景下是否具有一致趋势？
3. 去掉不同特征组后，模型性能会下降多少？

## 4. 范围与边界

本阶段只做：

- 不同流量强度下的泛化评估
- `local/remote` 场景对比
- 特征组消融

本阶段不做：

- 第二套 CNI 或第二种集群网络环境
- 新模型族（例如 XGBoost、LSTM、TCN）
- 在线推理链路
- 新的数据标注体系

这样可以把 `Phase 6` 收敛成一个可控、可验证、可直接写入论文的阶段。

## 5. 泛化实验设计

### 5.1 负载档位

在 `worker1` 固定作为入口节点的前提下，定义 3 个负载档位：

- `low`
  - `concurrency=8`
  - `duration=60s`
- `medium`
  - `concurrency=16`
  - `duration=60s`
- `high`
  - `concurrency=32`
  - `duration=60s`

三档负载统一使用与 `Phase 5` 相同的 benchmark 框架，避免引入新的测量方法差异。

### 5.2 拓扑维度

每个负载档位都在两种 backend 拓扑下执行：

- `local backend`
  - 唯一 backend 在 `worker1`
- `remote backend`
  - 唯一 backend 在 `worker2`

### 5.3 评估方式

泛化不重新训练模型，先采用“固定 `Phase 4` 最优模型 + 新实验数据”的方式验证：

- 二分类使用 `rf_n100_dnone_leaf5_cwnone`
- 多分类使用 `rf_n100_dnone_leaf5_cwbalanced`

这样可以直接回答“既有模型是否能泛化到不同负载条件”。

## 6. 消融实验设计

### 6.1 特征组划分

将当前特征划分为 4 组：

1. `datapath_stats`
   - 例如 `delta_new_conn`、`delta_ct_lookup_miss`、`delta_tcp_packets`
2. `ct_gc`
   - 例如 `ct_active_count`、`fwd_ct_active_count`、`gc_deleted_ct`
3. `k8s_events`
   - 例如 `service_event_seen`、`slice_event_seen`、`node_event_seen`
4. `topology_backend`
   - 例如 `backend_total`、`backend_local`、`backend_remote`、`routing_mode`

### 6.2 消融策略

采用逐组去除的方式：

- `all_features`
- `minus_datapath_stats`
- `minus_ct_gc`
- `minus_k8s_events`
- `minus_topology_backend`

### 6.3 指标

二分类关注：

- anomaly `F1`
- `PR-AUC`
- `balanced accuracy`

多分类关注：

- `macro-F1`
- `balanced accuracy`
- 各异常类别的 `F1`

## 7. 产物规划

### 7.1 新增脚本

建议新增：

- `scripts/phase6_run_generalization.py`
- `scripts/phase6_run_ablation.py`
- `scripts/phase6_export_paper_assets.py`

### 7.2 新增结果目录

- `results/phase6_generalization_v1/`
- `results/phase6_ablation_v1/`
- `paper_assets/phase6_v1/`

### 7.3 新增实验记录

- `aipr_phase6_test_record_round01.md`

若中途需要修订并重跑，再新增：

- `aipr_phase6_test_record_round02.md`

## 8. 论文输出目标

本阶段希望最终导出 4 张核心表图：

1. `table_phase6_generalization_binary.csv/.tex`
2. `table_phase6_generalization_multiclass.csv/.tex`
3. `table_phase6_ablation_binary.csv/.tex`
4. `table_phase6_ablation_multiclass.csv/.tex`

可选图：

- `fig_phase6_generalization_trend.png`
- `fig_phase6_ablation_drop.png`

## 9. 退出标准

满足以下条件即可认为 `Phase 6` 完成：

- 至少 3 个负载档位的泛化结果完整
- `local/remote` 对比结果完整
- 二分类与多分类都完成特征组消融
- 有至少一类特征组被证明对结果显著重要
- 已产出可直接写入论文的表图与中文测试记录

## 10. 下一步执行顺序

建议按这个顺序推进：

1. 先写并实现 `Phase 6` 结果脚本骨架
2. 跑第一轮泛化实验
3. 跑第一轮消融实验
4. 导出 `paper_assets/phase6_v1/`
5. 再统一回写论文正文
