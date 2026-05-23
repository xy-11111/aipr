# `ebpf_nodeport` AIPR Phase 1 测试记录 Round 02

## 1. 测试时间

- 测试日期：2026 年 5 月 22 日至 2026 年 5 月 23 日
- 主要测试时段：
  - `2026-05-22 15:50 UTC` 左右完成修复后二进制重构建与下发
  - `2026-05-22 15:52 UTC` 左右完成 `normal_steady_state`
  - `2026-05-22 15:54 UTC` 左右启动 `backend_rollout_restart`
  - `2026-05-23 06:25 UTC` 复核到 apiserver 健康检查异常

## 2. 本轮修复内容

### 2.1 文档修订

- 在 [aipr_phase1_implementation_design.md](/home/ubuntu/wjy/ebpf_nodeport/aipr_phase1_implementation_design.md:1) 中补充：
  - collector 单窗采样失败应降级跳过，而不是退出整个 syncer
  - `common.sh` 必须复用外部注入的 `EXPERIMENT_ID`
  - 实验脚本应在开始前做 cluster API 可用性预检查

### 2.2 代码修订

- `cmd/nodeport-syncer/conntrack_gc.go`
  - 为 `nodePortCTValue` 增加显式 `Pad [4]byte`
- `cmd/nodeport-syncer/telemetry.go`
  - 单窗采样或写出失败仅记录日志并跳过当前窗口
- `cmd/nodeport-syncer/conntrack_gc_test.go`
  - 增加 `nodePortCTValue` 大小校验测试
- `experiments/common.sh`
  - 保留外部 `EXPERIMENT_ID`
  - 事件同步仅作用于 `Running` pod
  - 增加 `require_cluster_api`

## 3. 本地验证

- `/usr/local/go/bin/go test ./...`：通过
- `clang -O2 -g -target bpf -D__TARGET_ARCH_x86 -I/usr/include/x86_64-linux-gnu -c nodeport_tc.c -o /tmp/nodeport_tc_phase1_round2.o`：通过
- `bash -n experiments/common.sh experiments/normal_steady_state.sh experiments/backend_rollout_restart.sh`：通过

## 4. 集群环境

- 仍沿用旧 DaemonSet 镜像，未完成整镜像升级
- 采用手动注入的新二进制：
  - `/workspace/bin/nodeport-syncer-phase1`
- 目标 agent pod：
  - `ebpf-nodeport-agent-jqrsh`
  - `ebpf-nodeport-agent-68rjl`
  - `ebpf-nodeport-agent-7jbq4`
- `k8s-worker3` 上的 agent pod 仍未进入 `Running`

## 5. 本轮 telemetry 配置

- `--telemetry-enable`
- `--telemetry-window=1s`
- `--telemetry-format=csv`
- `--telemetry-output=/var/log/ebpf-nodeport/telemetry`
- `--telemetry-events-file=/var/log/ebpf-nodeport/telemetry-events/current.jsonl`
- `--telemetry-service=ebpf-nodeport-test/nodeport-echo`
- `--ct-entry-timeout=60s`
- `--ct-gc-interval=10s`

## 6. 执行的实验脚本

### 6.1 `normal_steady_state`

- `experiment_id`: `normal-steady-state-20260522T155112Z`
- 本地 artifacts：
  - `artifacts/normal-steady-state-20260522T155112Z/meta.json`
  - `artifacts/normal-steady-state-20260522T155112Z/events.jsonl`
  - `artifacts/normal-steady-state-20260522T155112Z/notes.txt`

### 6.2 `backend_rollout_restart`

- `experiment_id`: `backend-rollout-restart-20260522T155326Z`
- 本地 artifacts：
  - `artifacts/backend-rollout-restart-20260522T155326Z/meta.json`
  - `artifacts/backend-rollout-restart-20260522T155326Z/events.jsonl`
  - `artifacts/backend-rollout-restart-20260522T155326Z/notes.txt`

