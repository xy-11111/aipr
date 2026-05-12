#!/usr/bin/env bash

set -euo pipefail

BPFFS_ROOT="${BPFFS_ROOT:-/sys/fs/bpf/nodeport_tc}"
MAP_DIR="${BPFFS_ROOT}/maps"
PROG_PIN="${BPFFS_ROOT}/prog"
OUTER_IFACE="${NODEPORT_ATTACH_IFACE:-enp1s0}"
INNER_IFACES="${NODEPORT_INNER_IFACES:-cni0,flannel.1}"
ATTACH_VETHS="${NODEPORT_ATTACH_VETHS:-0}"
VETH_GLOB="${NODEPORT_VETH_GLOB:-veth*}"
DRY_RUN="${DRY_RUN:-0}"

run() {
    printf '+'
    printf ' %q' "$@"
    printf '\n'
    if [[ "$DRY_RUN" != "1" ]]; then
        "$@"
    fi
}

require_root() {
    if [[ "$DRY_RUN" == "1" ]]; then
        return
    fi
    if [[ "$EUID" -ne 0 ]]; then
        echo "run as root or with sudo" >&2
        exit 1
    fi
}

delete_ingress() {
    local iface="$1"
    ip link show "$iface" >/dev/null 2>&1 || return 0
    run tc filter del dev "$iface" ingress pref 10 handle 10 bpf direct-action 2>/dev/null || true
}

delete_egress() {
    local iface="$1"
    ip link show "$iface" >/dev/null 2>&1 || return 0
    run tc filter del dev "$iface" egress pref 10 handle 10 bpf direct-action 2>/dev/null || true
}

require_root

delete_ingress "$OUTER_IFACE"
delete_egress "$OUTER_IFACE"

normalized_inner="${INNER_IFACES//,/ }"
read -r -a inner_items <<< "$normalized_inner"
for iface in "${inner_items[@]}"; do
    [[ -n "$iface" ]] || continue
    delete_ingress "$iface"
done

if [[ "$ATTACH_VETHS" == "1" ]]; then
    for iface_path in /sys/class/net/${VETH_GLOB}; do
        [[ -e "$iface_path" ]] || continue
        delete_ingress "$(basename "$iface_path")"
    done
fi

if [[ -e "$PROG_PIN" ]]; then
    run rm -f "$PROG_PIN"
fi

if [[ -d "$MAP_DIR" ]]; then
    run find "$MAP_DIR" -mindepth 1 -maxdepth 1 -type f -delete
fi

echo "cleaned nodeport tc state under: $BPFFS_ROOT"
