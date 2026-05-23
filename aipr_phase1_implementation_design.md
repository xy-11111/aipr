# `ebpf_nodeport` AIPR Phase 1 细化实施方案

## 1. 文档目的

这份文档是对 [aipr_anomaly_detection_plan.md](/home/ubuntu/wjy/ebpf_nodeport/aipr_anomaly_detection_plan.md:1) 中 `Phase 1` 和部分 `Phase 2` 的细化，重点回答三个实际开工前必须明确的问题：

1. 样本字段怎么定义
2. telemetry exporter 怎么设计
3. 第一批故障注入脚本准备做哪些

目标不是写论文文字，而是把后续代码实现和实验执行拆成可直接照着做的技术方案。

## 2. 关键约束与设计前提

在开始实现前，必须先接受当前 `ebpf_nodeport` 的几个现实约束。

### 2.1 当前 stats map 是“节点级”而不是“按 Service 级”

当前 `nodeport_stats_map` 是一个固定索引数组，记录的是整台节点上的累计统计，而不是按 `Service` 或 `frontend` 分开的统计。

这意味着：

- 如果同一节点同时跑多个 NodePort Service
- 且共享同一份 eBPF 程序与 stats map

那么直接读取到的 datapath 计数无法天然区分“是哪一个 Service 贡献的”。

### 2.2 第一版实验必须收敛到“单目标 Service”

为了不在第一阶段就改动 eBPF map 设计，第一版数据集构建建议使用：

- 单个目标 namespace/service
- 每次实验只针对一个固定 NodePort Service
- 每个节点每个窗口只输出一条样本

也就是说，第一版样本主键建议从：

`timestamp + node + service + nodePort`

收敛成：

`timestamp + node`

同时把 `service` 和 `nodePort` 当成样本元信息字段写入。

### 2.3 第一版 exporter 不建议单独起新 DaemonSet

当前最稳妥的工程方案不是再加一个新 Pod，而是：

- 将 telemetry collector 直接集成到现有 `nodeport-syncer`
- 由 `nodeport-agent` 继续负责统一启动

这样做的好处：

- 避免增加额外容器和权限配置
- 复用已有 pinned map 打开逻辑
- 复用已有 informer cache
- 更容易拿到 backend 数量和 K8s 事件上下文

### 2.4 当前实验环境默认收敛到“两节点模式”

如果集群中存在不稳定节点，第一版 Phase 1 验收允许先收敛到一个固定的两节点拓扑：

- `k8s-worker1`：固定 ingress 节点，也是一个 backend
- `k8s-worker2`：第二个 backend，也是 telemetry 采样节点
- `k8s-worker3`：不参与调度、不参与采样
- `k8s-master`：只保留控制面角色，不纳入本轮 telemetry 验收

在这种模式下，实验脚本需要：

- 固定流量入口 `192.168.1.202:30080`
- 只向 `worker1`、`worker2` 同步 `events.jsonl`
- 只回收 `worker1`、`worker2` 两份 CSV
- 在脚本内部自行绕过代理，不依赖操作者手工设置 `NO_PROXY`

## 3. 第一阶段总体实现思路

第一阶段的目标不是做模型，而是稳定产出训练样本。

推荐链路如下：

1. `nodeport-syncer` 内新增 telemetry collector goroutine
2. collector 按固定窗口读取：
   - stats map
   - CT/GC 状态
   - 当前 backend 状态
   - 最近一窗 K8s 事件标记
3. collector 将累计计数转换为窗口增量
4. collector 将样本写入 CSV 或 JSONL
5. 由实验脚本额外产出 `events.jsonl`
6. collector 或离线预处理程序将事件时间段映射为标签

## 4. 样本 schema 设计

## 4.1 样本单位

第一版样本单位：

- 固定窗口
- 单节点
- 单目标 Service

即每个窗口、每个节点输出一条样本。

## 4.2 字段分组

样本字段分为五类：

1. 基础元信息
2. 数据面计数增量
3. CT/GC 特征
4. Kubernetes 控制面上下文
5. 标签字段

## 4.3 推荐字段表

### A. 基础元信息

| 字段名 | 类型 | 说明 |
|---|---|---|
| `window_start_unix_ms` | int64 | 窗口开始时间 |
| `window_end_unix_ms` | int64 | 窗口结束时间 |
| `window_seconds` | float | 窗口长度，初始为 1 或 5 |
| `experiment_id` | string | 本轮实验 ID |
| `node_name` | string | 节点名 |
| `node_ip` | string | 节点 InternalIP |
| `service_namespace` | string | 目标 Service 命名空间 |
| `service_name` | string | 目标 Service 名称 |
| `service_nodeport` | int | NodePort 端口 |
| `routing_mode` | string | `native` 或 `encap` |
| `has_remote_backend` | int | 当前窗口内是否存在远端 backend，0/1 |

