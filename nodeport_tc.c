#include <stdbool.h>
#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/pkt_cls.h>
#include <linux/tcp.h>
#include <linux/in.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

#ifndef TC_ACT_OK
#define TC_ACT_OK 0
#endif

#ifndef TC_ACT_REDIRECT
#define TC_ACT_REDIRECT 7
#endif

#ifndef IPPROTO_TCP
#define IPPROTO_TCP 6
#endif

#ifndef NODEPORT_SNAT_MIN
#define NODEPORT_SNAT_MIN 40000
#endif

#ifndef NODEPORT_SNAT_MAX
#define NODEPORT_SNAT_MAX 60999
#endif

enum stats_index {
    STAT_TCP_PACKETS = 0,
    STAT_NODEPORT_HIT = 1,
    STAT_BACKEND_SELECTED = 2,
    STAT_BACKEND_LOOKUP_MISS = 3,
    STAT_RR_UPDATE = 4,
    STAT_SNAT_INSTALL = 5,
    STAT_REQUEST_REWRITE = 6,
    STAT_REVNAT_HIT = 7,
    STAT_CT_LOOKUP_MISS = 8,
    STAT_RESPONSE_REWRITE = 9,
    STAT_SAME_NODE_SKIP = 10,
    STAT_COUNT = 11,
};

struct nodeport_key {
    __be32 address;
    __be16 port;
    __u8 proto;
    __u8 pad;
};

struct nodeport_value {
    __u32 backend_count;
    __u32 flags;
    __be32 snat_ip;
};

struct nodeport_backend_key {
    struct nodeport_key service;
    __u32 slot;
};

struct nodeport_backend_value {
    __be32 address;
    __be16 port;
    __u16 pad;
    __be32 node_ip;
};

struct nodeport_ct_key {
    __be32 backend_ip;
    __be32 node_ip;
    __be16 backend_port;
    __be16 snat_port;
    __u8 proto;
    __u8 pad[3];
};

struct nodeport_ct_value {
    __be32 client_ip;
    __be32 frontend_ip;
    __be16 client_port;
    __be16 frontend_port;
    __u64 last_seen_ns;
};

struct nodeport_fwd_ct_key {
    __be32 client_ip;
    __be32 frontend_ip;
    __be16 client_port;
    __be16 frontend_port;
    __u8 proto;
    __u8 pad[3];
};

struct nodeport_fwd_ct_value {
    __be32 backend_ip;
    __be32 snat_ip;
    __be16 backend_port;
    __be16 snat_port;
    __u32 egress_ifindex;
};

struct nodeport_config {
    __u32 outer_ifindex;
    __u32 inner_ifindex;
    __u32 tunnel_ifindex;
    __u32 flags;
};

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 512);
    __type(key, struct nodeport_key);
    __type(value, struct nodeport_value);
} nodeport_service_map SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 4096);
    __type(key, struct nodeport_backend_key);
    __type(value, struct nodeport_backend_value);
} nodeport_backend_map SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 512);
    __type(key, struct nodeport_key);
    __type(value, __u32);
} nodeport_rr_state_map SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 16384);
    __type(key, struct nodeport_ct_key);
    __type(value, struct nodeport_ct_value);
} nodeport_ct_map SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 16384);
    __type(key, struct nodeport_fwd_ct_key);
    __type(value, struct nodeport_fwd_ct_value);
} nodeport_fwd_ct_map SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, __u32);
} nodeport_snat_port_map SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, STAT_COUNT);
    __type(key, __u32);
    __type(value, __u64);
} nodeport_stats_map SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, struct nodeport_config);
} nodeport_config_map SEC(".maps");

static __always_inline void bump_stat(__u32 index)
{
    __u64 *value;

    value = bpf_map_lookup_elem(&nodeport_stats_map, &index);
    if (value)
        *value += 1;
}

static __always_inline int parse_tcp_packet(
    void *data,
    void *data_end,
    struct iphdr **ip_hdr,
    struct tcphdr **tcp_hdr)
{
    struct ethhdr *eth = data;
    struct iphdr *ip;
    struct tcphdr *tcp;
    __u32 ip_hdr_len;
    __u32 tcp_hdr_len;

