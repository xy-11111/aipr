# Phase 2 故障注入设计文档

## 1. 目标

`Phase 2` 的目标不是直接做模型训练，而是补齐一套可重复、可回收、可标注的故障注入工具链，为后续数据集构建和异常检测实验提供稳定输入。

本阶段输出应满足三件事：

1. 能稳定制造预定义异常场景
2. 能按统一格式生成 `events.jsonl`
3. 能与 `Phase 1` 的 telemetry CSV 直接对齐

## 2. 范围

本阶段聚焦“故障注入工具链”，明确不做：

- 在线推理
- 模型训练代码
- 多 Service 并发采集
- 新增按 Service 维度的 eBPF map
- Prometheus / remote write 集成

## 3. 与 Phase 1 的接口约定

`Phase 2` 必须复用 `Phase 1` 已经跑通的数据采集闭环，不重新发明新的实验入口。

### 3.1 基本约定

- 仍使用 `nodeport-syncer` 内置 telemetry collector
- 仍以单目标 Service 为实验对象
- 仍以 `experiment_id` 作为本轮实验的唯一标识
- 仍输出到：
  - `artifacts/<experiment_id>/meta.json`
  - `artifacts/<experiment_id>/events.jsonl`
  - `artifacts/<experiment_id>/notes.txt`
  - `artifacts/<experiment_id>/traffic.csv`
  - `artifacts/<experiment_id>/agent-logs/<node>.log`
  - `artifacts/<experiment_id>/telemetry/<node>.csv`

### 3.2 事件文件格式

`events.jsonl` 沿用以下 JSON Lines 结构：

```json
{
  "ts_start_unix_ms": 1779520982981,
  "ts_end_unix_ms": 1779521053702,
  "label": "backend_churn",
  "scope": "service",
  "target": "ebpf-nodeport-test/nodeport-echo",
  "recovery_tail_ms": 3000
}
```

字段约束如下：

- `ts_start_unix_ms`：注入动作正式开始的毫秒时间戳
- `ts_end_unix_ms`：注入动作结束的毫秒时间戳
- `label`：异常类别，必须来自预定义枚举
- `scope`：当前阶段统一为 `service`
- `target`：目标 Service，格式为 `<namespace>/<name>`
- `recovery_tail_ms`：恢复尾窗，供 `Phase 1` 的 labeler 标记 `recovery_active=1`

### 3.3 标签语义

- `label=normal`：窗口不在任何异常事件范围内
- `label=<异常类别>`：窗口落在事件时间范围内
- `recovery_active=1`：窗口不在主异常时间段内，但落在 `recovery_tail_ms` 覆盖范围内

## 4. 当前实验拓扑默认值

在基础设施恢复前，`Phase 2` 默认继承 `Phase 1 Round 03` 的两节点模式：

- `worker1`：固定 ingress 节点，也是一个 backend
- `worker2`：第二个 backend，同时为采样节点
- `worker3`：完全排除
- `master`：只保留控制面角色

默认目标：

- `TARGET_NODE_NAME=k8s-worker1`
- `TARGET_NODE_IP=192.168.1.202`
- `EXPERIMENT_NODE_NAMES=k8s-worker1,k8s-worker2`

只有在 `worker3` 和 `master` 的基础设施问题恢复后，才考虑扩大实验拓扑。

### 4.1 运行时对齐实现注记

`Phase 2` 的原始计划是：

- 本机 `docker build`
- `docker save`
- 通过 agent Pod 导入节点侧 `containerd`

实际落地时，本机无法直接访问 `docker.sock`，因此本阶段采用等价实现：

- 用 `/usr/local/go/bin/go` 重建 `bin/nodeport-agent` 和 `bin/nodeport-syncer`
- 通过现有 agent Pod `nsenter` 到宿主机，把二进制写入 `/opt/ebpf-nodeport/bin`
- 在 DaemonSet 中通过 hostPath 挂载 `/opt/ebpf-nodeport/bin -> /workspace/bin`
- 在节点本地把已有镜像 `docker.io/library/ebpf-nodeport-agent:snat-v1` 重新打 tag 为 `docker.io/library/ebpf-nodeport-agent:phase2-local`

对应准备脚本为 [prepare_phase2_runtime.sh](/home/ubuntu/wjy/ebpf_nodeport/scripts/prepare_phase2_runtime.sh:1)。

## 5. 故障场景矩阵

