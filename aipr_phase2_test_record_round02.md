# Phase 2 Round 02 测试记录

## 1. 记录概览

- 测试时间：2026-05-23 08:39 UTC - 08:41 UTC
- 记录目的：验证 `Phase 2` Round 02 的两个场景：
  - `map_rebuild`
  - `path_degradation_netem`
- 结论：**Round 02 通过**

本轮延续 Round 01 的两节点实验拓扑：

- `k8s-worker1`
- `k8s-worker2`

`worker3` 和 `master` 继续排除，不纳入阻塞项。

## 2. Round 02 前置状态

进入本轮前，系统处于以下稳定状态：

- `ebpf-nodeport-agent`
  - `worker1`：`Running`
  - `worker2`：`Running`
- `nodeport-echo`
  - `worker1`：1 个 `Running` backend
  - `worker2`：1 个 `Running` backend

Round 01 中为 `agent_restart` 补上的 `RECOVERY_TAIL_MS=5000` 默认值保持生效，`map_rebuild` 也因此继续沿用 `5000ms` 的恢复尾窗。

## 3. 执行的实验

### 3.1 `map_rebuild`

- 实验脚本：[map_rebuild.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/map_rebuild.sh:1)
- 实验 ID：`map-rebuild-20260523T083956Z`
- 产物目录：[artifacts/map-rebuild-20260523T083956Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/map-rebuild-20260523T083956Z)

#### 结果摘要

- `events.jsonl` 写入 1 条 `control_plane_recovery`
  - `ts_start_unix_ms=1779525610224`
  - `ts_end_unix_ms=1779525611887`
  - `recovery_tail_ms=5000`
- `worker1` CSV：`16` 条数据行
  - 标签集合：`normal`、`control_plane_recovery`
  - `recovery_active=1` 窗口数：`5`
- `worker2` CSV：`15` 条数据行
  - 标签集合：`normal`、`control_plane_recovery`
  - `recovery_active=1` 窗口数：`5`
- 流量采样：`62/62` 成功

#### 关键观测

1. `run_targeted_oneshot_clear` 已成功调用带 `--clear-target-state` 的 `oneshot` syncer。
2. 本轮 `oneshot-clear.log` 已被写入：
   - [oneshot-clear.log](/home/ubuntu/wjy/ebpf_nodeport/artifacts/map-rebuild-20260523T083956Z/notes.txt:11)
3. 两个节点都能观察到 `control_plane_recovery` 和 `recovery_active=1`，说明 control-plane 级恢复事件在非 agent 重启场景下同样可稳定映射。
4. `map_rebuild` 没有破坏 NodePort 基本可达性。

#### 判定

`map_rebuild` 通过。

### 3.2 `path_degradation_netem`

- 实验脚本：[path_degradation_netem.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/path_degradation_netem.sh:1)
- 实验 ID：`path-degradation-netem-20260523T084038Z`
- 产物目录：[artifacts/path-degradation-netem-20260523T084038Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/path-degradation-netem-20260523T084038Z)

#### 结果摘要

- `events.jsonl` 写入 1 条 `path_degradation`
  - `ts_start_unix_ms=1779525651849`
  - `ts_end_unix_ms=1779525666849`
  - `recovery_tail_ms=3000`
- `worker1` CSV：`31` 条数据行
  - 标签集合：`normal`、`path_degradation`
  - `recovery_active=1` 窗口数：`3`
- `worker2` CSV：`30` 条数据行
  - 标签集合：`normal`、`path_degradation`
  - `recovery_active=1` 窗口数：`3`
- 流量采样：`94/94` 成功

#### 关键观测

1. 注入接口为 `flannel.1`，记录在 [notes.txt](/home/ubuntu/wjy/ebpf_nodeport/artifacts/path-degradation-netem-20260523T084038Z/notes.txt:6)。
2. 注入结束后，通过 `tc qdisc show dev flannel.1` 复核，接口恢复为默认状态：
   - `qdisc noqueue`
   - `qdisc clsact`
3. 说明本轮 `tc qdisc del dev flannel.1 root` 清理成功，没有把 `netem` 残留在实验环境里。
4. 在当前 `delay/loss` 参数下，NodePort 流量仍保持连续成功，说明这组更像“受控路径退化”而不是“强破坏”。

#### 判定

`path_degradation_netem` 通过。

## 4. Round 02 验收结论

本轮已完成并通过：

- `map_rebuild`
- `path_degradation_netem`

同时确认：

- `--clear-target-state` 路径可用
- `oneshot` 重建目标 Service 状态不会破坏主数据面
- `netem` 注入与清理链路可用
- 两个节点都能稳定映射 `path_degradation` 与 `control_plane_recovery`

因此本轮结论为：

**`Phase 2 Round 02` 通过，可以进入 `Round 03`。**

## 5. 下一步

下一步进入 `Phase 2 Round 03`，顺序为：

- `conntrack_pressure`
- `service_delete_recreate`
- `endpointslice_churn`
- `traffic_burst`