    if ((void *)(eth + 1) > data_end)
        return -1;

    if (eth->h_proto != bpf_htons(ETH_P_IP))
        return -1;

    ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end)
        return -1;

    if (ip->version != 4 || ip->protocol != IPPROTO_TCP)
        return -1;

    ip_hdr_len = ip->ihl * 4;
    if (ip_hdr_len < sizeof(*ip))
        return -1;
    if ((void *)ip + ip_hdr_len > data_end)
        return -1;

    tcp = (void *)ip + ip_hdr_len;
    if ((void *)(tcp + 1) > data_end)
        return -1;

    tcp_hdr_len = tcp->doff * 4;
    if (tcp_hdr_len < sizeof(*tcp))
        return -1;
    if ((void *)tcp + tcp_hdr_len > data_end)
        return -1;

    *ip_hdr = ip;
    *tcp_hdr = tcp;
    return 0;
}

static __always_inline int lookup_backend_slot(
    const struct nodeport_key *service,
    __u32 slot,
    struct nodeport_backend_value *backend_out)
{
    struct nodeport_backend_key backend_key = {
        .service = *service,
        .slot = slot,
    };
    const struct nodeport_backend_value *backend;

    backend = bpf_map_lookup_elem(&nodeport_backend_map, &backend_key);
    if (!backend)
        return -1;

    *backend_out = *backend;
    return 0;
}

static __always_inline int select_backend(
    const struct nodeport_key *service,
    const struct nodeport_value *service_meta,
    struct nodeport_backend_value *backend_out)
{
    __u32 *rr_slot;
    __u32 slot = 0;
    __u32 next_slot;

    if (!service_meta || service_meta->backend_count == 0)
        return -1;

    rr_slot = bpf_map_lookup_elem(&nodeport_rr_state_map, service);
    if (rr_slot)
        slot = *rr_slot;

    if (slot >= service_meta->backend_count)
        slot = 0;

    if (lookup_backend_slot(service, slot, backend_out) < 0)
        return -1;

    next_slot = slot + 1;
    if (next_slot >= service_meta->backend_count)
        next_slot = 0;

    bpf_map_update_elem(&nodeport_rr_state_map, service, &next_slot, BPF_ANY);
    bump_stat(STAT_RR_UPDATE);
    return 0;
}

static __always_inline __be16 allocate_snat_port(void)
{
    __u32 index = 0;
    __u32 *next;
    __u32 port;
    __u32 new_next;

    next = bpf_map_lookup_elem(&nodeport_snat_port_map, &index);
    if (!next) {
        port = NODEPORT_SNAT_MIN;
        new_next = NODEPORT_SNAT_MIN + 1;
        bpf_map_update_elem(&nodeport_snat_port_map, &index, &new_next, BPF_ANY);
        return bpf_htons((__u16)port);
    }

    port = *next;
    if (port < NODEPORT_SNAT_MIN || port > NODEPORT_SNAT_MAX)
        port = NODEPORT_SNAT_MIN;

    new_next = port + 1;
    if (new_next > NODEPORT_SNAT_MAX)
        new_next = NODEPORT_SNAT_MIN;

    bpf_map_update_elem(&nodeport_snat_port_map, &index, &new_next, BPF_ANY);
    return bpf_htons((__u16)port);
}