### B. 数据面增量特征

以下字段全部使用“窗口内增量”，而不是累计值：

| 字段名 | 类型 | 来源 |
|---|---|---|
| `delta_tcp_packets` | int64 | stats map |
| `delta_nodeport_hit` | int64 | stats map |
| `delta_backend_selected` | int64 | stats map |
| `delta_backend_lookup_miss` | int64 | stats map |
| `delta_rr_update` | int64 | stats map |
| `delta_snat_install` | int64 | stats map |
| `delta_request_rewrite` | int64 | stats map |
| `delta_revnat_hit` | int64 | stats map |
| `delta_ct_lookup_miss` | int64 | stats map |
| `delta_response_rewrite` | int64 | stats map |
| `delta_fwd_ct_hit` | int64 | stats map |
| `delta_new_conn` | int64 | stats map |
| `delta_map_miss` | int64 | stats map |
| `delta_rewrite_fail` | int64 | stats map |
| `delta_redirect_ok` | int64 | stats map |
| `delta_redirect_fail` | int64 | stats map |
| `delta_fallback_pass` | int64 | stats map |

### C. CT / GC 特征

| 字段名 | 类型 | 说明 |
|---|---|---|
| `ct_active_count` | int | 当前 CT 条目数 |
| `fwd_ct_active_count` | int | 当前 forward CT 条目数 |
| `gc_runs_in_window` | int | 当前窗口内 GC 触发次数 |
| `gc_deleted_ct` | int | 当前窗口内删除的 CT 条目数 |
| `gc_deleted_fwd_ct` | int | 当前窗口内删除的 forward CT 条目数 |
| `ct_entry_timeout_seconds` | int | 当前 CT 超时配置 |
| `ct_gc_interval_seconds` | int | 当前 GC 周期配置 |

### D. Kubernetes 控制面上下文特征

| 字段名 | 类型 | 说明 |
|---|---|---|
| `backend_total` | int | 当前窗口结束时 backend 总数 |
| `backend_local` | int | 当前本地 backend 数 |
| `backend_remote` | int | 当前远端 backend 数 |
| `backend_total_delta` | int | 相对上一窗口的 backend 数变化 |
| `service_event_seen` | int | 当前窗口是否观测到目标 Service 事件 |
| `slice_event_seen` | int | 当前窗口是否观测到目标 EndpointSlice 事件 |
| `node_event_seen` | int | 当前窗口是否观测到 Node 事件 |
| `sync_reconcile_count` | int | 当前窗口内 reconcile 次数 |
| `sync_upserted_services` | int | 当前窗口内 upsert 数 |
| `sync_removed_services` | int | 当前窗口内 removed 数 |

### E. 标签字段

| 字段名 | 类型 | 说明 |
|---|---|---|
| `label` | string | `normal` / 异常类别 |
| `label_source` | string | `manual`, `event_manifest`, `derived` |
| `anomaly_active` | int | 是否处于异常时段，0/1 |
| `recovery_active` | int | 是否处于恢复尾段，0/1 |

## 4.4 第一版推荐最小字段集

如果一开始不想字段太多，第一版最小可行集建议保留：

- 元信息：`window_start_unix_ms`, `window_end_unix_ms`, `experiment_id`, `node_name`, `service_name`, `service_nodeport`, `routing_mode`
- 数据面：`delta_nodeport_hit`, `delta_backend_selected`, `delta_new_conn`, `delta_fwd_ct_hit`, `delta_ct_lookup_miss`, `delta_map_miss`, `delta_rewrite_fail`, `delta_redirect_fail`, `delta_fallback_pass`
- CT/GC：`ct_active_count`, `fwd_ct_active_count`, `gc_deleted_ct`, `gc_deleted_fwd_ct`
- K8s 上下文：`backend_total`, `backend_local`, `backend_remote`, `service_event_seen`, `slice_event_seen`
- 标签：`label`, `anomaly_active`

## 4.5 CSV 示例

