# NodePort SNAT v1 测试记录（2026-05-11）

## 目标

在 `/home/ubuntu/wjy/ebpf_nodeport` 建立独立 NodePort 实验目录，不影响 `/home/ubuntu/wjy/o` 里的 ClusterIP connect4 实验。第一版只验证：

- IPv4/TCP
- NodePort Service
- `externalTrafficPolicy=Cluster`
- 入口节点 SNAT
- tc ingress/egress 数据面
- kube-proxy 保留为 miss fallback

目标数据路径：

```text
client -> NodeIP:NodePort
  -> enp1s0 ingress tc
  -> NodePort service/backend map
  -> RR 选择 backend
  -> DNAT 到 PodIP:targetPort
  -> SNAT 到入口节点 cni0 IP:SNAT_PORT
  -> request-side CT 固定同一连接 backend/SNAT
  -> redirect 到 cni0 或 flannel.1

backend -> cni0/flannel.1 ingress tc
  -> revNAT CT
  -> 恢复 src=NodeIP:NodePort, dst=clientIP:clientPort
  -> redirect_neigh 到 enp1s0
```

## 实现内容

- 新建目录：`/home/ubuntu/wjy/ebpf_nodeport`。
- 保留基线目录 `/home/ubuntu/wjy/o` 未改动。
- 新增数据面程序：`nodeport_tc.c`。
- 新增脚本：
  - `scripts/attach_nodeport_tc.sh`
  - `scripts/cleanup_nodeport_tc.sh`
  - `scripts/run_nodeport_agent.sh`
  - `scripts/sync_nodeport_maps.py`
  - `scripts/update_nodeport_map.sh`
  - `scripts/dump_nodeport_stats_map.py`
- 新增 manifest：
  - `manifests/nodeport-node-agent.yaml`
  - `manifests/nodeport-test.yaml`
- 镜像：`docker.io/library/ebpf-nodeport-agent:snat-v1`
- 2026-05-11 基线镜像 digest：`sha256:02e61974f8bf2f04eab87e43131d0fe15edc4a82738deca5116553c217238f25`

## 关键修正

- `HTTPS_PROXY/HTTP_PROXY` 会影响本机 `kubectl/curl` 访问 apiserver，测试命令统一使用去代理环境。
- 仅在 `enp1s0 ingress` 改写请求后放行不够稳定，后续补充 request-side `bpf_redirect_neigh()`：
  - 本地 backend redirect 到 `cni0`
  - 远端 backend redirect 到 `flannel.1`
- 仅做 revNAT 后 `bpf_redirect()` 会保留错误二层头，tcpdump 混杂模式能看到包，但客户端协议栈不接收；改为 `bpf_redirect_neigh()`。
- 只维护 revNAT CT 不够，SYN 后续 ACK/HTTP GET 会重新 RR，导致后端 RST；新增 `nodeport_fwd_ct_map`，同一连接固定 backend/SNAT。
- attach 时写入 `nodeport_config_map`：
  - `outer_ifindex=enp1s0`
  - `inner_ifindex=cni0`
  - `tunnel_ifindex=flannel.1`
- attach 时打开相关接口 `accept_local=1`，允许 tc 改写后使用本机 SNAT 源地址继续转发。

## 2026-05-11 部署状态

测试对象：

```text
namespace: ebpf-nodeport-test
service: nodeport-echo
type: NodePort
nodePort: 30080
port: 80
targetPort: 8080
```

后端 Pod：

```text
nodeport-echo-d9b549479-klqn7   10.244.1.8    k8s-worker1
nodeport-echo-d9b549479-tnr5t   10.244.0.63   k8s-master
```

agent Pod：

```text
ebpf-nodeport-agent-r4v8r   192.168.1.202   k8s-worker1
ebpf-nodeport-agent-vnsls   192.168.1.201   k8s-master
ebpf-nodeport-agent-x5hf8   192.168.1.203   k8s-worker2
```

## 2026-05-11 最终验证

验证命令均去掉代理环境变量：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u http_proxy -u https_proxy -u all_proxy \
    NO_PROXY='*' curl -sS --connect-timeout 3 http://<NodeIP>:30080/
```

结果：

```text
master -> worker1(192.168.1.202:30080)
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t

master -> worker2(192.168.1.203:30080)
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t

worker1 -> master(192.168.1.201:30080)
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t
```

最终 stats 摘要：

```text
k8s-master:
nodeport_hit=30
backend_selected=6
snat_install=6
request_rewrite=30
revnat_hit=30
response_rewrite=30

k8s-worker1:
nodeport_hit=30
backend_selected=6
snat_install=6
request_rewrite=30
revnat_hit=30
response_rewrite=30