本阶段把故障注入分成两层：

- `Tier 1`：优先落地，直接服务第一版数据集
- `Tier 2`：在 `Tier 1` 稳定后补充

### 5.1 Tier 1 场景

| 场景名 | 标签 | 核心动作 | 目标 |
| --- | --- | --- | --- |
| `backend_rollout_restart` | `backend_churn` | 对目标 Deployment 执行 `rollout restart` | 制造 backend 集合短时变化 |
| `agent_restart` | `control_plane_recovery` | 重启指定节点上的 `ebpf-nodeport-agent` | 观察 collector/syncer 恢复过程 |
| `map_rebuild` | `control_plane_recovery` | 清空并重建目标 Service 对应 map 状态 | 验证同步链路恢复能力 |
| `path_degradation_netem` | `path_degradation` | 对远端路径注入 delay/loss | 制造跨节点路径退化 |
| `conntrack_pressure` | `conntrack_pressure` | 发送高并发短连接流量 | 压测 CT/GC 行为 |

### 5.2 Tier 2 场景

| 场景名 | 标签 | 核心动作 | 说明 |
| --- | --- | --- | --- |
| `service_delete_recreate` | `service_reconcile` | 删除并重建 NodePort Service | 风险较高，需更强保护 |
| `endpointslice_churn` | `backend_churn` | 高频 backend 扩缩容 | 用于更强烈的 backend 抖动数据 |
| `traffic_burst` | `load_surge` | 突发增加请求强度 | 与故障场景叠加时更有价值 |

## 6. 脚本目录与职责

建议在现有 `experiments/` 目录基础上继续扩展，不新增独立仓库。

### 6.1 目录建议

```text
experiments/
  common.sh
  normal_steady_state.sh
  backend_rollout_restart.sh
  agent_restart.sh
  map_rebuild.sh
  path_degradation_netem.sh
  conntrack_pressure.sh
  service_delete_recreate.sh
  endpointslice_churn.sh
  traffic_burst.sh
```

### 6.2 职责拆分

- `common.sh`
  - 代理绕过
  - preflight
  - artifact 初始化
  - telemetry 文件回收
  - agent 日志回收
  - collector 生命周期管理
  - `netem` / 压力流量 / targeted oneshot clear helper
  - CSV/traffic 基础校验
- 其余各场景脚本
  - 只负责本场景的注入动作、事件时间戳写入和场景级验收

## 7. 参数矩阵

`Phase 2` 的脚本统一接受环境变量参数，避免把实验条件写死在脚本内部。

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `EXPERIMENT_ID` | 自动生成 | 本轮实验唯一标识 |
| `TARGET_NAMESPACE` | `ebpf-nodeport-test` | 目标命名空间 |
| `TARGET_SERVICE` | `nodeport-echo` | 目标 Service |
| `TARGET_DEPLOYMENT` | `nodeport-echo` | 目标 Deployment |
| `TARGET_NODE_NAME` | `k8s-worker1` | 固定入口节点 |
| `TARGET_NODE_IP` | `192.168.1.202` | NodePort 打流地址 |
| `EXPERIMENT_NODE_NAMES` | `k8s-worker1,k8s-worker2` | 参与采样节点 |
| `ROLLOUT_TIMEOUT` | `180s` | rollout 等待超时 |
| `RECOVERY_TAIL_MS` | `3000` | 恢复尾窗，部分控制面恢复脚本会覆盖为 `5000` |
| `NETEM_DELAY_MS` | `100` | 路径退化延迟 |
| `NETEM_LOSS_PERCENT` | `5` | 路径退化丢包 |
| `TARGET_NETEM_IFACE` | `flannel.1` | `netem` 注入接口 |
| `PRESSURE_DURATION_SECONDS` | `15` | `conntrack_pressure` 注入时长 |
| `PRESSURE_WORKERS` | `20` | `conntrack_pressure` 并发 worker 数 |
| `BURST_DURATION_SECONDS` | `15` | `traffic_burst` 注入时长 |
| `BURST_WORKERS` | `40` | `traffic_burst` 并发 worker 数 |
| `SERVICE_RECREATE_DELAY_SECONDS` | `0` | `service_delete_recreate` 中 Service 删除后、重建前的显式等待时长 |

## 8. 事件标注与恢复尾窗规则

### 8.1 统一规则

每个注入脚本必须在动作发生前后记录：

