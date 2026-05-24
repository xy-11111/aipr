# `ebpf_nodeport` 面向 AIPR 的异常检测论文方案

## 1. 总体目标

将 `ebpf_nodeport` 从“纯 eBPF NodePort 快路径原型”重新定位为一篇更贴近 AIPR 风格的模式识别论文，核心方向为：

`基于 eBPF 遥测的 Kubernetes NodePort 服务异常检测`

论文目标不再只是“做一个更快的 NodePort 数据面”，而是：

- 从 eBPF 数据面和 Kubernetes 控制面采集低开销遥测数据
- 构造带标签的 NodePort 异常数据集
- 评估轻量模式识别模型能否准确、及时地识别服务异常
- 证明这套检测链路在真实 Kubernetes 环境中具有可部署性

## 2. 为什么走这个方向

当前代码库已经具备比较完整的系统基础：

- `nodeport_tc.c` 中已经有可工作的 eBPF NodePort 数据面
- 已有 Go 版本的 per-node agent 和 syncer
- 已有按角色探测网络环境的逻辑
- 已有基于 informer 的事件驱动控制面
- 已有连接跟踪和 CT 垃圾回收
- 已有较细粒度的数据面统计项

这意味着当前仓库天然适合作为：

- 遥测数据来源
- 故障注入实验平台
- Kubernetes 服务观测与复现实验底座

相比把项目直接扩成“AI 驱动的智能转发系统”，异常检测是更低风险、也更容易对齐 AIPR 口味的方向。

## 3. 论文定位

### 3.1 候选标题

`基于 eBPF 遥测的 Kubernetes NodePort 服务异常检测`

英文可用：

`eBPF Telemetry-Based Anomaly Detection for Kubernetes NodePort Services`

### 3.2 核心研究问题

低开销的 eBPF 数据面遥测，结合 Kubernetes 控制面事件，能否在不显著增加运行时开销的前提下，对 NodePort 服务异常进行准确且及时的识别？

### 3.3 预期贡献

1. 提出一套基于 eBPF 数据面遥测与 Kubernetes 事件的 NodePort 异常检测框架。
2. 构建一套通过可控故障注入得到的 NodePort 异常数据集。
3. 系统评估检测准确率、检测时延与运行时开销，并分析其工程可行性。

## 3.4 当前进度（截至 2026-05-24）

- `Phase 1`：已完成
  - 完成 telemetry collector MVP、CSV 导出、事件标签映射和最小实验闭环。
- `Phase 2`：已完成
  - 完成故障注入工具链，并通过多轮实验补齐核心异常场景。
- `Phase 3`：已完成
  - 已构建并冻结 `datasets/phase3_v1`，当前数据集覆盖 `train/val/test`。
- `Phase 4`：已完成
  - 已完成二分类与多分类 baseline，结果已冻结在 `results/phase4_baselines_v1/`。
- `Phase 5`：已完成
  - 已完成 `telemetry off/on x local/remote backend` 的最小 `2x2` 开销评估矩阵。
  - `Round 01` 中对 local-only 路径的“不支持”判断已在 `Round 02` 中纠正；当前结论为：在 `ETP=Cluster` 前提下，local-only backend 路径可工作，但对拓扑切换后的收敛时机更敏感。
- `Phase 6`：已完成
  - 已完成 `6` 轮正常稳态泛化与 `10` 轮异常泛化实验，并构建 `datasets/phase6_generalization_v1/`。
  - 已完成固定 `Phase 4` 最优模型的跨负载、跨拓扑泛化推理与 `paper_assets/phase6_v1/` 表图导出。
  - 已完成 `all_features / minus_datapath_stats / minus_ct_gc / minus_k8s_events / minus_topology_backend` 五组特征消融，结论显示 `datapath_stats` 是最关键的特征组。
  - 结果表明：Phase 4 固定模型在 Phase 6 新负载/新拓扑下存在明显误报与多分类退化，后续论文应将其作为“泛化边界”而不是回避。
- `Phase 7`：已完成中文初稿
  - 已完成中文完整初稿 `paper_draft/phase7_v1/paper_zh.md`，用于内部审阅和论证链打磨。
  - 已完成图表索引 `paper_draft/phase7_v1/asset_map.md`，将 `paper_assets/phase4_v1`、`paper_assets/phase5_v1`、`paper_assets/phase6_v1` 的核心表图映射到论文各节。
  - 已完成 `aipr_phase7_writing_record_round01.md`，明确本阶段不是最终投稿稿，下一阶段进入英文化与 AIPR 模板化。

## 4. 现有仓库中可直接复用的部分

### 4.1 数据面遥测统计

来自 `nodeport_tc.c` 的统计项可直接作为特征基础，包括但不限于：

- `tcp_packets`
- `nodeport_hit`
- `backend_selected`
- `backend_lookup_miss`
- `rr_update`
- `snat_install`
- `request_rewrite`
- `revnat_hit`
- `ct_lookup_miss`
- `response_rewrite`
- `fwd_ct_hit`
- `new_conn`
- `map_miss`
- `rewrite_fail`
- `redirect_ok`
- `redirect_fail`
- `fallback_pass`