k8s-worker2:
nodeport_hit=30
backend_selected=6
snat_install=6
request_rewrite=30
revnat_hit=30
response_rewrite=30
```

解释：每个入口节点 6 个 HTTP 连接，每个连接包含多个 TCP 包，因此 `request_rewrite/revnat_hit/response_rewrite` 高于连接数是正常的。

## 2026-05-11 结论

NodePort SNAT v1 已完成端到端验证：

- 三个节点的 `NodeIP:30080` frontend 都能命中 eBPF。
- 两个后端 Pod 都能被 RR 选中。
- 本地 backend 和远端 flannel backend 都能完成请求和回包 NAT。
- `request_rewrite`、`revnat_hit`、`response_rewrite` 均按预期增长。
- kube-proxy 仍保留，eBPF miss 时不破坏原生 fallback。

## 后续建议

- 增加 CT 老化和删除逻辑，避免 fwd/rev CT map 长期堆积。
- 细化 stats：区分新连接、fwd CT hit、request redirect、response redirect、checksum error、redirect miss。
- 支持 Pod/veth 变化后的动态 reattach 或完全不依赖 veth。
- 后续再扩展 UDP、IPv6、`externalTrafficPolicy=Local`、DSR、sessionAffinity。

## 向 Cilium 风格演进的清单

这一版已经不是“只能改三四层、完全放给内核去猜后续路径”的原型了，但它仍然明显带有 flannel 路径假设。下面按“从当前版本继续演进”的角度整理下一步。

### 阶段 1：把固定设备名改成设备角色

当前实现直接依赖：

- `enp1s0`
- `cni0`
- `flannel.1`

在 `attach_nodeport_tc.sh` 和 `nodeport_config_map` 里，这三个设备已经被进一步抽象成：

- `outer_ifindex`
- `inner_ifindex`
- `tunnel_ifindex`

这是第一步，但还不够。下一步应继续把“设备名”彻底从数据面逻辑里拿掉，只保留“角色”：

- `external-facing device`
- `local delivery device`
- `remote delivery device`
- 以后可扩展 `direct-routing device`

目标是：数据面不关心设备实际叫 `cni0` 还是别的名字，只依赖设备角色。

### 阶段 2：把 backend 转发策略独立出来

当前 request path 的核心判断是：

- backend 在本节点：redirect 到 `cni0`
- backend 在远端节点：redirect 到 `flannel.1`

更像 Cilium 的做法应该是给 backend 单独维护 forwarding policy，而不是在 tc 程序里直接写本地/远端分支。backend metadata 至少应包含：

- backend IP
- backend port
- backend 所在 node IP
- backend 是否本地
- 到 backend 应该使用的 forwarding mode
- 对应的 egress ifindex

这样以后才能自然扩展到：

- flannel 隧道
- native routing
- DSR
- 不同设备角色下的同一套 service LB 逻辑

### 阶段 3：把 routing mode 变成一等配置

当前实现本质上是：

```text
NodePort + SNAT + tc + flannel encapsulation path
```

更像 Cilium 的方向是把 routing mode 单独建模，而不是让它隐含在 `cni0/flannel.1` 这组设备里。建议显式支持：

- `encap`
- `native`
- 预留 `dsr`

控制面根据 routing mode 决定：

- backend egress 设备
- 是否需要 SNAT
- 回包从哪里做 revNAT
- 是否需要 tunnel metadata / neigh redirect

这样 NodePort 逻辑本身就不会和某一种 CNI 数据路径绑死。

### 阶段 4：把 Service 模型统一起来

当前 map 还是“NodePort v1 最小可用集”：

- `nodeport_service_map`
- `nodeport_backend_map`
- `nodeport_rr_state_map`
- `nodeport_ct_map`
- `nodeport_fwd_ct_map`

如果继续做下去，更像 Cilium 的方向不是再为每种 service type 复制一套 map，而是统一为一套 service model。建议抽象出：

- service frontend 类型：`ClusterIP / NodePort / ExternalIP / HostPort`
- backend 集合
- traffic policy：`externalTrafficPolicy / internalTrafficPolicy`
- service flags
- revNAT / service ID
- affinity / 算法

这样 NodePort 和 ClusterIP 才能逐步合并为同一套 LB 数据模型，而不是两套独立实验代码。

### 阶段 5：把 hook 选择也抽象出来

当前这版 hook 点是：

- request：`enp1s0 ingress tc`
- local/remote backend request redirect：`cni0` 或 `flannel.1`
- response：`cni0/flannel.1 ingress tc`
- response return：`redirect_neigh(enp1s0)`

这已经能跑通 NodePort SNAT，但更像 Cilium 的做法是把 hook 点视为“能力层”，而不是实现细节。未来可按流量类型拆开：

- `cgroup/connect` 更适合 ClusterIP
- `tc ingress/egress` 适合 NodePort
- `XDP` 适合更早的 fast path
- host path / endpoint path 分开考虑

这样以后 NodePort、ClusterIP、HostPort 不必共用完全同一种挂载方式。

### 阶段 6：把控制面适配层从 flannel 假设里解耦

当前 `sync_nodeport_maps.py` 已经做了：

- watch Service / EndpointSlice / Node
- 只给本节点写 frontend
- 根据 endpoint `nodeName` 补全 backend node IP

但它仍然默认：

- SNAT IP 从 `cni0` 获取
- `NODEPORT_INNER_IFACES=cni0,flannel.1`

如果要更像 Cilium，控制面应拆成两层：

1. Kubernetes 对象收集层  
负责生成抽象 service/backend 信息

2. 节点网络能力发现层  
负责识别：
- 外网入口设备
- 本地 endpoint delivery 设备
- 远端 tunnel / direct-routing 设备
- routing mode
- 本机可用于 SNAT / host-routing 的地址

这样换 CNI 时，改的是“节点网络能力发现层”，不是整套 NodePort LB。

### 建议的实际落地顺序

如果按当前代码基础继续推进，建议顺序如下：

1. 先把 `cni0/flannel.1/enp1s0` 从配置名字升级为“自动发现 + 角色绑定”。
2. 再把 backend 的 forwarding policy 单独建模，不要在 `nodeport_tc.c` 里写死本地/远端。
3. 然后把 routing mode 显式化，至少区分 `encap` 和 `native`。
4. 接着把 NodePort 和 ClusterIP 的 service/backend 数据模型合并。
5. 最后再考虑把不同 service type 分配到不同 hook 层。

### 当前版本的准确定位

所以更准确的定位不是：

```text
与 CNI 无关的通用 NodePort eBPF 实现
```

而是：

```text
面向 flannel 路径验证通过的 NodePort SNAT eBPF 实现，
并且已经开始向“按设备角色和 forwarding policy 抽象”的方向演进
```

## 2026-05-12 控制面探测重构与回归

这次调整的目标是继续向 Cilium 的组织方式靠拢：

- agent 控制面启动时探测节点环境
- 控制面把探测结果写成少量 config/map
- eBPF 数据面只读取最终结果，而不是在包路径里判断当前 CNI 是什么

### 本次重构点

- 新增 `scripts/detect_nodeport_environment.py`：
  - 探测 `external_iface`
  - 探测 `local_delivery_iface`
  - 探测 `remote_delivery_iface`
  - 推导 `routing_mode=encap|native`
- 新增 `scripts/update_nodeport_config_map.sh`，专门负责写 `nodeport_config_map`。
- `scripts/attach_nodeport_tc.sh` 不再负责写 config map，只负责：
  - 编译和挂载 tc 程序
  - pin 程序和 maps
  - 清理旧 tc filter
- `scripts/run_nodeport_agent.sh` 启动时先解析 delivery profile，再把结果传给 syncer。
- `scripts/sync_nodeport_maps.py` 每轮同步前先比较 delivery profile：
  - 如果环境变化，先更新 `nodeport_config_map`
  - 如果 Service/EndpointSlice 变化，再更新 service/backend maps
- `nodeport_tc.c` 的配置结构改为只认设备角色：
  - `external_ifindex`
  - `local_delivery_ifindex`
  - `remote_delivery_ifindex`
  - `routing_mode`
- `manifests/nodeport-node-agent.yaml` 里把显式接口配置留空，改为默认自动探测。

### 当前数据面只读的最小信息

```text
service/backend map
forward CT map
reverse CT map
delivery config map:
  external_ifindex
  local_delivery_ifindex
  remote_delivery_ifindex
  routing_mode
