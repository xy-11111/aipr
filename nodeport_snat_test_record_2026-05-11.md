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
- 最终镜像 digest：`sha256:02e61974f8bf2f04eab87e43131d0fe15edc4a82738deca5116553c217238f25`

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

## 当前部署状态

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

## 最终验证

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

## 当前结论

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
