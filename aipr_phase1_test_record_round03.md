# Phase 1 Round 03 测试记录

## 1. 记录概览

- 测试时间：2026-05-23 07:21 UTC - 07:25 UTC
- 记录目的：在排除 `worker3` 和 `master` 的前提下，完成 `Phase 1` 的两节点降级验收
- 结论：`Phase 1` 两节点版本通过，可进入 `Phase 2` 设计

本轮不再等待以下基础设施问题恢复：

- `worker3`：新 Pod sandbox 创建失败，不参与调度与采样
- `master`：`ebpf-nodeport-agent` 仍为 `ImagePullBackOff`，不纳入本轮验收

## 2. 集群与版本信息

- Kubernetes API Server：`https://192.168.1.201:6443`
- 参与节点：
  - `k8s-worker1` (`192.168.1.202`)：固定 ingress 节点，同时承载 1 个 backend 和 1 个 telemetry collector
  - `k8s-worker2` (`192.168.1.203`)：承载第 2 个 backend 和 1 个 telemetry collector
- 排除节点：
  - `k8s-worker3`：不参与调度、不参与采样
  - `k8s-master`：仅保留控制面角色
- `ebpf-nodeport-agent` DaemonSet 镜像：`docker.io/library/ebpf-nodeport-agent:snat-v1`
- 测试 Deployment 镜像：`busybox:1.36`
- 本轮使用的 syncer 二进制：`/workspace/bin/nodeport-syncer-phase1`
  - 两个参与节点上的时间戳均为 `May 22 15:50`

## 3. 本轮执行前的调整

### 3.1 两节点拓扑固化

本轮已将 [nodeport-test.yaml](/home/ubuntu/wjy/ebpf_nodeport/manifests/nodeport-test.yaml:1) 收敛为实验专用拓扑：

- 仅允许 `nodeport-echo` 调度到 `k8s-worker1` 和 `k8s-worker2`
- Pod anti-affinity 改为硬约束，要求两个副本分布在不同 hostname
- rollout 策略调整为：
  - `maxSurge: 0`
  - `maxUnavailable: 1`

调整后的运行态如下：

- `worker1`：1 个 ready backend
- `worker2`：1 个 ready backend
- `worker3`：0 个相关 Pod
- `master`：0 个相关 Pod

### 3.2 实验脚本代理绕过

本轮所有 `kubectl` / `curl` 均通过 [common.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/common.sh:1) 内部 helper 显式清理以下代理变量：

- `HTTP_PROXY`
- `HTTPS_PROXY`
- `ALL_PROXY`
- `http_proxy`
- `https_proxy`
- `all_proxy`

这样后续实验不再依赖调用者手工设置 `NO_PROXY`。

### 3.3 本轮默认约定

- 目标 NodePort：`192.168.1.202:30080`
- 目标 Service：`ebpf-nodeport-test/nodeport-echo`
- 参与采样节点：`k8s-worker1,k8s-worker2`
- telemetry 输出格式：`CSV`
- telemetry 窗口：`1s`

## 4. 执行的实验

### 4.1 `normal_steady_state`

- 实验脚本：[normal_steady_state.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/normal_steady_state.sh:1)
- 实验 ID：`normal-steady-state-20260523T072104Z`
- 产物目录：[artifacts/normal-steady-state-20260523T072104Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/normal-steady-state-20260523T072104Z)

#### 结果摘要

- `worker1` CSV：`51` 条数据行
- `worker2` CSV：`42` 条数据行
- 两份 CSV 的 `label` 均为 `normal`
- 流量采样：`131/131` 成功，无失败样本

#### 产物检查

- [meta.json](/home/ubuntu/wjy/ebpf_nodeport/artifacts/normal-steady-state-20260523T072104Z/meta.json:1)
- [notes.txt](/home/ubuntu/wjy/ebpf_nodeport/artifacts/normal-steady-state-20260523T072104Z/notes.txt:1)
- [k8s-worker1.csv](/home/ubuntu/wjy/ebpf_nodeport/artifacts/normal-steady-state-20260523T072104Z/telemetry/k8s-worker1.csv:1)
- [k8s-worker2.csv](/home/ubuntu/wjy/ebpf_nodeport/artifacts/normal-steady-state-20260523T072104Z/telemetry/k8s-worker2.csv:1)
- [traffic.csv](/home/ubuntu/wjy/ebpf_nodeport/artifacts/normal-steady-state-20260523T072104Z/traffic.csv:1)

#### 判定

