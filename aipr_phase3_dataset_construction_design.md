# Phase 3 数据集构建设计文档

## 1. 目标

`Phase 3` 的目标是把 `Phase 1` 的 telemetry 输出和 `Phase 2` 的故障注入产物，整理成一套可复现、可切分、可直接喂给 baseline 模型的 `Dataset v1`。

这一阶段不追求模型效果最大化，重点是先把下面三件事做稳：

1. 把实验产物整理成结构一致的数据集目录
2. 把窗口样本、事件标签、流量结果和实验元信息对齐
3. 产出一版可以直接用于 `Phase 4` baseline 的训练/验证/测试集

## 2. 与前两阶段的衔接

`Phase 3` 只消费已经跑通的产物，不重新定义新的采样格式。

直接输入来自：

- `artifacts/<experiment_id>/telemetry/<node>.csv`
- `artifacts/<experiment_id>/events.jsonl`
- `artifacts/<experiment_id>/traffic.csv`
- `artifacts/<experiment_id>/meta.json`
- `artifacts/<experiment_id>/notes.txt`
- `artifacts/<experiment_id>/agent-logs/<node>.log`

相关设计与记录可参考：

- 总方案：[aipr_anomaly_detection_plan.md](/home/ubuntu/wjy/ebpf_nodeport/aipr_anomaly_detection_plan.md:1)
- Phase 1 设计：[aipr_phase1_implementation_design.md](/home/ubuntu/wjy/ebpf_nodeport/aipr_phase1_implementation_design.md:1)
- Phase 2 设计：[aipr_phase2_fault_injection_design.md](/home/ubuntu/wjy/ebpf_nodeport/aipr_phase2_fault_injection_design.md:1)
- Phase 2 Round 03 记录：[aipr_phase2_test_record_round03.md](/home/ubuntu/wjy/ebpf_nodeport/aipr_phase2_test_record_round03.md:1)

## 3. 当前约束

在进入数据集构建前，先明确当前实现边界：

### 3.1 单目标 Service

当前 telemetry 仍然基于节点级 stats map，因此第一版数据集继续限定为：

- 单目标 namespace/service
- 固定 `NodePort 30080`
- 每个节点、每个窗口一条样本

这意味着 `Dataset v1` 的核心样本粒度仍然是：

`experiment_id + node_name + window_start_unix_ms`

### 3.2 两节点实验拓扑

当前有效实验数据全部基于两节点模式：

- `k8s-worker1`
- `k8s-worker2`

`worker3` 与 `master` 不纳入 `Dataset v1`。

### 3.3 标签集合

第一版数据集采用已落地的粗粒度标签：

- `normal`
- `backend_churn`
- `control_plane_recovery`
- `path_degradation`
- `conntrack_pressure`
- `service_reconcile`
- `load_surge`

其中：

- `backend_churn` 可能来自 `backend_rollout_restart` 或 `endpointslice_churn`
- `control_plane_recovery` 可能来自 `agent_restart` 或 `map_rebuild`

## 4. Dataset v1 的目标形态

`Dataset v1` 建议同时保留三层产物：

### 4.1 原始层

不改内容，只做归档与索引：

- 每轮实验原始 artifact
- 原始 telemetry CSV
- 原始 event 文件
- 原始 traffic 结果

目的：

- 保留可追溯性
- 允许后续重新清洗

### 4.2 窗口样本层

按“每节点每窗口一行”的方式汇总成统一表。

这一层是 `Phase 4` 的主输入，建议命名为：

- `windows_all.csv`

### 4.3 序列样本层

为了后续 `LSTM / 1D-CNN / TCN` 预留一层滑动窗口序列数据。

这一层不作为 `Dataset v1` 的硬门槛，但建议预先设计目录和字段。

可选输出：

- `sequences_len8.csv`
- `sequences_len16.csv`

## 5. 目录结构建议

建议在仓库中新增统一的数据集目录：

```text
datasets/
  phase3_v1/
    raw_index.csv
    experiment_selection.json
    windows_all.csv
    windows_train.csv
    windows_val.csv
    windows_test.csv
    splits.json
    dataset_manifest.json
    dataset_summary.md
    quality_report.md
    artifacts_snapshot/
```