static __always_inline int rewrite_tuple(
    struct __sk_buff *skb,
    void *data,
    struct iphdr *ip,
    struct tcphdr *tcp,
    __be32 new_saddr,
    __be32 new_daddr,
    __be16 new_sport,
    __be16 new_dport)
{
    __be32 old_saddr = ip->saddr;
    __be32 old_daddr = ip->daddr;
    __be16 old_sport = tcp->source;
    __be16 old_dport = tcp->dest;
    __u32 ip_check_offset = (__u32)((void *)&ip->check - data);
    __u32 ip_saddr_offset = (__u32)((void *)&ip->saddr - data);
    __u32 ip_daddr_offset = (__u32)((void *)&ip->daddr - data);
    __u32 tcp_check_offset = (__u32)((void *)&tcp->check - data);
    __u32 tcp_source_offset = (__u32)((void *)&tcp->source - data);
    __u32 tcp_dest_offset = (__u32)((void *)&tcp->dest - data);

    if (old_saddr != new_saddr &&
        bpf_skb_store_bytes(skb, ip_saddr_offset, &new_saddr, sizeof(new_saddr), 0) < 0)
        return -1;
    if (old_daddr != new_daddr &&
        bpf_skb_store_bytes(skb, ip_daddr_offset, &new_daddr, sizeof(new_daddr), 0) < 0)
        return -1;
    if (old_sport != new_sport &&
        bpf_skb_store_bytes(skb, tcp_source_offset, &new_sport, sizeof(new_sport), 0) < 0)
        return -1;
    if (old_dport != new_dport &&
        bpf_skb_store_bytes(skb, tcp_dest_offset, &new_dport, sizeof(new_dport), 0) < 0)
        return -1;

    if (old_saddr != new_saddr &&
        bpf_l3_csum_replace(skb, ip_check_offset, old_saddr, new_saddr, sizeof(new_saddr)) < 0)
        return -1;
    if (old_daddr != new_daddr &&
        bpf_l3_csum_replace(skb, ip_check_offset, old_daddr, new_daddr, sizeof(new_daddr)) < 0)
        return -1;

    if (old_saddr != new_saddr &&
        bpf_l4_csum_replace(skb, tcp_check_offset, old_saddr, new_saddr,
                            BPF_F_PSEUDO_HDR | sizeof(new_saddr)) < 0)
        return -1;
    if (old_daddr != new_daddr &&
        bpf_l4_csum_replace(skb, tcp_check_offset, old_daddr, new_daddr,
                            BPF_F_PSEUDO_HDR | sizeof(new_daddr)) < 0)
        return -1;
    if (old_sport != new_sport &&
        bpf_l4_csum_replace(skb, tcp_check_offset, old_sport, new_sport, sizeof(new_sport)) < 0)
        return -1;
    if (old_dport != new_dport &&
        bpf_l4_csum_replace(skb, tcp_check_offset, old_dport, new_dport, sizeof(new_dport)) < 0)
        return -1;

    return 0;
}

static __always_inline int handle_request(
    struct __sk_buff *skb,
    void *data,
    struct iphdr *ip,
    struct tcphdr *tcp)
{
    struct nodeport_key service = {
        .address = ip->daddr,
        .port = tcp->dest,
        .proto = IPPROTO_TCP,
        .pad = 0,
    };
    const struct nodeport_value *service_meta;
    const struct nodeport_config *config;
    const struct nodeport_fwd_ct_value *fwd_ct;
    struct nodeport_backend_value backend = {};
    struct nodeport_ct_key ct_key = {};
    struct nodeport_ct_value ct_value = {};
    struct nodeport_fwd_ct_key fwd_key = {};
    struct nodeport_fwd_ct_value fwd_value = {};
    __u32 config_key = 0;
    __u32 egress_ifindex = 0;
    __be16 snat_port;

    service_meta = bpf_map_lookup_elem(&nodeport_service_map, &service);
    if (!service_meta)
        return -1;

    bump_stat(STAT_NODEPORT_HIT);
    config = bpf_map_lookup_elem(&nodeport_config_map, &config_key);

    fwd_key.client_ip = ip->saddr;
    fwd_key.frontend_ip = service.address;
    fwd_key.client_port = tcp->source;
    fwd_key.frontend_port = service.port;
    fwd_key.proto = IPPROTO_TCP;

    fwd_ct = bpf_map_lookup_elem(&nodeport_fwd_ct_map, &fwd_key);
    if (fwd_ct) {
        if (rewrite_tuple(skb, data, ip, tcp,
                          fwd_ct->snat_ip, fwd_ct->backend_ip,
                          fwd_ct->snat_port, fwd_ct->backend_port) == 0) {
            bump_stat(STAT_REQUEST_REWRITE);
            if (fwd_ct->egress_ifindex > 0) {
                bpf_redirect_neigh(fwd_ct->egress_ifindex, 0, 0, 0);
                return TC_ACT_REDIRECT;
            }
        }

        return TC_ACT_OK;
    }