```csv
window_start_unix_ms,window_end_unix_ms,experiment_id,node_name,service_namespace,service_name,service_nodeport,routing_mode,delta_nodeport_hit,delta_backend_selected,delta_new_conn,delta_fwd_ct_hit,delta_ct_lookup_miss,delta_map_miss,delta_rewrite_fail,delta_redirect_fail,delta_fallback_pass,ct_active_count,fwd_ct_active_count,gc_deleted_ct,gc_deleted_fwd_ct,backend_total,backend_local,backend_remote,service_event_seen,slice_event_seen,label,anomaly_active
1715500000000,1715500001000,exp-rollout-001,k8s-worker1,ebpf-nodeport-test,nodeport-echo,30080,encap,120,8,8,112,0,0,0,0,0,34,34,0,0,2,1,1,0,1,backend_churn,1
```

## 5. 标签生成机制设计

第一版不建议让 exporter 自己“智能推断标签”，应采用实验驱动的确定性标签。

## 5.1 事件清单文件

每次实验由外部脚本生成一个事件文件，例如：

`artifacts/<experiment_id>/events.jsonl`

每条事件记录建议格式：

```json
{"ts_start_unix_ms":1715500000000,"ts_end_unix_ms":1715500008000,"label":"backend_churn","scope":"service","target":"ebpf-nodeport-test/nodeport-echo","recovery_tail_ms":3000}
```

## 5.2 标签映射规则

对每个窗口：

1. 若窗口与某个异常事件区间重叠，则 `anomaly_active=1`
2. 若窗口落在 `recovery_tail_ms` 范围内，则 `recovery_active=1`
3. `label` 优先使用异常标签
4. 若无异常命中，则 `label=normal`

## 6. Exporter 设计方案

## 6.1 实现位置建议

第一版 exporter 不新建独立二进制，建议直接加在：

- `cmd/nodeport-syncer/`

推荐文件组织：

- `cmd/nodeport-syncer/telemetry.go`
- `cmd/nodeport-syncer/telemetry_schema.go`
- `cmd/nodeport-syncer/telemetry_writer.go`
- `cmd/nodeport-syncer/telemetry_labeler.go`

这样做的理由：

- 可直接访问已打开的 pinned maps
- 可直接读取 syncer 当前掌握的 backend 状态
- 可直接使用 informer 缓存与 reconcile 状态
- 不引入新的部署角色

## 6.2 运行方式

在 `nodeport-syncer` 中增加一个 collector goroutine：

1. 周期性读取 stats map
2. 周期性读取 CT / forward CT 当前大小
3. 汇总最近一窗的 GC 删除数
4. 读取当前目标 Service 的 backend 状态
5. 读取最近一窗是否发生 Service / Slice / Node 事件
6. 生成一条窗口样本
7. 写入 CSV 或 JSONL

## 6.3 collector 与现有 syncer 的关系

syncer 负责：

- watch Service / Node / EndpointSlice
- 维护 service/backend/config maps
- 执行 CT GC

collector 负责：

- 周期采样
- 状态聚合
- 窗口化输出
- 对单窗采样失败进行降级处理，记录日志并跳过当前窗口，而不是退出整个 syncer

二者共享：

- pinned maps
- informer cache
- 当前目标 Service 信息
- 当前 backend 视图

## 6.4 推荐配置项

建议新增命令行参数：

- `--telemetry-enable`
- `--telemetry-window=1s`
- `--telemetry-format=csv|jsonl`
- `--telemetry-output=/var/log/ebpf-nodeport/telemetry.csv`
- `--telemetry-experiment-id=<id>`
- `--telemetry-events-file=<path>`
- `--telemetry-service=namespace/name`

对应的环境变量可以在 `nodeport-agent` 中透传：

- `NODEPORT_TELEMETRY_ENABLE`
- `NODEPORT_TELEMETRY_WINDOW`
- `NODEPORT_TELEMETRY_FORMAT`
- `NODEPORT_TELEMETRY_OUTPUT`
- `NODEPORT_TELEMETRY_EXPERIMENT_ID`
- `NODEPORT_TELEMETRY_EVENTS_FILE`

## 6.5 第一版输出路径建议

建议统一输出到：

- `/var/log/ebpf-nodeport/telemetry/<experiment_id>/<node_name>.csv`

如果在容器中运行，可通过 hostPath 或额外 volume 保存，便于回收。

## 6.6 exporter 关键内部状态

为了从累计计数生成窗口增量，collector 需要维护：

- 上一窗口 stats snapshot
- 当前窗口内 GC 聚合计数
- 当前窗口内事件标记
- 上一窗口 backend 数量

## 6.7 第一版不建议实现的 exporter 能力

为了控制复杂度，以下能力放到后面再说：

- Prometheus remote write
- 直接在线推理
- 多 Service 同时精细统计
- 自动滚动分片存储
- 高可用 exporter 集群化