### 4.2 连接生命周期与 GC 信号

来自 syncer 的 CT/GC 逻辑，可导出如下信息：

- 当前 CT 表大小
- 当前 forward CT 表大小
- 每轮 GC 删除的 CT 条目数
- 每轮 GC 删除的 forward CT 条目数
- GC 周期与超时配置

### 4.3 Kubernetes 控制面上下文

来自 informer 控制面的上下文信息：

- Service 的新增、更新、删除事件
- EndpointSlice 的变化
- backend 数量变化
- 本地 backend 与远端 backend 的构成

### 4.4 网络拓扑与角色信息

来自环境探测逻辑的上下文：

- external interface
- local delivery interface
- remote delivery interface
- routing mode（`native` / `encap`）

## 5. 任务定义

第一篇论文只做一个任务：

`NodePort 服务异常检测`

不要在第一篇里同时做：

- 智能选路
- 智能 backend 调度
- 强化学习控制

否则问题会变散，审稿时也更难讲清楚。

### 5.1 标签空间

建议先采用粗粒度标签：

- `normal`：正常
- `backend_churn`：backend 波动/替换
- `control_plane_recovery`：控制面恢复过程
- `path_degradation`：路径退化
- `conntrack_pressure`：连接跟踪压力异常

后续如果效果稳定，再细化为更细粒度的子类别。

### 5.2 样本粒度

每条样本用固定时间窗口聚合：

- 推荐初始窗口：`1s`
- 如果实现复杂度偏高，可先用 `5s`

样本主键建议为：

`timestamp + node + service + nodePort`

## 6. 数据采集方案

### 6.1 特征组设计

#### A. eBPF 数据面统计特征

- 直接读取 stats map 中的累计计数
- 在用户态转换为“窗口内增量”

#### B. CT 与 GC 特征

- CT 表大小
- forward CT 表大小
- 当前窗口删除的 CT 条目数
- 当前窗口删除的 forward CT 条目数
- 当前窗口 GC 执行次数

#### C. Kubernetes 控制面特征

- backend 总数
- backend 数量变化量
- 本地 backend 数量
- 远端 backend 数量
- 当前窗口内 Service 是否变化
- 当前窗口内 EndpointSlice 是否变化

#### D. 拓扑与路径特征

- routing mode
- 本地/远端 backend 比例
- 当前 service 是否涉及远端路径

#### E. 可选质量特征

- 请求成功率
- 连接失败数
- 超时数
- 若增加主动探测客户端，可加入时延统计

### 6.2 导出格式

第一版推荐：

- `CSV`：便于快速实验
- `JSONL`：便于保留更丰富的上下文

第一版建议样本格式：

`timestamp,node,service,nodeport,label,<feature columns...>`

## 7. 故障注入与标注方案

异常数据集需要通过可控实验构造，而不是被动等待异常发生。

### 7.1 场景集合

#### 场景 1：正常稳态

- backend 集合稳定
- 流量稳定
- 采集 baseline 数据

#### 场景 2：backend 波动

- `deployment rollout restart`
- Pod 重建
- backend 替换事件

#### 场景 3：控制面恢复

- 重启 `nodeport-agent`
- 清空后重建 pinned maps
- 观测恢复窗口

#### 场景 4：路径退化

- 使用 `tc netem` 注入 delay / loss
- 本地 backend 与远端 backend 分开跑

#### 场景 5：连接跟踪压力

- 生成高频短连接
- 人为拉高 CT 增长与 GC 活跃度

### 7.2 标签生成策略

用实验编排事件作为真值锚点：

- 注入故障的时间段标成异常
- 必要时在故障结束后额外保留一个短恢复尾巴
- 第一版标签逻辑保持简单、确定、可复现

## 8. 模型方案

先从简单基线开始，不依赖复杂模型撑论文。

### 8.1 基线模型

1. 基于阈值的规则方法
2. Logistic Regression
3. Random Forest
4. XGBoost 或 LightGBM

### 8.2 轻量时序模型

在基线有效后，再尝试：

1. LSTM
2. 1D-CNN
3. TCN

不建议一上来就上重模型，否则论文会显得“系统和模型两头都不够深”。

## 9. 评估方案

### 9.1 检测效果

- Precision
- Recall
- F1-score
- AUROC
- 混淆矩阵

### 9.2 检测时效性

- 异常发生后的检测延迟
- 恢复阶段的识别滞后

### 9.3 运行时开销

- throughput
- p50 latency
- p99 latency
- 节点 CPU 使用率
- agent CPU 使用率
- 内存开销

### 9.4 泛化与鲁棒性

- 仅本地 backend 场景
- 涉及远端 backend 场景
- 不同流量强度
- 不同异常频率
- 最好再补一个不同网络环境

## 10. 第一篇论文的非目标

为了控制范围，以下内容先不作为主目标：

- AI 驱动的 backend 调度
- 强化学习
- 以 UDP/IPv6 支持作为论文成立前提
- 以 DSR/hybrid 支持作为论文成立前提
- 多集群广泛泛化

这些可以在论文里写成 future work，但不应阻塞第一篇稿子。