```

### 2026-05-12 当前环境探测结果

三台节点自动探测出的 delivery profile 如下：

```text
k8s-master:
  external=enp1s0
  local=cni0
  remote=flannel.1
  routing_mode=encap
  config: external_ifindex=2 local_delivery_ifindex=6 remote_delivery_ifindex=5 routing_mode=1

k8s-worker1:
  external=enp1s0
  local=cni0
  remote=flannel.1
  routing_mode=encap
  config: external_ifindex=2 local_delivery_ifindex=5 remote_delivery_ifindex=4 routing_mode=1

k8s-worker2:
  external=enp1s0
  local=cni0
  remote=flannel.1
  routing_mode=encap
  config: external_ifindex=2 local_delivery_ifindex=5 remote_delivery_ifindex=4 routing_mode=1
```

说明：

- 设备名字在三台节点上逻辑一致，但 ifindex 不要求完全相同。
- 数据面只依赖 ifindex 和 `routing_mode`，不直接依赖固定设备名。
- 当前集群仍然是 flannel 封装路径，因此 `routing_mode=encap`。

### 本次回归使用的镜像与对象

- 镜像：`docker.io/library/ebpf-nodeport-agent:snat-v1`
- 本次重构后的镜像 digest：`sha256:bc17a0a29185a323f4549cff4c46e2d428708c3b3070763daccca64b5f538d74`

当前后端 Pod：

```text
nodeport-echo-d9b549479-tnr5t   10.244.0.64   k8s-master
nodeport-echo-d9b549479-klqn7   10.244.1.89   k8s-worker1
```

当前 agent Pod：

```text
ebpf-nodeport-agent-2d7hw   192.168.1.201   k8s-master
ebpf-nodeport-agent-jd9s2   192.168.1.202   k8s-worker1
ebpf-nodeport-agent-2t5s2   192.168.1.203   k8s-worker2
```

### 回归验证结果

三条跨节点路径重新验证：

```text
master -> worker1(192.168.1.202:30080)
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7