字段说明：

- `raw_index.csv`
  - 每条实验产物的索引
- `experiment_selection.json`
  - checked-in 的实验纳入清单，作为 `phase3_v1` 刷新时的唯一来源
- `windows_all.csv`
  - 汇总后的全量窗口样本
- `windows_train.csv` / `windows_val.csv` / `windows_test.csv`
  - 按实验切分后的数据集
- `splits.json`
  - 记录每个 `experiment_id` 被分到哪个 split
- `dataset_manifest.json`
  - 记录版本、时间、数据来源、字段、统计摘要
- `dataset_summary.md`
  - 面向论文和后续实验的人类可读说明
- `quality_report.md`
  - 数据质量检查结果

## 6. 数据集构建范围

`Dataset v1` 只纳入已经通过验收的实验，不把失败轮次混进主数据集。

当前实现中，“哪些实验应被纳入主数据集”不再由 Python 脚本内部常量决定，而是由：

- [experiment_selection.json](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/experiment_selection.json:1)

统一维护。这样补采和回刷时只需要更新清单并重跑离线脚本，不需要反复改代码。

### 6.1 纳入范围

建议纳入以下实验：

- `normal_steady_state`
- `backend_rollout_restart`
- `agent_restart`
- `map_rebuild`
- `path_degradation_netem`
- `conntrack_pressure`
- `service_delete_recreate`
- `endpointslice_churn`
- `traffic_burst`

### 6.2 排除范围

建议从 `Dataset v1` 主表中排除：

- 明确失败的实验轮次
- 修复前的 `agent_restart` 首次失败样本
- telemetry 文件缺失的实验
- 标签与事件区间对不齐的实验

但这些实验仍可以在 `artifacts_snapshot/` 或单独索引中保留，供后续“负例/脏数据分析”使用。

## 7. 样本表设计

`windows_all.csv` 建议直接复用现有 telemetry schema，不再另起一套字段名。

现有字段基线在 [telemetry_schema.go](/home/ubuntu/wjy/ebpf_nodeport/cmd/nodeport-syncer/telemetry_schema.go:29)。

### 7.1 必保留字段

以下字段必须保留到 `windows_all.csv`：

- 元信息：
  - `window_start_unix_ms`
  - `window_end_unix_ms`
  - `window_seconds`
  - `experiment_id`
  - `node_name`
  - `node_ip`
  - `service_namespace`
  - `service_name`
  - `service_nodeport`
  - `routing_mode`
  - `has_remote_backend`
- 数据面增量：
  - `delta_tcp_packets`
  - `delta_nodeport_hit`
  - `delta_backend_selected`
  - `delta_backend_lookup_miss`
  - `delta_rr_update`
  - `delta_snat_install`
  - `delta_request_rewrite`
  - `delta_revnat_hit`
  - `delta_ct_lookup_miss`
  - `delta_response_rewrite`
  - `delta_fwd_ct_hit`
  - `delta_new_conn`
  - `delta_map_miss`
  - `delta_rewrite_fail`
  - `delta_redirect_ok`
  - `delta_redirect_fail`
  - `delta_fallback_pass`
- CT / GC：
  - `ct_active_count`
  - `fwd_ct_active_count`
  - `gc_runs_in_window`
  - `gc_deleted_ct`
  - `gc_deleted_fwd_ct`
  - `ct_entry_timeout_seconds`
  - `ct_gc_interval_seconds`
- 控制面：
  - `backend_total`
  - `backend_local`
  - `backend_remote`
  - `backend_total_delta`
  - `service_event_seen`
  - `slice_event_seen`
  - `node_event_seen`
  - `sync_reconcile_count`
  - `sync_upserted_services`
  - `sync_removed_services`
- 标签：
  - `label`
  - `label_source`
  - `anomaly_active`
  - `recovery_active`

### 7.2 Dataset 层新增字段

在不破坏现有 schema 的前提下，建议额外加这些派生字段：