- 动作起始时间
- 动作结束时间
- 目标对象
- 标签名
- `recovery_tail_ms`

### 8.2 推荐恢复尾窗

| 场景 | 建议 `recovery_tail_ms` | 备注 |
| --- | --- | --- |
| `backend_rollout_restart` | `3000` | 已在 Phase 1 验证 |
| `agent_restart` | `5000` | 给 syncer 重建留时间 |
| `map_rebuild` | `5000` | 需要覆盖重新同步窗口 |
| `path_degradation_netem` | `3000` | 退化撤销后的缓冲期 |
| `conntrack_pressure` | `3000` | 观察 GC 和计数回落 |
| `service_delete_recreate` | `5000` | 覆盖 Service 删除、重建与重新同步的恢复窗口 |

### 8.3 禁止事项

- 不允许在脚本失败时写入假的事件标签
- 不允许没有 `ts_end_unix_ms` 就把事件当作成功完成
- 不允许把多种异常合并成一条无结构事件

## 9. 安全边界与回滚

`Phase 2` 的核心要求之一是“实验失败也能恢复环境”。

### 9.1 通用安全边界

- 所有脚本必须先做 preflight
- 所有注入动作都必须注册 `trap` 清理逻辑
- 所有 `kubectl` 和 `curl` 必须走无代理 helper
- 所有实验只作用于 `EXPERIMENT_NODE_NAMES` 指定节点

### 9.2 分场景回滚要求

- `rollout restart`
  - 失败时等待 Deployment 回到 `Available=False/True` 的稳定态
- `agent_restart`
  - 失败时确认 agent Pod 恢复 `Running`
- `map_rebuild`
  - 失败时强制触发一次 syncer 全量 reconcile
- `path_degradation_netem`
  - 必须清除对应 qdisc
- `conntrack_pressure`
  - 必须停止压测流量进程

### 9.3 不应触碰的区域

当前阶段避免对以下对象做破坏性操作：

- `kube-system`
- `kube-apiserver`
- `worker3`
- `master` 上的测试流量与采样流程

## 10. Phase 2 的验证方式

每个新脚本至少经过两层验证：

### 10.1 基础验证

- shell 语法检查
- 参数缺省行为检查
- `events.jsonl` 结构检查
- 失败分支是否留下明确错误记录

### 10.2 集群验证

对每个场景至少验证：

1. 注入动作确实发生
2. `events.jsonl` 正确生成
3. `telemetry/<node>.csv` 中出现对应标签
4. `recovery_active=1` 出现在恢复尾窗
5. 流量没有因为脚本 bug 而永久中断

## 11. Phase 2 产出物

本阶段完成后，仓库里应至少新增：

- `agent_restart.sh`
- `map_rebuild.sh`
- `path_degradation_netem.sh`
- `conntrack_pressure.sh`
- 需要时补充 `helpers/` 下的辅助脚本
- 对应的中文测试记录文档

### 11.1 补采轮次约定

当 `Phase 3` 发现某些标签缺少独立的 `val/test` 覆盖时，`Phase 2` 允许回到已稳定的故障脚本上做“轻参数扰动补采”，但遵守以下规则：

1. 不新增标签语义，只在既有标签下补充实验轮次
2. 通过显式 `EXPERIMENT_ID` 保持 split 的字典序可预期
3. 只有通过验收的补采实验才加入数据集
4. `service_delete_recreate` 可通过 `SERVICE_RECREATE_DELAY_SECONDS` 控制删除与重建之间的空窗时长

## 12. 阶段完成判定

满足以下条件即可判定 `Phase 2` 完成：

1. 至少 `3` 个新增故障注入场景跑通
2. 每个场景都能产生有效 `events.jsonl`
3. 每个场景都能在至少两个节点的 CSV 中映射到正确标签
4. 每个场景都有独立中文测试记录
5. 失败注入后环境可恢复，不留下持续性污染

## 13. 下一步

`Phase 2` 文档落地后，执行顺序保持和 `Phase 1` 一致：

1. 先实现最小脚本
2. 跑测试
3. 写独立 md 记录
4. 有问题就回改文档和脚本
5. 通过后再进入下一阶段

当前最合适的起步顺序是：

1. `agent_restart.sh`
2. `path_degradation_netem.sh`
3. `conntrack_pressure.sh`

这三类场景和现有 `Phase 1` 数据链路衔接最自然，也最能尽快产出第一版可训练异常样本。
