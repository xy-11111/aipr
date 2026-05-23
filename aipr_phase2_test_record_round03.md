# Phase 2 Round 03 测试记录

## 1. 记录概览

- 测试时间：2026-05-23 08:42 UTC - 08:46 UTC
- 记录目的：验证 `Phase 2` Round 03 的四个场景：
  - `conntrack_pressure`
  - `service_delete_recreate`
  - `endpointslice_churn`
  - `traffic_burst`
- 结论：**Round 03 通过**

本轮延续前两轮的两节点实验拓扑：

- `k8s-worker1`
- `k8s-worker2`

`worker3` 和 `master` 继续排除，不纳入阻塞项。

## 2. Round 03 前置状态

进入本轮前，系统处于以下稳定状态：

- `ebpf-nodeport-agent`
  - `worker1`：`Running`
  - `worker2`：`Running`
- `nodeport-echo`
  - `worker1`：1 个 `Running` backend
  - `worker2`：1 个 `Running` backend

Round 02 中验证过的 `map_rebuild` 与 `netem` 清理链路保持正常，未发现遗留状态污染本轮实验。

## 3. 执行的实验

### 3.1 `conntrack_pressure`

- 实验脚本：[conntrack_pressure.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/conntrack_pressure.sh:1)
- 实验 ID：`conntrack-pressure-20260523T084229Z`
- 产物目录：[artifacts/conntrack-pressure-20260523T084229Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/conntrack-pressure-20260523T084229Z)

#### 结果摘要

- `events.jsonl` 写入 1 条 `conntrack_pressure`
  - `ts_start_unix_ms=1779525763473`
  - `ts_end_unix_ms=1779525778473`
  - `recovery_tail_ms=3000`
- `worker1` CSV：`31` 条数据行
  - 标签集合：`normal`、`conntrack_pressure`
  - `recovery_active=1` 窗口数：`3`
  - `ct_active_count` 峰值：基线 `724` -> 事件期 `5878`
- `worker2` CSV：`30` 条数据行
  - 标签集合：`normal`、`conntrack_pressure`
  - `recovery_active=1` 窗口数：`3`
  - `ct_active_count` 保持 `0`
- 流量采样：`93/96` 成功
  - 事件后恢复尾窗之外：`11/12` 成功

#### 关键观测

1. `worker1` 的 `ct_active_count` 在事件期明显抬升，满足“事件期峰值高于基线”的验收条件。
2. `worker2` 的 `ct_active_count` 维持为 `0`，与当前两节点拓扑下入口流量主要落在 `worker1` 的事实一致。
3. 恢复阶段只出现了 1 次尾部超时，未出现长时间持续不可达。

#### 判定

`conntrack_pressure` 通过。

### 3.2 `service_delete_recreate`

- 实验脚本：[service_delete_recreate.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/service_delete_recreate.sh:1)
- 实验 ID：`service-delete-recreate-20260523T084325Z`
- 产物目录：[artifacts/service-delete-recreate-20260523T084325Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/service-delete-recreate-20260523T084325Z)

#### 结果摘要

- `events.jsonl` 写入 1 条 `service_reconcile`
  - `ts_start_unix_ms=1779525818914`
  - `ts_end_unix_ms=1779525822073`
  - `recovery_tail_ms=5000`
- `worker1` CSV：`18` 条数据行
  - 标签集合：`normal`、`service_reconcile`
  - `recovery_active=1` 窗口数：`5`
- `worker2` CSV：`17` 条数据行
  - 标签集合：`normal`、`service_reconcile`
  - `recovery_active=1` 窗口数：`5`
- 流量采样：`60/63` 成功
  - 事件后恢复尾窗之外：`5/5` 成功

#### 关键观测

1. 本轮已按设计把 Service 当前状态和恢复用 manifest 一并保存：
   - [service-live.yaml](/home/ubuntu/wjy/ebpf_nodeport/artifacts/service-delete-recreate-20260523T084325Z/service-live.yaml)
   - [service-restore.yaml](/home/ubuntu/wjy/ebpf_nodeport/artifacts/service-delete-recreate-20260523T084325Z/service-restore.yaml)
2. 删除与重建期间允许出现短暂失败，但恢复尾窗结束后流量恢复为连续成功。
3. `5000ms` 的恢复尾窗足够覆盖 Service 对象重新建立后的同步窗口。

#### 判定

`service_delete_recreate` 通过。

### 3.3 `endpointslice_churn`

- 实验脚本：[endpointslice_churn.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/endpointslice_churn.sh:1)
- 实验 ID：`endpointslice-churn-20260523T084408Z`
- 产物目录：[artifacts/endpointslice-churn-20260523T084408Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/endpointslice-churn-20260523T084408Z)