master -> worker2(192.168.1.203:30080)
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7

worker1 -> master(192.168.1.201:30080)
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7
```

回归后的 stats 摘要：

```text
k8s-master:
nodeport_hit=30
backend_selected=6
snat_install=6
request_rewrite=30
revnat_hit=30
response_rewrite=30

k8s-worker1:
nodeport_hit=30
backend_selected=6
snat_install=6
request_rewrite=30
revnat_hit=30
response_rewrite=30

k8s-worker2:
nodeport_hit=30
backend_selected=6
snat_install=6
request_rewrite=30
revnat_hit=30
response_rewrite=30
```

补充说明：

- `backend_lookup_miss=0`，说明 NodePort frontend 查找和 backend 选择没有退化。
- `ct_lookup_miss` 仍然较大，主要来自挂在相关设备上的其它 TCP 流量，不代表 NodePort 命中失败。
- 这次重构后，`nodeport_hit`、`snat_install`、`revnat_hit` 仍按预期增长，说明“控制面探测 + config map”没有破坏原有 SNAT 闭环。

### 本次结论

这次改造已经把实现边界进一步收敛清楚了：

- 核心 LB/NAT/CT 逻辑继续留在 eBPF 数据面。
- 节点环境识别放到 agent 控制面完成。
- 数据面只读取“设备角色 -> ifindex”的最终配置，而不是在包路径里理解具体 CNI 名字。

因此，当前版本更接近下面这个定位：

```text
NodePort SNAT eBPF 数据面 + 节点网络环境探测控制面
```

而不是：

```text
写死 flannel 设备名的单环境原型
```

后续如果切换到别的 CNI，优先调整的应该是“环境探测和 delivery profile 生成逻辑”，而不是重写 Service LB 数据面本身。

### 2026-05-12 第二轮探测收敛：从“按设备名猜环境”改为“按职责认人”

上一版虽然已经把 `external/local/remote/routing_mode` 收敛成 config map，但探测逻辑仍然主要依赖设备名候选：

- `flannel.1`
- `cilium_vxlan`
- `vxlan.calico`
- `cni0`

这次进一步调整为“按节点地址和路由事实识别设备角色”，探测顺序改成：

```text
external iface:
  本节点 InternalIP 所在接口
  -> fallback 默认路由接口

local delivery iface:
  本节点 PodCIDR 选一个探测地址
  -> ip route get <probe-ip>
  -> 解析 dev

remote delivery iface:
  优先用远端 backend PodIP
  -> 否则用其它节点 PodCIDR 派生的探测地址
  -> ip route get <probe-ip>
  -> 解析 dev

routing_mode:
  remote iface != external iface -> encap
  remote iface == external iface -> native
```

实现位置：

- `scripts/detect_nodeport_environment.py`
- `scripts/sync_nodeport_maps.py`
- `scripts/run_nodeport_agent.sh`

这次之后：

- “候选设备名”仍然保留，但只作为 fallback。
- 主探测逻辑已经不再把 `flannel.1/cni0` 当成前提假设，而是把它们当成当前环境下由路由事实推导出来的结果。

本次镜像 digest：

- `sha256:14afa2243cbb6119dd0f2a1f0f3729e5fe503b1f6040aa0527423e4a0dcd8c37`

三台节点 rollout 后日志结果：

```text
k8s-master:
  resolved delivery profile: external=enp1s0 local=cni0 remote=flannel.1 mode=encap

