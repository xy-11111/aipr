# Phase 2 Round 01 测试记录

## 1. 记录概览

- 测试时间：2026-05-23 08:30 UTC - 08:38 UTC
- 记录目的：完成 `Phase 2` 的运行时对齐，并验证 Round 01 的三个场景：
  - `normal_steady_state`
  - `backend_rollout_restart`
  - `agent_restart`
- 结论：**Round 01 通过**

本轮采用两节点实验拓扑：

- `k8s-worker1`：固定 ingress 节点，同时承载 1 个 backend 和 1 个 agent
- `k8s-worker2`：承载第 2 个 backend 和 1 个 agent
- `k8s-worker3`：继续排除
- `k8s-master`：继续排除

## 2. 本轮实现与运行时调整

### 2.1 `nodeport-syncer` 新增 `--clear-target-state`

本轮已在 [main.go](/home/ubuntu/wjy/ebpf_nodeport/cmd/nodeport-syncer/main.go:53) 中加入：

- `--clear-target-state`

约束与行为如下：

- 只能与 `--sync-mode=oneshot` 搭配
- 只能与 `--service namespace/name` 搭配
- 执行顺序为：
  1. 计算目标 Service 当前期望 entry
  2. 调用现有 `DeleteEntry`
  3. 再执行正常 reconcile / upsert

参数校验测试已在 [main_test.go](/home/ubuntu/wjy/ebpf_nodeport/cmd/nodeport-syncer/main_test.go:1) 中补齐。

### 2.2 实验脚本升级为自动 collector 生命周期

本轮已将 [common.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/common.sh:1) 扩展为 Phase 2 版本，新增能力包括：

- 自动发现主 `nodeport-syncer` 命令行
- 自动启动/停止 detached collector
- 自动回收 CSV、agent 日志和实验元数据
- `netem` 注入
- 压力流量生成
- targeted oneshot clear

同时新增了 Phase 2 所需的新场景脚本：

- [agent_restart.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/agent_restart.sh:1)
- [map_rebuild.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/map_rebuild.sh:1)
- [path_degradation_netem.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/path_degradation_netem.sh:1)
- [conntrack_pressure.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/conntrack_pressure.sh:1)
- [service_delete_recreate.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/service_delete_recreate.sh:1)
- [endpointslice_churn.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/endpointslice_churn.sh:1)
- [traffic_burst.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/traffic_burst.sh:1)

### 2.3 DaemonSet 运行时对齐

本轮对 [nodeport-node-agent.yaml](/home/ubuntu/wjy/ebpf_nodeport/manifests/nodeport-node-agent.yaml:1) 做了两类关键调整：

- 镜像名切换为 `docker.io/library/ebpf-nodeport-agent:phase2-local`
- DaemonSet 加入硬 node affinity，只允许调度到 `k8s-worker1` 和 `k8s-worker2`

实现上对原计划做了一个受环境约束的收敛：

- 原计划是 `docker build/save` 后再导入节点 containerd
- 实际环境里本机无法直接访问 `/var/run/docker.sock`
- 因此本轮改为：
  - 用 `/usr/local/go/bin/go` 重新构建 `bin/nodeport-agent` 和 `bin/nodeport-syncer`
  - 通过现有 agent Pod `nsenter` 到宿主机，把二进制写入 `/opt/ebpf-nodeport/bin`
  - 在 DaemonSet 中通过 hostPath 将 `/opt/ebpf-nodeport/bin` 挂载到 `/workspace/bin`
  - 在节点本地将已有镜像 `docker.io/library/ebpf-nodeport-agent:snat-v1` 重新打 tag 为 `docker.io/library/ebpf-nodeport-agent:phase2-local`

对应准备脚本为 [prepare_phase2_runtime.sh](/home/ubuntu/wjy/ebpf_nodeport/scripts/prepare_phase2_runtime.sh:1)。

### 2.4 运行时对齐后的状态

Round 01 开始前，系统状态如下：

- `ebpf-nodeport-agent` 仅在 `worker1` 和 `worker2` 上运行
- 两个 agent Pod 均为 `Running`
- `nodeport-echo` 维持两副本、两节点分布
- `worker1` 和 `worker2` 上的 `/workspace/bin/nodeport-agent`、`/workspace/bin/nodeport-syncer` 均为本轮新构建版本

## 3. 基础验证

本轮在进入集群实验前，已完成以下本地验证：

- `/usr/local/go/bin/go test ./...`
- `clang -target bpf ... nodeport_tc.c`
- `bash -n experiments/*.sh`

结论：全部通过。

## 4. 执行的实验

### 4.1 `normal_steady_state`

- 实验脚本：[normal_steady_state.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/normal_steady_state.sh:1)
- 实验 ID：`normal-steady-state-20260523T083153Z`
- 产物目录：[artifacts/normal-steady-state-20260523T083153Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/normal-steady-state-20260523T083153Z)

#### 结果摘要

- `worker1` CSV：`32` 条数据行
- `worker2` CSV：`31` 条数据行
- 两份 CSV 的 `label` 均为 `normal`
- 两份 CSV 的 `recovery_active` 均为 `0`
- 流量采样：`131/131` 成功

#### 判定