| 字段名 | 说明 |
| --- | --- |
| `experiment_type` | 从 `experiment_id` 或事件标签归一化出的场景名 |
| `split` | `train` / `val` / `test` |
| `is_baseline_experiment` | 是否属于 `normal_steady_state` |
| `traffic_probe_total` | 对应实验 `traffic.csv` 的探测总数 |
| `traffic_probe_success` | 对应实验 `traffic.csv` 的成功数 |
| `traffic_probe_success_rate` | 对应实验整体成功率 |
| `event_count` | 对应实验 `events.jsonl` 条目数 |
| `source_artifact_dir` | 回溯到原始 artifact 目录 |

这些字段不需要写回原 telemetry 文件，只在 `windows_all.csv` 中增加即可。

## 8. 构建流程

建议 `Phase 3` 的实现按下面 6 步走：

### 8.1 建立实验索引

先读取 `datasets/phase3_v1/experiment_selection.json`，再扫描 `artifacts/` 目录，收集每个实验的：

- `experiment_id`
- 场景名
- 是否有 `telemetry/*.csv`
- 是否有 `events.jsonl`
- 是否有 `traffic.csv`
- 是否有 `agent-logs/*.log`
- 是否来自通过轮次

输出：

- `raw_index.csv`

这里的关键约束是：

- `included_experiments` 是主数据集唯一的纳入来源
- `excluded_experiments` 只用于显式屏蔽已知坏轮次
- 如果某个补采实验失败，不更新选择清单，也不半途刷新 `phase3_v1`

### 8.2 读取并规范化 telemetry

对每个实验、每个节点：

1. 读取原始 CSV
2. 检查 header 是否与现有 schema 一致
3. 统一字段顺序
4. 加入 dataset 层新增字段
5. 追加到 `windows_all.csv`

### 8.3 对齐事件与实验元信息

虽然 telemetry 里已经有 `label` 和 `recovery_active`，但仍建议在 Phase 3 再做一次离线一致性校验：

1. 读取 `events.jsonl`
2. 随机抽样若干窗口检查：
   - 事件期窗口是否带目标 label
   - 恢复尾窗是否带 `recovery_active=1`
3. 若发现不一致，标记该实验为 `needs_review`

### 8.4 合并 traffic 统计

`traffic.csv` 不建议逐条并入每个窗口，而是先生成实验级摘要：

- `traffic_probe_total`
- `traffic_probe_success`
- `traffic_probe_success_rate`

再把实验级摘要复制到该实验的所有窗口样本上。

这样做的好处是：

- 避免把高频主动探测行数直接膨胀成窗口表
- 仍能为后续分析提供“场景级可达性背景”

### 8.5 生成数据切分

切分必须按 `experiment_id` 做，而不能按单行随机切分。

原因：

- 同一实验相邻窗口高度相关
- 如果按行随机切分，train/test 会发生时间泄漏

推荐切分原则：

- `train`：60%
- `val`：20%
- `test`：20%

在第一版数据量还不大时，优先满足：

1. 每个标签都在 `train` 中出现
2. 每个标签都在 `test` 中至少出现 1 个实验
3. 同一类场景的不同实验尽量分布到不同 split

### 8.6 生成数据集报告

输出两份人类可读文档：

- `dataset_summary.md`
- `quality_report.md`

## 9. 切分策略

`Dataset v1` 建议采用“按实验分组切分”的规则。

### 9.1 最小切分单元

切分单元固定为：

- `experiment_id`

而不是：

- 单窗口
- 单节点

### 9.2 推荐切分方式

如果每类场景实验次数还不够多，建议先采用“场景分层 + 实验级切分”：

- `normal_steady_state` 至少留 1 个实验给 `test`
- 每个异常标签至少留 1 个实验给 `test`
- 其余实验按数量落到 `train` 和 `val`

### 9.3 第一版特殊约束

由于当前 `Dataset v1` 很可能实验轮次不算多，建议优先保证：

- `test` 是“从未见过的 experiment_id”
- `val` 和 `test` 不共享同一实验

如果某个标签当前只有 1 个实验：

- 先全部放入 `train`
- 在 `dataset_summary.md` 中明确标出“该标签暂不具备独立测试集”

## 10. 平衡与采样策略

`normal` 样本通常远多于异常样本，因此需要在 `Phase 3` 先定义平衡规则，避免 `Phase 4` 临时拍脑袋。