## 7. 关键结果

### 7.1 `normal_steady_state` 成功完成

本地记录完整：

- `start_ms=1779465122126`
- `end_ms=1779465154030`

collector 输出目录与 experiment id 已对齐，说明 `EXPERIMENT_ID` 修复生效。

从节点内回读到的 CSV 情况如下：

- `k8s-master.csv`：`109` 行
- `k8s-worker1.csv`：`100` 行
- `k8s-worker2.csv`：`93` 行

3 个文件都包含完整 CSV header，且首条样本中：

- `experiment_id` 正确
- `label=normal`
- `label_source=derived`
- `anomaly_active=0`
- `recovery_active=0`

同时，Round 01 中出现的 CT map 反序列化错误在本轮未再出现，说明 padding 修复有效。

### 7.2 `backend_rollout_restart` 未正常完成

本地 `notes.txt` 只记录到：

- `rollout_restart_start_ms=1779465255133`

但没有写入：

- `rollout_restart_end_ms`
- `experiment_end_ms`

`events.jsonl` 为空，说明脚本没有走到 `append_event` 与 `sync_events_to_agents` 之后的完成路径。

## 8. 环境异常定位

在 `backend_rollout_restart` 卡住后，进一步检查发现 Kubernetes API server 已不健康。

### 8.1 现象

- 多个 `kubectl --request-timeout=10s ...` 请求返回：

```text
context deadline exceeded - error from a previous attempt: EOF
```

- 本机访问：

```text
curl -k --max-time 5 https://192.168.1.201:6443/readyz
```

超时无响应。

- 本机访问：

```text
curl -k --max-time 5 https://127.0.0.1:6443/livez
```

返回：

```text
curl: (35) error:0A000126:SSL routines::unexpected eof while reading
```

### 8.2 进程层面

主机上 `kube-apiserver` 与 `etcd` 进程仍存在：

- `kube-apiserver` 监听 `*:6443`
- `etcd` 监听 `127.0.0.1:2379` 与 `192.168.1.201:2379`

说明问题更像是控制面服务处于卡死或异常响应状态，而不是进程已经退出。

## 9. 通过项

- Round 01 的三个核心修复均已生效：
  - CT value padding 修复
  - 外部 `EXPERIMENT_ID` 保留
  - 非 Running pod 过滤
- `normal_steady_state` 闭环已跑通
- 3 个节点均成功生成 CSV
- CSV header 与 schema 对齐
- 单窗采样错误不会再直接拖垮 syncer 主循环

## 10. 未通过项

### P1. `backend_rollout_restart` 未完成

- 直接影响：
  - 无法验证 `backend_churn` 标签是否映射到窗口样本
  - 无法验证 backend 变化期间的统计波动

### P1. Kubernetes API server 不健康

- 直接影响：
  - rollout 状态无法可靠查询
  - event 标记流程无法继续完成
  - 后续任何依赖 `kubectl` 的 Phase 1 / Phase 2 实验都不具备稳定基础

## 11. 本轮结论

Round 02 达成了 **Phase 1 部分通过**：

- telemetry 代码骨架可运行
- 单目标 Service 的窗口化 CSV 输出闭环可用
- 正常场景数据采集可完成

但 **Phase 1 仍未整体通过**，因为关键验收项

- `backend_rollout_restart` 事件映射

尚未完成验证，且根本阻塞来自集群控制面异常，而不是当前 telemetry 实现本身。

## 12. 下一步建议

在继续 Phase 1 之前，先处理测试环境问题：

1. 恢复 `kube-apiserver` 健康状态
2. 重新执行 `backend_rollout_restart`
3. 回收 3 个节点的 rollout 实验 CSV
4. 验证 `events.jsonl -> backend_churn` 标签映射
5. 只有这一项通过后，才进入 `aipr_phase2_fault_injection_design.md`