k8s-worker1:
  resolved delivery profile: external=enp1s0 local=cni0 remote=flannel.1 mode=encap

k8s-worker2:
  resolved delivery profile: external=enp1s0 local=cni0 remote=flannel.1 mode=encap
```

说明这次的 `cni0/flannel.1` 已经不是“先猜到了 flannel，所以用这两个接口”，而是：

- `Node InternalIP` 落在 `enp1s0`
- 本节点 `PodCIDR` 的路由落在 `cni0`
- 其它节点 `PodCIDR` / 远端 backend 的路由落在 `flannel.1`

回归验证结果：

```text
master -> worker1(192.168.1.202:30080)
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7

master -> worker2(192.168.1.203:30080)
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7

worker1 -> master(192.168.1.201:30080)
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7
nodeport-echo-d9b549479-tnr5t
nodeport-echo-d9b549479-klqn7
```

对应 stats 摘要：

```text
k8s-master:
nodeport_hit=20
backend_selected=4
snat_install=4
request_rewrite=20
revnat_hit=20
response_rewrite=20

k8s-worker1:
nodeport_hit=20
backend_selected=4
snat_install=4
request_rewrite=20
revnat_hit=20
response_rewrite=20

k8s-worker2:
nodeport_hit=20
backend_selected=4
snat_install=4
request_rewrite=20
revnat_hit=20
response_rewrite=20
```

这一步之后，当前实现离“只关心 Service LB 与 delivery 角色，不直接关心底层 CNI 名称”的方向又近了一层。

### 2026-05-12 第三轮控制面收敛：切换到 watch，同步全集群 NodePort Service

上一版控制面已经能稳定完成：

- 按职责识别 `external/local/remote/routing_mode`
- 每节点写入 delivery config map
- 同步一个指定的 `NodePort` Service 到本地 BPF map

这一轮继续把控制面从 `poll` 改成更接近 informer 的事件驱动模式，并取消固定
`NODEPORT_SERVICE_SELECTOR`，让 agent 默认处理全集群内满足约束条件的 NodePort
Service。

当前默认同步范围明确为：

```text
type=NodePort
protocol=TCP
address family=IPv4
externalTrafficPolicy=Cluster
```

仍然暂不支持：

```text
UDP
IPv6
externalTrafficPolicy=Local
internalTrafficPolicy
sessionAffinity
DSR / hybrid
XDP fast path
```

本次主要改动：

- `manifests/nodeport-node-agent.yaml`
  - `NODEPORT_SERVICE_SELECTOR: ""`
  - `NODEPORT_SYNC_MODE: "watch"`
- `scripts/run_nodeport_agent.sh`
  - 默认同步模式改为 `watch`
  - 启动 syncer 时统一传入 `--sync-mode`
- `scripts/sync_nodeport_maps.py`
  - 新增 `watch` 模式
  - 启动时先做一次全量 reconcile
  - 后续分别 watch `Service`、`Node`、`EndpointSlice`
  - 收到事件后做 debounce，再触发一次全量 reconcile
- `Dockerfile`
- `Dockerfile.localbuild`
  - 增加 `python3-pip`
  - 安装 `kubernetes` Python client，供 watch 模式使用

这一轮实现上仍然保持了“控制面 watch，数据面只看最终 map”的边界：

```text
watch Service/Node/EndpointSlice
  -> 重新计算目标 service/backend/config 状态
  -> 写本地 BPF maps
  -> tc 数据面只按最终 map 做 lookup / NAT / redirect