`normal_steady_state` 通过，满足以下条件：

- 两份 CSV 均成功生成
- 表头与 schema 一致
- 没有异常标签污染
- NodePort 流量连续可达

### 4.2 `backend_rollout_restart`

- 实验脚本：[backend_rollout_restart.sh](/home/ubuntu/wjy/ebpf_nodeport/experiments/backend_rollout_restart.sh:1)
- 实验 ID：`backend-rollout-restart-20260523T072229Z`
- 产物目录：[artifacts/backend-rollout-restart-20260523T072229Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/backend-rollout-restart-20260523T072229Z)

#### 结果摘要

- rollout 成功完成
- `events.jsonl` 写入 1 条 `backend_churn` 事件
  - `ts_start_unix_ms=1779520982981`
  - `ts_end_unix_ms=1779521053702`
  - `recovery_tail_ms=3000`
- `worker1` CSV：`103` 条数据行
  - `backend_churn` 窗口：`4`
  - `recovery_active=1` 窗口：`3`
- `worker2` CSV：`94` 条数据行
  - `backend_churn` 窗口：`2`
  - `recovery_active=1` 窗口：`2`
- 流量采样：`358/358` 成功，无失败样本

#### 关键观测

1. `backend_churn` 标签已在两份 CSV 中成功映射，不再依赖单节点观测。
2. `recovery_active=1` 已在 rollout 尾窗中出现，说明 `events.jsonl -> recovery tail` 的链路有效。
3. rollout 期间 backend 拓扑出现过短时变化，但实验结束后重新收敛为：
   - `worker1` 1 个 ready backend
   - `worker2` 1 个 ready backend
4. NodePort 流量未出现“完全不可访问”窗口。

#### 产物检查

- [meta.json](/home/ubuntu/wjy/ebpf_nodeport/artifacts/backend-rollout-restart-20260523T072229Z/meta.json:1)
- [events.jsonl](/home/ubuntu/wjy/ebpf_nodeport/artifacts/backend-rollout-restart-20260523T072229Z/events.jsonl:1)
- [notes.txt](/home/ubuntu/wjy/ebpf_nodeport/artifacts/backend-rollout-restart-20260523T072229Z/notes.txt:1)
- [k8s-worker1.csv](/home/ubuntu/wjy/ebpf_nodeport/artifacts/backend-rollout-restart-20260523T072229Z/telemetry/k8s-worker1.csv:1)
- [k8s-worker2.csv](/home/ubuntu/wjy/ebpf_nodeport/artifacts/backend-rollout-restart-20260523T072229Z/telemetry/k8s-worker2.csv:1)
- [traffic.csv](/home/ubuntu/wjy/ebpf_nodeport/artifacts/backend-rollout-restart-20260523T072229Z/traffic.csv:1)

#### 判定

`backend_rollout_restart` 通过，满足以下条件：

- rollout 在超时内完成
- `events.jsonl` 非空，且写入正确的 `backend_churn`
- 两个节点的 CSV 都出现了 `backend_churn`
- 两个节点的 CSV 都出现了 `recovery_active=1`
- rollout 期间流量持续可达

## 5. 验收结论

在“仅使用 `worker1+worker2`”的降级拓扑下，`Phase 1` 首轮剩余验收项已经补齐：

- `normal_steady_state` 通过
- `backend_rollout_restart` 通过
- `events.jsonl -> backend_churn` 标签映射通过
- `recovery_tail_ms -> recovery_active` 映射通过
- NodePort 主数据面未因 exporter 引入回退或失效

因此本轮结论为：

**`Phase 1` 两节点版本通过，可以进入 `Phase 2` 设计与实现。**

## 6. 仍然存在但不阻塞本轮的基础设施问题

- `worker3` 仍存在 Pod sandbox 创建问题，不纳入当前实验面
- `master` 上的 `ebpf-nodeport-agent` 仍为 `ImagePullBackOff`

这两个问题会影响未来扩展到三节点或全节点实验，但**不再阻塞当前 AIPR 路线的 `Phase 2`**。

## 7. 下一步

下一步进入 [aipr_phase2_fault_injection_design.md](/home/ubuntu/wjy/ebpf_nodeport/aipr_phase2_fault_injection_design.md:1) 的编写与落地，重点是：

- 扩展故障注入场景矩阵
- 规范事件标注和恢复尾窗
- 固化安全边界、清理逻辑和回滚方式
- 为后续数据集构建和异常检测模型训练准备更完整的实验工具链