## 11. 分阶段执行计划

### Phase 0：冻结范围

目标：

- 明确论文任务就是异常检测，而不是智能转发

任务：

- 确认最终任务定义
- 确认标签集合
- 确认最小必要特征集

交付物：

- 一页问题定义说明

退出标准：

- 论文目标不再摇摆

### Phase 1：遥测导出器

目标：

- 从当前 NodePort 系统中稳定导出窗口化样本

任务：

- 增加 Go 版本 telemetry exporter 或 collector
- 将累计计数转换为窗口增量
- 导出 CT/GC 与 backend 数量特征
- 将样本写入 CSV 或 JSONL

交付物：

- exporter 二进制或模块
- 第一份正常流量样本数据

退出标准：

- 至少成功采集 1 小时正常数据

### Phase 2：故障注入工具链

目标：

- 建立可重复的异常场景生成能力

任务：

- 编写 rollout restart 实验脚本
- 编写 agent restart 实验脚本
- 编写 map rebuild 实验脚本
- 编写 `tc netem` 路径退化脚本
- 编写 CT 压力生成脚本

交付物：

- 一组可重复执行的故障注入脚本
- 带时间戳的场景记录清单

退出标准：

- 每类异常都能被稳定触发并记录

### Phase 3：数据集构建

目标：

- 形成第一版带标签异常数据集

任务：

- 多轮运行所有场景
- 平衡正常与异常样本
- 划分训练/验证/测试集
- 记录采集环境、集群拓扑与实验参数

交付物：

- 数据集 v1
- 数据集说明文档

退出标准：

- 数据集足够支撑第一轮机器学习实验

### Phase 4：基线模型验证

目标：

- 验证这些遥测特征是否真能识别异常

任务：

- 训练规则方法与经典机器学习基线
- 比较主要检测指标
- 观察特征重要性

交付物：

- baseline 指标表
- 第一版混淆矩阵
- 特征重要性结论

退出标准：

- 至少有一个轻量 baseline 明显优于朴素规则

### Phase 5：系统开销评估

目标：

- 证明检测链路具备在线可部署性

任务：

- 测量 exporter 打开前后的 throughput 和 latency
- 测量节点与 agent CPU 开销
- 对比“仅系统”与“系统 + 采集”的成本差异

交付物：

- 开销评估表
- 吞吐/时延图

退出标准：

- 开销足够低，不至于否定在线部署价值

### Phase 6：泛化与消融

目标：

- 提升论文说服力，减少“只在单一环境有效”的质疑

任务：

- 对比 local backend 与 remote backend 场景
- 对比不同流量强度
- 做特征组消融实验
- 如果条件允许，增加第二种网络环境验证

交付物：

- 消融实验表
- 泛化实验表

退出标准：

- 主要结论不依赖单一窄场景

### Phase 7：论文写作

目标：

- 产出第一版可内部审阅的中文完整论文初稿

任务：

- 已写引言与问题定义
- 已写系统与遥测采集设计
- 已写数据集构建方法
- 已写检测结果、开销评估、泛化与消融分析
- 已写讨论、局限性与未来工作
- 已整理 Phase 4/5/6 图表索引

交付物：

- `paper_draft/phase7_v1/paper_zh.md`
- `paper_draft/phase7_v1/asset_map.md`
- `aipr_phase7_writing_record_round01.md`

退出标准：

- 达到可内部审阅状态

下一阶段：

- 将中文初稿英文化，并按 AIPR full paper 模板整理为最终投稿稿。
- 补齐正式 BibTeX、英文摘要、图表排版和至少 8 页正文的投稿要求。

## 12. 建议里程碑顺序

建议按以下顺序推进：

1. 冻结范围
2. 实现 exporter
3. 建立故障注入脚本
4. 构建数据集 v1
5. 跑 baseline 模型
6. 做系统开销评估
7. 做泛化与消融
8. 写论文初稿

## 13. 主要风险与缓解方式

### 风险 1：标签质量弱

缓解：

- 用脚本化故障注入保证时间边界清晰
- 初期保持粗粒度标签

### 风险 2：遥测特征区分度不够

缓解：

- 将数据面统计与 K8s 事件特征结合
- 必要时补充简单主动探测特征

### 风险 3：在线开销过高

缓解：

- 先使用较粗时间窗口
- 在用户态计算增量
- 保持 exporter 逻辑轻量

### 风险 4：论文过于偏系统，不够 AIPR

缓解：

- 叙事重心始终放在“模式识别问题”
- 让数据集、模型对比、检测指标成为核心评估

## 14. 下一步立刻要做的事

接下来最具体的工作应该是：

1. 定义样本字段 schema
2. 设计 telemetry exporter 接口
3. 列出第一批故障注入脚本清单
4. 先产出数据集 v1，再考虑复杂模型

## 15. 成功标准

如果最终能产出以下结果，这条路线就算成功：

- 一套带标签的 NodePort 异常数据集
- 一个或多个轻量模型能取得较好的异常检测效果
- 检测链路的运行时开销可接受
- 论文叙事能够清晰对齐 AIPR 风格的模式识别评估