```

#### 实现细节补充

这次最开始直接尝试使用 `DiscoveryV1Api.list_endpoint_slice_for_all_namespaces()` 做
EndpointSlice watch，但实际运行时出现反复报错：

```text
watch EndpointSlice: error: Invalid value for endpoints, must not be None
```

后续把 EndpointSlice watch 切换为：

```text
CustomObjectsApi.list_cluster_custom_object(
  group="discovery.k8s.io",
  version="v1",
  plural="endpointslices",
)
```

改为原始 dict 事件流之后，watch 恢复稳定。

本次最终镜像 digest：

- `sha256:6b415adcbb1e5590743e71bd3d320582808a7be52a36a8bf229d5f081c7c0243`

#### 本轮验证

1. agent rollout 后，三台节点日志都显示：

```text
starting NodePort syncer with mode=watch
watch EndpointSlice: listed ...
watch Service: listed ...
watch Node: listed ...
```

说明三个资源的 watch 都已经正常建立。

2. 对 `ebpf-nodeport-test/nodeport-echo` 做 `rollout restart`：

```text
kubectl rollout restart deployment/nodeport-echo -n ebpf-nodeport-test
```

控制面日志可以看到：

```text
watch: received ... event(s); reconciling
watch reconcile: syncing NodePort map state
```

并且 backend 变化后，新的 PodIP 会自动写回 map，不需要等固定轮询周期。

3. 临时新增第二个 NodePort Service 做“非固定 selector”验证：

```text
ebpf-nodeport-test/nodeport-echo-alt
type=NodePort
nodePort=30081
selector=app=nodeport-echo
externalTrafficPolicy=Cluster
```

新增后 master 节点日志出现：

```text
selected nodeport services: 2, total backends: 4
```

同时 map 中会为本节点写入两组 frontend：

```text
192.168.1.201:30080
192.168.1.201:30081
```

从 `k8s-master` 访问 `192.168.1.202:30081` 返回正常，两个 backend Pod 名可以轮转。

4. 删除临时 Service：

```text
kubectl delete svc/nodeport-echo-alt -n ebpf-nodeport-test
```

watch 会再次自动触发 reconcile，日志重新收敛为：

```text
selected nodeport services: 1, total backends: 2
```

说明当前实现已经不再依赖固定 `NODEPORT_SERVICE_SELECTOR`，而是默认自动识别全集群中满足约束条件的 NodePort Service。

#### 这一轮后的架构结论

当前控制面已经从“定时 poll 一个固定测试 Service”演进为：

```text
每节点 agent
  -> 启动时探测环境角色
  -> watch Service / Node / EndpointSlice
  -> 自动重建本节点应有的 service/backend/config maps
```

当前数据面则继续保持不变：

```text
tc 程序
  -> 读 service/backend/config/CT maps
  -> 对命中的 NodePort/TCP/IPv4/ETP=Cluster 流量做 SNAT 模式加速
```

到这里为止，这套实现已经具备了：

- 默认处理全集群满足约束条件的 NodePort Service
- backend 变化事件驱动收敛
- service 新增/删除事件驱动收敛
- 保留 kube-proxy fallback

距离更完整的 Cilium 风格 NodePort 实现，当前仍缺的主要是：

- 统一 LB service/backend/revNAT map
- `externalTrafficPolicy=Local`
- UDP / IPv6
- DSR / hybrid
- XDP fast path

### 2026-05-12 第四轮控制面重构：syncer 改为 Go informer，直接写 pinned BPF map

上一轮虽然已经切到 `watch`，但控制面本质上仍然是：

```text
Python watch
  -> 触发 reconcile
  -> 全量拉取 cluster snapshot
  -> shell + bpftool 清理并重写 service/backend/rr map
```

这一轮按新的目标继续收敛两件事：

1. 把 syncer 从 Python 改成 Go，使用 informer/cache 监听 `Service`、`Node`、`EndpointSlice`
2. 把 `update_nodeport_map.sh` / `update_nodeport_config_map.sh` 这类 shell 写 map 路径收掉，改为 Go 直接打开 pinned BPF map 做 `update/delete`

#### 本轮后的控制面分工

当前每节点 agent 的职责分工变成：

```text
shell:
  run_nodeport_agent.sh
  attach_nodeport_tc.sh
  cleanup_nodeport_tc.sh

python:
  detect_nodeport_environment.py
  只负责启动前环境探测

go:
  cmd/nodeport-syncer/main.go
  负责 informer/cache、service/backend 计算、增量 reconcile、直接写 pinned maps
```

也就是说，这一轮之后：

- “启动前探测环境角色”还保留 Python
- “NodePort Service 控制面同步”已经完全切到 Go
- “service/backend/config map 写入”也已经完全切到 Go

#### 新增文件与启动方式

新增：

- `go.mod`
- `go.sum`
- `cmd/nodeport-syncer/main.go`
- `scripts/build_nodeport_syncer.sh`

启动脚本 `scripts/run_nodeport_agent.sh` 也同步改成：

```text
/workspace/bin/nodeport-syncer
```

不再执行：

```text
python3 /workspace/scripts/sync_nodeport_maps.py
```

镜像构建方面，为了避免当前环境访问 Docker Hub 拉 builder image 超时，本轮没有使用 Docker 内部多阶段构建，而是：

```text
宿主机 Go 1.22 先编译 bin/nodeport-syncer
  -> Dockerfile.localbuild 直接 COPY 进 runtime 镜像
