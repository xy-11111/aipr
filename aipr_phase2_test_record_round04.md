# Phase 2 Round 04 补采记录

## 1. 记录概览

- 测试时间：2026-05-23 11:37 UTC - 11:47 UTC
- 记录目的：
  - 为 `Phase 3 Dataset v1` 补齐覆盖不足标签的独立实验轮次
  - 只补 `conntrack_pressure`、`path_degradation`、`service_reconcile`、`load_surge` 的 `val/test`
  - 额外补 1 轮 `control_plane_recovery` 的 `val`
- 结论：**Round 04 通过**

本轮继续沿用两节点实验拓扑：

- `k8s-worker1`
- `k8s-worker2`

`worker3` 与 `master` 继续排除，不作为本轮阻塞项。

## 2. 本轮新增变更

### 2.1 场景脚本补强

已在 [service_delete_recreate.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/service_delete_recreate.sh:1) 增加：

- `SERVICE_RECREATE_DELAY_SECONDS`

行为为：

- `wait_service_absent` 成功后
- `kubectl apply` 重建前
- 显式 `sleep SERVICE_RECREATE_DELAY_SECONDS`

这使得 `service_reconcile` 可以用轻度参数扰动稳定地产生 `val/test` 轮次。

### 2.2 运行前检查

本轮开始前已确认：

- `ebpf-nodeport-agent` 在 `worker1`、`worker2` 均为 `Running`
- `nodeport-echo` 在 `worker1`、`worker2` 各有 1 个 `Running` backend
- 补采前未发现 `netem` 遗留 qdisc

## 3. 补采执行顺序与结果

本轮按既定顺序执行 9 个实验，且全部通过。

### 3.1 `control_plane_recovery` 补 `val`

- 脚本：[agent_restart.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/agent_restart.sh:1)
- 实验 ID：`agent-restart-20260523T113700Z`
- 产物目录：[artifacts/agent-restart-20260523T113700Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/agent-restart-20260523T113700Z)
- 参数：
  - `FAULT_NODE_NAME=k8s-worker1`
  - `AGENT_READY_TIMEOUT_SECONDS=180`
  - `RECOVERY_TAIL_MS=5000`

结果摘要：

- `events.jsonl`：`control_plane_recovery`
- `worker1` CSV：`13` 行，`recovery_active=1` 共 `1` 行
- `worker2` CSV：`22` 行，`recovery_active=1` 共 `4` 行
- 流量采样：`89/89` 成功

判定：通过，可作为 `control_plane_recovery` 的 `val` 轮次。

### 3.2 `conntrack_pressure` 补 `val/test`

#### `val`

- 脚本：[conntrack_pressure.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/conntrack_pressure.sh:1)
- 实验 ID：`conntrack-pressure-20260523T113900Z`
- 产物目录：[artifacts/conntrack-pressure-20260523T113900Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/conntrack-pressure-20260523T113900Z)
- 参数：
  - `PRESSURE_WORKERS=16`
  - `PRESSURE_DURATION_SECONDS=12`
  - `RECOVERY_TAIL_MS=3000`

结果摘要：

- `worker1` CSV：`26` 行，`ct_active_count` 峰值 `9019`
- `worker2` CSV：`25` 行
- 两节点均出现 `conntrack_pressure` 与 `recovery_active=1`
- 流量采样：`107/107` 成功

判定：通过。

#### `test`

- 实验 ID：`conntrack-pressure-20260523T114000Z`
- 产物目录：[artifacts/conntrack-pressure-20260523T114000Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/conntrack-pressure-20260523T114000Z)
- 参数：
  - `PRESSURE_WORKERS=24`
  - `PRESSURE_DURATION_SECONDS=18`
  - `RECOVERY_TAIL_MS=3000`

结果摘要：

- `worker1` CSV：`32` 行，`ct_active_count` 峰值 `13229`
- `worker2` CSV：`31` 行
- 两节点均出现 `conntrack_pressure` 与 `recovery_active=1`
- 流量采样：`131/131` 成功

判定：通过。

### 3.3 `load_surge` 补 `val/test`

#### `val`

- 脚本：[traffic_burst.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/traffic_burst.sh:1)
- 实验 ID：`traffic-burst-20260523T114100Z`
- 产物目录：[artifacts/traffic-burst-20260523T114100Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/traffic-burst-20260523T114100Z)
- 参数：
  - `BURST_WORKERS=32`
  - `BURST_DURATION_SECONDS=12`
  - `RECOVERY_TAIL_MS=3000`

结果摘要：

- `worker1` CSV：`27` 行，`ct_active_count` 峰值 `13845`
- `worker2` CSV：`26` 行
- 两节点均出现 `load_surge` 与 `recovery_active=1`
- 流量采样：`104/107` 成功

判定：通过。

#### `test`

- 实验 ID：`traffic-burst-20260523T114200Z`
- 产物目录：[artifacts/traffic-burst-20260523T114200Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/traffic-burst-20260523T114200Z)
- 参数：
  - `BURST_WORKERS=48`
  - `BURST_DURATION_SECONDS=18`
  - `RECOVERY_TAIL_MS=3000`

结果摘要：