#### 结果摘要

- `events.jsonl` 写入 1 条 `backend_churn`
  - `ts_start_unix_ms=1779525862469`
  - `ts_end_unix_ms=1779525937001`
  - `recovery_tail_ms=3000`
- `worker1` CSV：`89` 条数据行
  - 标签集合：`normal`、`backend_churn`
  - `recovery_active=1` 窗口数：`3`
- `worker2` CSV：`88` 条数据行
  - 标签集合：`normal`、`backend_churn`
  - `recovery_active=1` 窗口数：`3`
- 流量采样：`222/239` 成功
  - 事件后恢复尾窗之外：`9/10` 成功

#### 关键观测

1. [notes.txt](/home/ubuntu/wjy/ebpf_nodeport/artifacts/endpointslice-churn-20260523T084408Z/notes.txt:13) 到 [notes.txt](/home/ubuntu/wjy/ebpf_nodeport/artifacts/endpointslice-churn-20260523T084408Z/notes.txt:16) 记录了两轮 `2 -> 1 -> 2` 的扩缩容震荡。
2. 两个节点都稳定映射出 `backend_churn` 和 `recovery_active=1`。
3. 事件结束后，Deployment 已恢复为 `worker1` 和 `worker2` 各 1 个 backend 的目标拓扑。

#### 判定

`endpointslice_churn` 通过。

### 3.4 `traffic_burst`

- 实验脚本：[traffic_burst.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/traffic_burst.sh:1)
- 实验 ID：`traffic-burst-20260523T084558Z`
- 产物目录：[artifacts/traffic-burst-20260523T084558Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/traffic-burst-20260523T084558Z)

#### 结果摘要

- `events.jsonl` 写入 1 条 `load_surge`
  - `ts_start_unix_ms=1779525971366`
  - `ts_end_unix_ms=1779525986366`
  - `recovery_tail_ms=3000`
- `worker1` CSV：`30` 条数据行
  - 标签集合：`normal`、`load_surge`
  - `recovery_active=1` 窗口数：`3`
  - `ct_active_count` 峰值：基线 `5801` -> 事件期 `6560`
- `worker2` CSV：`30` 条数据行
  - 标签集合：`normal`、`load_surge`
  - `recovery_active=1` 窗口数：`3`
- 流量采样：`46/54` 成功
  - 事件前：`7/8`
  - 事件期：`23/28`
  - 事件后：`16/18`
  - 恢复尾窗之外：`13/14`

#### 关键观测

1. 脉冲流量期间出现了预期内的若干 `curl --max-time 2` 超时，但这正是该场景要制造的“受控负载突增”。
2. 事件后成功率回到与事件前基线相近的水平，没有出现恢复后持续恶化。
3. `worker1` 的 `ct_active_count` 峰值高于基线，说明流量突增对入口节点的 CT 压力可被 telemetry 捕获。

#### 判定

`traffic_burst` 通过。

## 4. Round 03 验收结论

本轮已完成并通过：

- `conntrack_pressure`
- `service_delete_recreate`
- `endpointslice_churn`
- `traffic_burst`

同时确认：

- 四个场景的 `events.jsonl`、`telemetry/*.csv`、`traffic.csv`、`agent-logs/*.log` 均已产出
- 两个节点的 CSV 都能映射出目标 `label`
- 两个节点的 CSV 都能映射出 `recovery_active=1`
- `conntrack_pressure` 和 `traffic_burst` 都能在 `worker1` 上观测到明显的 CT 压力抬升
- `service_delete_recreate` 和 `endpointslice_churn` 在恢复尾窗之后都回到了可继续下一轮实验的状态

因此本轮结论为：

**`Phase 2 Round 03` 通过。**

## 5. Round 03 结束时的运行态

Round 03 结束后再次检查集群状态：

- `ebpf-nodeport-agent`
  - `worker1`：`Running`
  - `worker2`：`Running`
- `nodeport-echo`
  - `worker1`：1 个 `Running` backend
  - `worker2`：1 个 `Running` backend

说明本轮脚本的清理逻辑和环境回滚链路均保持正常。

## 6. Phase 2 总结

随着 Round 01、Round 02、Round 03 全部通过，当前 `Phase 2` 已完成：

- 运行时对齐完成
- collector 自动生命周期完成
- `--clear-target-state` 接口完成
- 本计划新增的 7 个故障场景脚本已全部交付，既有 `backend_rollout_restart` 也继续保持可用

下一步可以从“工具链建设”切换到“数据集整理与 Phase 3 设计”。