```

对应辅助脚本：

```text
GO_BIN=/tmp/go1.22.4/go/bin/go ./scripts/build_nodeport_syncer.sh
```

#### Go 控制面的实现边界

本轮 Go syncer 做了这些事：

- 用 `client-go` shared informer 监听：
  - `Service`
  - `Node`
  - `EndpointSlice`
- 启动时 `WaitForCacheSync()`
- 首次做一次 initial reconcile
- 后续在 watch 事件上做 debounce，再触发 reconcile
- 从 informer cache 中构建本节点需要处理的 NodePort frontend 和 backend 列表
- 直接打开这些 pinned map：
  - `nodeport_service_map`
  - `nodeport_backend_map`
  - `nodeport_rr_state_map`
  - `nodeport_config_map`
- 通过 `github.com/cilium/ebpf` 直接做：
  - `Update`
  - `Delete`

本轮仍然没有改动的数据面：

- `nodeport_tc.c`
- request path / response path NAT 逻辑
- CT / revNAT 逻辑

#### 关键行为变化

上一版是“事件驱动的全量重写”。

现在变成了“事件驱动的按 service 增量更新”：

```text
Service 不变:
  不写 map

新增一个 NodePort Service:
  只 upsert 这个新 service 的 frontend/backend

删除一个 NodePort Service:
  只 delete 这个 service 的 service/backend/rr state

同一个 NodePort Service 的 backend 集合变化:
  只 delete 旧 entry，再 upsert 新 entry
```

也就是说，目前仍然不是“单 backend slot 级别的最细粒度 patch”，但已经不是上一版那种：

```text
先清空全局 service/backend/rr map
再把所有 service 全量写回
```

#### 本轮镜像

本次最终镜像 manifest digest：

- `sha256:49a311c4c05703287dd68481c22f3f81b835e40da346ac2fb8712a875a053b0d`

#### 本轮验证

1. Go syncer 本地 dry-run 验证

直接在宿主机执行：

```text
nodeport-syncer --sync-mode oneshot --dry-run
```

可以正确识别：

- `ebpf-nodeport-test/nodeport-echo`
- 本节点 frontend
- 两个 backend

2. rollout 后三台 agent 都切到新的 Go 路径

三台节点日志都显示：

```text
starting NodePort syncer with mode=watch
2026/... sync mode=watch
2026/... initial sync: syncing delivery config
2026/... upsert service: ebpf-nodeport-test/nodeport-echo:30080 ...
```

而不是旧版的：

```text
watch Service / Node / EndpointSlice
+ env SERVICE_MAP_PIN=... update_nodeport_map.sh ...
cleared stale keys ...
selected nodeport services ...
```

说明当前线上已经不再走 Python + shell 写 map 的控制面路径。

3. rollout restart backend Deployment，观察增量更新

执行：

```text
kubectl rollout restart deployment/nodeport-echo -n ebpf-nodeport-test
```

master 节点日志可以看到：

```text
delete service: ebpf-nodeport-test/nodeport-echo:30080 frontend=192.168.1.201:30080
upsert service: ebpf-nodeport-test/nodeport-echo:30080 frontend=192.168.1.201:30080 backends=3
watch reconcile: services=1 backends=3 upserted=1 removed=0 unchanged=0

delete service: ebpf-nodeport-test/nodeport-echo:30080 frontend=192.168.1.201:30080
upsert service: ebpf-nodeport-test/nodeport-echo:30080 frontend=192.168.1.201:30080 backends=2
watch reconcile: services=1 backends=2 upserted=1 removed=0 unchanged=0
```

这说明：

- 不是全局 map 清空重建
- 只是对 `nodeport-echo:30080` 这一条 service entry 做替换
- 同一轮 backend 变化不会再重复 delete 同一个旧 entry

同时访问仍然正常，返回的新 Pod 名轮转为：

```text
nodeport-echo-764b54cdb9-lqp6t
nodeport-echo-764b54cdb9-mmpc6
```

4. 新增第二个 NodePort Service，验证“只增量 upsert 新 service”

临时创建：

```text
ebpf-nodeport-test/nodeport-echo-alt
nodePort=30081
externalTrafficPolicy=Cluster
selector=app=nodeport-echo
```

master 节点日志：

```text
watch reconcile: services=2 backends=4 upserted=1 removed=0 unchanged=1
```

说明当前只新增了 `nodeport-echo-alt:30081` 这一条 service entry，没有重写已有 `30080`。

从 `k8s-master` 访问 `192.168.1.202:30081` 返回正常：

```text
nodeport-echo-764b54cdb9-lqp6t
nodeport-echo-764b54cdb9-mmpc6
```

5. 删除第二个 NodePort Service，验证“只增量 delete 这个 service”

执行：

```text
kubectl delete svc/nodeport-echo-alt -n ebpf-nodeport-test
```

master 节点日志：

```text
delete service: ebpf-nodeport-test/nodeport-echo-alt:30081 frontend=192.168.1.201:30081
watch reconcile: services=1 backends=2 upserted=0 removed=1 unchanged=1
```

说明当前只删除了 `30081` 这条 service 的 map 条目，`30080` 保持不动。

#### 这一轮后的结论

到这里为止，当前实现已经从：

```text
Python watch
  + 全量 snapshot
  + shell/bpftool 写 map