- `worker1` CSV：`33` 行，`ct_active_count` 峰值 `14065`
- `worker2` CSV：`32` 行
- 两节点均出现 `load_surge` 与 `recovery_active=1`
- 流量采样：`125/128` 成功

判定：通过。

### 3.4 `path_degradation` 补 `val/test`

#### `val`

- 脚本：[path_degradation_netem.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/path_degradation_netem.sh:1)
- 实验 ID：`path-degradation-netem-20260523T114300Z`
- 产物目录：[artifacts/path-degradation-netem-20260523T114300Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/path-degradation-netem-20260523T114300Z)
- 参数：
  - `NETEM_DELAY_MS=80`
  - `NETEM_LOSS_PERCENT=3`
  - `INJECTION_DURATION_SECONDS=12`
  - `RECOVERY_TAIL_MS=3000`

结果摘要：

- `worker1` CSV：`28` 行
- `worker2` CSV：`27` 行
- 两节点均出现 `path_degradation` 与 `recovery_active=1`
- 流量采样：`93/98` 成功

判定：通过。

#### `test`

- 实验 ID：`path-degradation-netem-20260523T114400Z`
- 产物目录：[artifacts/path-degradation-netem-20260523T114400Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/path-degradation-netem-20260523T114400Z)
- 参数：
  - `NETEM_DELAY_MS=120`
  - `NETEM_LOSS_PERCENT=7`
  - `INJECTION_DURATION_SECONDS=18`
  - `RECOVERY_TAIL_MS=3000`

结果摘要：

- `worker1` CSV：`34` 行
- `worker2` CSV：`33` 行
- 两节点均出现 `path_degradation` 与 `recovery_active=1`
- 流量采样：`101/103` 成功

判定：通过。

### 3.5 `service_reconcile` 补 `val/test`

#### `val`

- 脚本：[service_delete_recreate.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/service_delete_recreate.sh:1)
- 实验 ID：`service-delete-recreate-20260523T114500Z`
- 产物目录：[artifacts/service-delete-recreate-20260523T114500Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/service-delete-recreate-20260523T114500Z)
- 参数：
  - `SERVICE_RECREATE_DELAY_SECONDS=1`
  - `RECOVERY_TAIL_MS=5000`

结果摘要：

- `worker1` CSV：`20` 行，`recovery_active=1` 共 `5` 行
- `worker2` CSV：`19` 行，`recovery_active=1` 共 `5` 行
- 流量采样：`64/77` 成功
- 已产出：
  - [service-live.yaml](/home/ubuntu/wjy/ebpf_nodeport/artifacts/service-delete-recreate-20260523T114500Z/service-live.yaml)
  - [service-restore.yaml](/home/ubuntu/wjy/ebpf_nodeport/artifacts/service-delete-recreate-20260523T114500Z/service-restore.yaml)

判定：通过。

#### `test`

- 实验 ID：`service-delete-recreate-20260523T114600Z`
- 产物目录：[artifacts/service-delete-recreate-20260523T114600Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/service-delete-recreate-20260523T114600Z)
- 参数：
  - `SERVICE_RECREATE_DELAY_SECONDS=3`
  - `RECOVERY_TAIL_MS=5000`

结果摘要：

- `worker1` CSV：`22` 行，`recovery_active=1` 共 `5` 行
- `worker2` CSV：`21` 行，`recovery_active=1` 共 `4` 行
- 流量采样：`67/85` 成功
- 已产出：
  - [service-live.yaml](/home/ubuntu/wjy/ebpf_nodeport/artifacts/service-delete-recreate-20260523T114600Z/service-live.yaml)
  - [service-restore.yaml](/home/ubuntu/wjy/ebpf_nodeport/artifacts/service-delete-recreate-20260523T114600Z/service-restore.yaml)

判定：通过。

## 4. Round 04 验收结论

本轮 9 个补采实验全部通过，满足：

- `artifacts/<experiment_id>/` 产物完整
- `events.jsonl` 标签正确
- `worker1`、`worker2` 两份 CSV 均生成
- 两份 CSV 均出现目标 `label`
- 两份 CSV 均出现 `recovery_active=1`
- `path_degradation_netem` 未留下 `netem` 遗留状态
- `service_delete_recreate` 均产出 `service-live.yaml` 与 `service-restore.yaml`

因此本轮结论为：

**`Phase 2 Round 04` 通过。**

## 5. Round 04 结束时的运行态

本轮结束后再次检查：

- `ebpf-nodeport-agent`
  - `worker1`：`Running`
  - `worker2`：`Running`
- `nodeport-echo`
  - `worker1`：1 个 `Running` backend
  - `worker2`：1 个 `Running` backend
- `worker1` 的 `flannel.1` 未残留 `netem` qdisc，只保留：
  - `noqueue`
  - `clsact`

说明补采脚本的清理逻辑和环境回滚链路仍然保持正常。

## 6. 下一步

Round 04 的直接目标已经完成，下一步应进入：

1. 更新 `experiment_selection.json`
2. 刷新 `datasets/phase3_v1`
3. 生成新的 `dataset_summary.md` 与 `quality_report.md`
4. 记录 `Phase 3 Round 02`