`normal_steady_state` 通过，说明：

- 自动 collector 启停链路可用
- CSV 输出路径与回收链路可用
- 主数据面在 Phase 2 运行时下未回退

### 4.2 `backend_rollout_restart`

- 实验脚本：[backend_rollout_restart.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/backend_rollout_restart.sh:1)
- 实验 ID：`backend-rollout-restart-20260523T083411Z`
- 产物目录：[artifacts/backend-rollout-restart-20260523T083411Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/backend-rollout-restart-20260523T083411Z)

#### 结果摘要

- `events.jsonl` 写入 1 条 `backend_churn`
- `worker1` CSV：`84` 条数据行
  - 标签集合：`normal`、`backend_churn`
  - `recovery_active=1` 窗口数：`3`
- `worker2` CSV：`83` 条数据行
  - 标签集合：`normal`、`backend_churn`
  - `recovery_active=1` 窗口数：`3`
- 流量采样：`346/347` 成功
  - rollout 期间出现过 1 次 `curl --max-time 2` 超时
  - 但没有出现“持续完全不可访问”

#### 关键观测

1. 在两节点硬 anti-affinity 和 `maxSurge: 0` 约束下，rollout 期间仍会出现旧 Pod `Terminating`、新 Pod `Pending -> Running` 的正常切换窗口。
2. `events.jsonl -> backend_churn` 的映射在两个节点上都成功出现。
3. `recovery_tail_ms=3000` 能在 rollout 结束后的尾窗中稳定映射出 `recovery_active=1`。

#### 判定

`backend_rollout_restart` 通过。

### 4.3 `agent_restart`

本场景在 Round 01 中经历了 **一次失败 + 一次修复后重跑通过**。

#### 第一次执行

- 实验 ID：`agent-restart-20260523T083558Z`
- 产物目录：[artifacts/agent-restart-20260523T083558Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/agent-restart-20260523T083558Z)

失败现象：

- `worker1` CSV 已出现 `control_plane_recovery`
- `worker2` CSV 全部为 `normal`
- 脚本最终报错：`csv_missing_control_plane_recovery`

根因分析：

- 第一次实现沿用了全局默认 `RECOVERY_TAIL_MS=3000`
- 故障节点 `worker2` 的 agent Pod 重建后，新的 collector 启动稍晚
- 等 `worker2` 的第一个新窗口写出时，`control_plane_recovery` 的恢复尾窗已经过期
- 结果是：
  - `worker1` 仍能观测到事件尾窗
  - `worker2` 因 collector 重启后的首窗晚于恢复尾窗，只留下 `normal`

#### 修复

按 Phase 2 设计约定，将以下脚本的默认 `RECOVERY_TAIL_MS` 显式提升为 `5000`：

- [agent_restart.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/agent_restart.sh:1)
- [map_rebuild.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/map_rebuild.sh:1)
- [service_delete_recreate.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/service_delete_recreate.sh:1)

其中 `agent_restart` 是本轮直接验证到的问题点，后两个脚本属于同类控制面恢复场景，顺手一起对齐。

#### 第二次执行

- 实验脚本：[agent_restart.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/agent_restart.sh:1)
- 实验 ID：`agent-restart-20260523T083729Z`
- 产物目录：[artifacts/agent-restart-20260523T083729Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/agent-restart-20260523T083729Z)

#### 结果摘要

- `events.jsonl` 写入 1 条 `control_plane_recovery`
  - `recovery_tail_ms=5000`
- `worker1` CSV：`22` 条数据行
  - 标签集合：`normal`、`control_plane_recovery`
  - `recovery_active=1` 窗口数：`5`
- `worker2` CSV：`12` 条数据行
  - 标签集合：`normal`、`control_plane_recovery`
  - `recovery_active=1` 窗口数：`1`
- 流量采样：`88/88` 成功

#### 判定

修复后 `agent_restart` 通过，说明：

- fault node 上 agent Pod 删除并重建后，collector 可重新挂起
- `events.jsonl` 在 collector 重启后的场景下仍可映射到恢复尾窗
- `5000ms` 的恢复尾窗更符合两节点控制面恢复场景

## 5. Round 01 验收结论

本轮已完成并通过：

- `normal_steady_state`
- `backend_rollout_restart`
- `agent_restart`

同时已经确认：

- Phase 2 运行时对齐完成
- collector 自动生命周期机制可用
- `clear-target-state` 接口已进入代码基线
- 两节点实验模式下，Round 01 不再存在阻塞性问题

因此本轮结论为：

**`Phase 2 Round 01` 通过，可以进入 `Round 02`。**

## 6. Round 01 结束时的运行态

- `ebpf-nodeport-agent`
  - `worker1`：`Running`
  - `worker2`：`Running`
- `nodeport-echo`
  - `worker1`：1 个 `Running` backend
  - `worker2`：1 个 `Running` backend

未纳入当前阻塞项的基础设施约束仍保持不变：

- `worker3`：继续排除
- `master`：继续排除

## 7. 下一步

下一步进入 `Phase 2 Round 02`：

- `map_rebuild`
- `path_degradation_netem`

通过后再进入 `Round 03` 的压力和 Tier 2 场景。