```

演进为：

```text
Go informer/cache
  + 按 service 增量 reconcile
  + github.com/cilium/ebpf 直接写 pinned maps
```

当前仍然保留的旧组件只有两块：

- 启动前环境探测：`detect_nodeport_environment.py`
- tc 程序挂载/清理：shell 脚本

所以这轮之后，已经基本达到“先把 syncer 改成 Go，再把 map 写入也收进 Go”的目标。下一步如果继续往前收，可以考虑：

- 把环境探测也移到 Go
- 把 tc attach/cleanup 也移到 Go
- 再往统一 LB service/backend/revNAT map 演进

### 2026-05-12 第五轮启动路径收敛：环境探测和 tc attach/cleanup 也改成 Go

上一轮之后，控制面虽然已经切到 Go syncer，但节点启动路径还残留：

- `detect_nodeport_environment.py`
- `attach_nodeport_tc.sh`
- `cleanup_nodeport_tc.sh`
- `run_nodeport_agent.sh`

这一轮继续收敛为：

```text
nodeport-agent (Go)
  -> 读环境变量
  -> 访问 Kubernetes Node 信息
  -> 按职责识别 external/local/remote/routing_mode
  -> pre-cleanup
  -> 编译并挂载 tc eBPF 程序
  -> 启动 nodeport-syncer (Go)
  -> 退出时 cleanup
```

对应新增文件：

- `cmd/nodeport-agent/main.go`
- `internal/envdetect/envdetect.go`
- `internal/tcctl/tcctl.go`

本轮之后：

- `nodeport-agent` 成为新的唯一 runtime 入口
- DaemonSet command 改为 `/workspace/bin/nodeport-agent`
- `run_nodeport_agent.sh` 只保留成一个兼容 wrapper，内部直接 `exec` Go binary

#### 启动链路变化

旧链路：

```text
bash run_nodeport_agent.sh
  -> python detect_nodeport_environment.py
  -> bash cleanup_nodeport_tc.sh
  -> bash attach_nodeport_tc.sh
  -> go nodeport-syncer
```

新链路：

```text
go nodeport-agent
  -> go envdetect
  -> go tc cleanup / attach
  -> go nodeport-syncer
```

#### 镜像与部署变化

镜像里现在直接带两个二进制：

- `/workspace/bin/nodeport-agent`
- `/workspace/bin/nodeport-syncer`

本轮没有着急做新一轮集群验证，只做了三类轻量检查：

1. 本地 `go build`

```text
GO_BIN=/tmp/go1.22.4/go/bin/go ./scripts/build_nodeport_syncer.sh
```

结果：

```text
built: /home/ubuntu/wjy/ebpf_nodeport/bin/nodeport-syncer
built: /home/ubuntu/wjy/ebpf_nodeport/bin/nodeport-agent
```

2. 本地镜像构建

```text
docker build -f Dockerfile.localbuild ...
```

镜像构建成功，说明：

- Dockerfile 已经与新的二进制布局对齐
- DaemonSet 切换到 `nodeport-agent` 后镜像内容是完整的

3. `nodeport-agent --dry-run`

在 `KUBECONFIG=/etc/kubernetes/admin.conf` 下执行 dry-run，可以看到完整启动链路日志：

```text
resolved delivery profile: external=enp1s0 local=cni0 remote=flannel.1 mode=encap
pre-cleaning existing NodePort eBPF state
attaching NodePort tc program on outer=enp1s0 inner=cni0,flannel.1 attach_veths=false
starting NodePort syncer with mode=oneshot
initial sync: services=1 backends=2 upserted=1 removed=0 unchanged=0
cleaning up NodePort tc attachment
```

说明当前 Go agent 已经能够在不依赖 Python / shell 实现主逻辑的前提下，完成：

- 环境探测
- tc attach/cleanup 参数拼装
- syncer 启动
- 退出清理

#### 当前边界

到这里为止，运行时主逻辑已经全部迁到 Go。

仓库里仍然保留的 shell / Python 文件，目前只作为：

- 兼容入口
- 早期实现留档
- 参考实现

而不是 DaemonSet 的实际运行路径。
