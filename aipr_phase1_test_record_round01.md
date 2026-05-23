# `ebpf_nodeport` AIPR Phase 1 测试记录 Round 01

## 1. 测试时间

- 测试日期：2026 年 5 月 22 日
- 主要测试时段：约 `15:46 UTC` 到 `15:48 UTC`

## 2. 镜像 / 二进制版本

- 集群中原有 DaemonSet 镜像仍为旧版本：`docker.io/library/ebpf-nodeport-agent:snat-v1`
- 本轮未完成整镜像滚动发布
- 采用手动注入的新二进制进行验证：
  - `/workspace/bin/nodeport-syncer-phase1`
- 本地验证通过：
  - `/usr/local/go/bin/go test ./...`
  - `clang -O2 -g -target bpf -D__TARGET_ARCH_x86 -I/usr/include/x86_64-linux-gnu -c nodeport_tc.c`

## 3. 集群环境

- 节点：
  - `k8s-master` `192.168.1.201`
  - `k8s-worker1` `192.168.1.202`
  - `k8s-worker2` `192.168.1.203`
  - `k8s-worker3` `192.168.1.204`
- 系统命名空间：`ebpf-nodeport-system`
- 测试命名空间：`ebpf-nodeport-test`
- 目标 Service：`nodeport-echo`
- NodePort：`30080/TCP`
- backend Pod：
  - `k8s-master`
  - `k8s-worker2`
- 额外环境事实：
  - `ebpf-nodeport-agent-k75gs` 在 `k8s-worker3` 上长期处于 `ContainerCreating`

## 4. 本轮启用的 telemetry 配置

- `--telemetry-enable`
- `--telemetry-window=1s`
- `--telemetry-format=csv`
- `--telemetry-output=/var/log/ebpf-nodeport/telemetry`
- `--telemetry-events-file=/var/log/ebpf-nodeport/telemetry-events/current.jsonl`
- `--telemetry-service=ebpf-nodeport-test/nodeport-echo`
- `--ct-entry-timeout=60s`
- `--ct-gc-interval=10s`

## 5. 执行内容

### 5.1 代码与脚本准备

- 为 `nodeport-syncer` 增加 telemetry collector、CSV writer、labeler、schema
- 增加实验脚本：
  - `experiments/common.sh`
  - `experiments/normal_steady_state.sh`
  - `experiments/backend_rollout_restart.sh`

### 5.2 集群内执行方式

- 因旧镜像未替换，采用手动 `kubectl exec` 长连接方式启动 `nodeport-syncer-phase1`
- 在以下 3 个 pod 中尝试运行 telemetry：
  - `ebpf-nodeport-agent-jqrsh`
  - `ebpf-nodeport-agent-68rjl`
  - `ebpf-nodeport-agent-7jbq4`

### 5.3 实验脚本

- 尝试运行：
  - `normal_steady_state`

## 6. 关键日志与现象

### 6.1 CT map 迭代失败

在 `k8s-worker1` 与 `k8s-worker2` 上，telemetry syncer 很快退出，错误为：

```text
error: iterate conntrack map: look up next key: unmarshaling *main.nodePortCTValue doesn't consume all data
```

这说明用户态 `nodePortCTValue` 结构与 pinned BPF map 的 value 布局不一致。

### 6.2 experiment id 未按预期对齐

本轮原计划让 collector 和实验脚本共用同一个 `EXPERIMENT_ID`，但实际运行时：

- collector 使用手工传入的 `normal-steady-state-20260522T154647Z`
- 实验脚本最终产物写成了 `normal-steady-state-20260522T154715Z`

原因是 `experiments/common.sh` 中全局变量初始化把外部传入的 `EXPERIMENT_ID` 覆盖掉了。

### 6.3 事件同步脚本潜在阻塞

由于 `k8s-worker3` 的 agent pod 不是 `Running`，原始脚本如果对所有同标签 pod 执行 `kubectl exec`，会在 `sync_events_to_agents` 阶段引入不必要失败风险。

## 7. 通过项

- telemetry 主体代码已编译通过
- 本地 Go / BPF 基础验证通过
- `k8s-master` 上手动启动的新 syncer 可以完成初始同步
- 单目标 Service 约束与 collector 输出路径设计基本成立

## 8. 问题项

### P1. CT map value 结构缺少显式 padding

- 影响：worker1 / worker2 上 telemetry collector 直接退出
- 根因：`nodePortCTValue` 在 Go 侧未显式表示 BPF value 中的对齐填充

### P1. 实验脚本不保留外部 `EXPERIMENT_ID`

- 影响：artifacts 目录与 collector 输出目录不一致
- 根因：`EXPERIMENT_ID=""` 重置了外部环境变量

### P2. 事件同步未过滤非 Running agent pod

- 影响：在存在异常节点时，实验脚本稳定性下降
- 根因：`common.sh` 对同标签 pod 全量执行 `kubectl exec`

## 9. 本轮结论

Round 01 未通过，不能进入 Phase 2。

需要先完成以下修订：

1. 修正 `nodePortCTValue` 的布局，使其与 BPF map value 对齐
2. 修正脚本中的 `EXPERIMENT_ID` 透传逻辑
3. 将事件同步范围收敛到 `Running` pod
4. 完成代码修复后重新跑完整测试

## 10. 后续处理

本轮后已按回环规则进入修订：

1. 先更新 `aipr_phase1_implementation_design.md`
2. 再修改脚本与 Go 代码
3. 准备执行 Round 02