### 10.1 不直接删掉全部正常样本

正常样本要保留，但建议：

- 对超长稳态实验做下采样
- 保留足够的正常窗口覆盖不同节点和不同时间段

### 10.2 推荐第一版平衡方式

建议在 `train` 集内部做轻量平衡：

- `normal`：随机下采样到异常总量的 `1x ~ 2x`
- 异常类：暂不做过采样，先保持原始比例

`val/test` 不做重采样，保持自然分布。

### 10.3 序列模型的平衡原则

如果后续构建序列样本：

- 切分先按实验做
- 再在每个 split 内构造滑动窗口序列
- 不允许先构造序列再跨 split 打散

## 11. 数据质量门槛

只有通过以下检查，`Dataset v1` 才算可以交付给 `Phase 4`：

### 11.1 文件完整性

- 每个纳入实验都必须存在：
  - `telemetry/*.csv`
  - `events.jsonl`
  - `traffic.csv`
- `agent-logs/*.log` 建议存在，但若单个实验缺失可先记为 warning

### 11.2 Schema 一致性

- 所有 telemetry CSV header 完全一致
- 所有必需字段都非缺失
- 不允许字段顺序漂移

### 11.3 标签一致性

- 目标 label 在事件期必须出现
- `recovery_active=1` 在恢复尾窗必须出现
- `normal` 窗口不能被错误标到异常类

### 11.4 数值合理性

- 所有 `delta_*` 字段非负
- `window_end_unix_ms > window_start_unix_ms`
- `backend_total = backend_local + backend_remote`
  - 若不满足，记为 warning 并抽样复核
- `traffic_probe_success_rate` 落在 `[0, 1]`

### 11.5 数据覆盖

- 至少包含 1 个正常稳态实验
- 至少包含所有已实现的异常标签
- 每个纳入 label 至少有一份可用于训练的数据

## 12. 建议实现文件

`Phase 3` 推荐新增一个轻量脚本目录：

```text
scripts/
  phase3_build_dataset.py
  phase3_split_dataset.py
  phase3_dataset_report.py
```

职责建议如下：

- `phase3_build_dataset.py`
  - 扫描 `artifacts/`
  - 构建 `raw_index.csv`
  - 输出 `windows_all.csv`
- `phase3_split_dataset.py`
  - 读取 `windows_all.csv`
  - 按 `experiment_id` 生成 `train/val/test`
  - 输出 `splits.json`
- `phase3_dataset_report.py`
  - 统计标签分布
  - 统计节点分布
  - 生成 `dataset_summary.md`
  - 生成 `quality_report.md`

实现语言建议用 `Python 3`，原因很简单：

- CSV/JSON 处理更直接
- 适合做离线数据处理
- 不会污染在线 agent/syncer 路径

## 13. 交付物

`Phase 3` 完成时，至少应交付：

1. `datasets/phase3_v1/windows_all.csv`
2. `datasets/phase3_v1/windows_train.csv`
3. `datasets/phase3_v1/windows_val.csv`
4. `datasets/phase3_v1/windows_test.csv`
5. `datasets/phase3_v1/splits.json`
6. `datasets/phase3_v1/dataset_manifest.json`
7. `datasets/phase3_v1/dataset_summary.md`
8. `datasets/phase3_v1/quality_report.md`
9. 一份 `Phase 3` 测试/构建记录文档

## 14. 退出标准

只有同时满足以下条件，才视为 `Phase 3` 完成：

1. 已产出 `Dataset v1`
2. 所有纳入实验都能回溯到原始 artifact
3. `train/val/test` 已按实验切分完成
4. 已有一份明确的数据集说明文档
5. 数据质量报告中没有阻塞性错误
6. 数据集足够支撑 `Phase 4` 的第一轮 baseline

## 15. 建议执行顺序

为了减少返工，建议按下面顺序推进：

1. 先做 `raw_index.csv`
2. 再做 `windows_all.csv`
3. 然后补 `quality_report.md`
4. 质量检查过关后再切分 `train/val/test`
5. 最后写 `dataset_summary.md`

如果中途发现某类实验样本过少，不要在 `Phase 3` 临时改 schema，而是回到 `Phase 2` 补实验轮次。