    if (select_backend(&service, service_meta, &backend) < 0) {
        bump_stat(STAT_BACKEND_LOOKUP_MISS);
        return 0;
    }

    bump_stat(STAT_BACKEND_SELECTED);
    snat_port = allocate_snat_port();
    if (config) {
        if (backend.node_ip == service.address)
            egress_ifindex = config->inner_ifindex;
        else
            egress_ifindex = config->tunnel_ifindex;
    }

    ct_key.backend_ip = backend.address;
    ct_key.node_ip = service_meta->snat_ip;
    ct_key.backend_port = backend.port;
    ct_key.snat_port = snat_port;
    ct_key.proto = IPPROTO_TCP;

    ct_value.client_ip = ip->saddr;
    ct_value.frontend_ip = service.address;
    ct_value.client_port = tcp->source;
    ct_value.frontend_port = service.port;
    ct_value.last_seen_ns = bpf_ktime_get_ns();

    if (bpf_map_update_elem(&nodeport_ct_map, &ct_key, &ct_value, BPF_ANY) == 0)
        bump_stat(STAT_SNAT_INSTALL);

    fwd_value.backend_ip = backend.address;
    fwd_value.snat_ip = service_meta->snat_ip;
    fwd_value.backend_port = backend.port;
    fwd_value.snat_port = snat_port;
    fwd_value.egress_ifindex = egress_ifindex;
    bpf_map_update_elem(&nodeport_fwd_ct_map, &fwd_key, &fwd_value, BPF_ANY);

    if (rewrite_tuple(skb, data, ip, tcp, service_meta->snat_ip, backend.address, snat_port, backend.port) == 0) {
        bump_stat(STAT_REQUEST_REWRITE);
        if (egress_ifindex > 0) {
            bpf_redirect_neigh(egress_ifindex, 0, 0, 0);
            return TC_ACT_REDIRECT;
        }
    }

    return TC_ACT_OK;
}

static __always_inline int handle_response(
    struct __sk_buff *skb,
    void *data,
    struct iphdr *ip,
    struct tcphdr *tcp)
{
    struct nodeport_ct_key ct_key = {
        .backend_ip = ip->saddr,
        .node_ip = ip->daddr,
        .backend_port = tcp->source,
        .snat_port = tcp->dest,
        .proto = IPPROTO_TCP,
    };
    const struct nodeport_ct_value *ct_value;
    const struct nodeport_config *config;
    struct nodeport_ct_value updated;
    __u32 config_key = 0;

    ct_value = bpf_map_lookup_elem(&nodeport_ct_map, &ct_key);
    if (!ct_value)
        return -1;

    updated = *ct_value;
    updated.last_seen_ns = bpf_ktime_get_ns();
    bpf_map_update_elem(&nodeport_ct_map, &ct_key, &updated, BPF_ANY);

    bump_stat(STAT_REVNAT_HIT);

    if (rewrite_tuple(skb, data, ip, tcp,
                      updated.frontend_ip, updated.client_ip,
                      updated.frontend_port, updated.client_port) == 0)
        bump_stat(STAT_RESPONSE_REWRITE);

    config = bpf_map_lookup_elem(&nodeport_config_map, &config_key);
    if (config && config->outer_ifindex > 0) {
        bpf_redirect_neigh(config->outer_ifindex, 0, 0, 0);
        return TC_ACT_REDIRECT;
    }

    return TC_ACT_OK;
}

SEC("tc")
int nodeport_tc(struct __sk_buff *skb)
{
    void *data = (void *)(long)skb->data;
    void *data_end = (void *)(long)skb->data_end;
    struct iphdr *ip;
    struct tcphdr *tcp;
    int action;

    if (parse_tcp_packet(data, data_end, &ip, &tcp) < 0)
        return TC_ACT_OK;

    bump_stat(STAT_TCP_PACKETS);

    action = handle_response(skb, data, ip, tcp);
    if (action >= 0)
        return action;

    action = handle_request(skb, data, ip, tcp);
    if (action >= 0)
        return action;

    bump_stat(STAT_CT_LOOKUP_MISS);
    return TC_ACT_OK;
}

char LICENSE[] SEC("license") = "GPL";