## 7. 第一批故障注入脚本清单

第一批脚本目标不是全面，而是能尽快产出带标签数据。

## 7.1 目录建议

建议新增目录：

- `experiments/`

第一版脚本建议放在：

- `experiments/common.sh`
- `experiments/normal_steady_state.sh`
- `experiments/backend_rollout_restart.sh`
- `experiments/agent_restart.sh`
- `experiments/map_rebuild.sh`
- `experiments/path_degradation_netem.sh`
- `experiments/conntrack_pressure.sh`

## 7.2 `common.sh`

职责：

- 统一实验参数
- 在实验开始前做 cluster API 可用性预检查，避免 rollout 等场景在控制面异常时半途卡死
- 生成 `experiment_id`
- 如果外部已经注入 `EXPERIMENT_ID`，必须原样复用，保证实验脚本产物与 collector 输出目录一致
- 建立输出目录
- 记录开始/结束时间
- 写 `events.jsonl`
- 提供 kubectl、ssh、日志收集等公共函数

## 7.3 `normal_steady_state.sh`

目标：

- 采集正常数据 baseline

动作：

- 确保目标 Service 和 backend 稳定
- 启动固定强度流量
- 运行固定时长，例如 10~30 分钟
- 不注入异常

标签：

- 全部为 `normal`

## 7.4 `backend_rollout_restart.sh`

目标：

- 产生 `backend_churn` 类异常

动作：

- 对目标 Deployment 执行 `rollout restart`
- 记录 rollout 开始时间
- 记录 rollout 完成时间
- 采集 backend 变化期与恢复期

标签：

- backend 波动区间标记为 `backend_churn`

## 7.5 `agent_restart.sh`

目标：

- 产生 `control_plane_recovery` 类异常

动作：

- 删除目标节点上的 `ebpf-nodeport-agent` Pod
- 等待重建并恢复 Ready
- 记录中断与恢复时间

标签：

- agent 中断到恢复期间标记为 `control_plane_recovery`

## 7.6 `map_rebuild.sh`

目标：

- 产生更强的控制面恢复场景

动作：

- 在目标节点上清理 bpffs 下相关 pinned maps 或重建流程
- 触发 agent/syncer 恢复
- 记录重建完成时间

标签：

- 清理到恢复完成期间标记为 `control_plane_recovery`

说明：

这个脚本风险稍高，执行前应确保不影响其它实验环境。

## 7.7 `path_degradation_netem.sh`

目标：

- 产生 `path_degradation` 类异常

动作：

- 在本地路径或远端路径接口上注入：
  - delay
  - jitter
  - loss
- 每次只改变一个维度，便于解释

建议优先：

- 先从远端路径退化开始
- 再补本地路径退化

标签：

- 注入时段标记为 `path_degradation`

## 7.8 `conntrack_pressure.sh`

目标：

- 产生 `conntrack_pressure` 类异常

动作：

- 产生高频短连接
- 控制请求速率分层上升
- 拉高 CT 表增长与 GC 活跃度

标签：

- 高压力时段标记为 `conntrack_pressure`

## 7.9 每个实验脚本的统一输出

每个实验脚本都建议输出：

- `artifacts/<experiment_id>/meta.json`
- `artifacts/<experiment_id>/events.jsonl`
- `artifacts/<experiment_id>/notes.txt`

其中：

- `meta.json` 记录实验参数
- `events.jsonl` 记录标签事件
- `notes.txt` 记录人工备注

## 8. Phase 1 推荐实施顺序

建议按下面顺序动手：

1. 先明确“单目标 Service 实验模式”
2. 实现 exporter 样本结构和 CSV 写出
3. 实现 stats map 增量采样
4. 实现 CT/GC 聚合
5. 实现 backend 数量上下文导出
6. 实现事件文件标签映射
7. 跑 `normal_steady_state`
8. 跑 `backend_rollout_restart`
9. 再补 `agent_restart` 和 `path_degradation`

## 9. 第一阶段完成标准

第一阶段完成，不要求已经有模型结果，但至少要达到：

1. 可以稳定导出窗口化样本
2. 可以通过实验脚本生成带标签事件文件
3. 可以产出一版正常 + 异常混合数据集
4. 可以初步人工检查样本变化与异常事件是否对齐

## 10. 下一步建议

完成本文档对应的实现后，下一份最值得写的文档是：

- `baseline 模型实验设计`

内容可包括：

- 训练/验证/测试集划分
- baseline 模型选择理由
- 指标计算方式
- 消融实验设计
