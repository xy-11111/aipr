#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

SRC="${SRC:-${PROJECT_ROOT}/nodeport_tc.c}"
OBJ="${OBJ:-/tmp/nodeport_tc.o}"
BPFFS_ROOT="${BPFFS_ROOT:-/sys/fs/bpf/nodeport_tc}"
MAP_DIR="${BPFFS_ROOT}/maps"
PROG_PIN="${BPFFS_ROOT}/prog"
OUTER_IFACE="${NODEPORT_ATTACH_IFACE:-enp1s0}"
INNER_IFACES="${NODEPORT_INNER_IFACES:-cni0,flannel.1}"
ATTACH_VETHS="${NODEPORT_ATTACH_VETHS:-0}"
VETH_GLOB="${NODEPORT_VETH_GLOB:-veth*}"
SET_ACCEPT_LOCAL="${NODEPORT_SET_ACCEPT_LOCAL:-1}"
DRY_RUN="${DRY_RUN:-0}"
CLANG="${CLANG:-clang}"
BPF_CFLAGS="${BPF_CFLAGS:-}"

declare -a EXTRA_BPF_CFLAGS=()
if [[ -n "$BPF_CFLAGS" ]]; then
    read -r -a EXTRA_BPF_CFLAGS <<< "$BPF_CFLAGS"
fi

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

set_accept_local() {
    local iface="$1"
    local proc_path="/proc/sys/net/ipv4/conf/${iface}/accept_local"

    [[ "$SET_ACCEPT_LOCAL" == "1" ]] || return 0
    [[ -e "$proc_path" ]] || return 0
    run sh -c "echo 1 > '$proc_path'"
}

attach_ingress() {
    local iface="$1"
    ip link show "$iface" >/dev/null 2>&1 || {
        echo "skip missing ingress iface: $iface" >&2
        return 0
    }

    run tc qdisc replace dev "$iface" clsact
    run tc filter replace dev "$iface" ingress pref 10 handle 10 bpf direct-action object-pinned "$PROG_PIN"
    set_accept_local "$iface"
}

attach_egress() {
    local iface="$1"
    ip link show "$iface" >/dev/null 2>&1 || {
        echo "skip missing egress iface: $iface" >&2
        return 0
    }

    run tc qdisc replace dev "$iface" clsact
    run tc filter replace dev "$iface" egress pref 10 handle 10 bpf direct-action object-pinned "$PROG_PIN"
    set_accept_local "$iface"
}

require_root

if [[ ! -f "$SRC" ]]; then
    echo "missing source file: $SRC" >&2
    exit 1
fi

if ! mountpoint -q /sys/fs/bpf; then
    run mount -t bpf bpf /sys/fs/bpf
fi

run mkdir -p "$MAP_DIR"
run rm -f "$PROG_PIN"
run find "$MAP_DIR" -mindepth 1 -maxdepth 1 -type f -delete

run "$CLANG" -O2 -g -target bpf -D__TARGET_ARCH_x86 -I/usr/include/x86_64-linux-gnu "${EXTRA_BPF_CFLAGS[@]}" -c "$SRC" -o "$OBJ"
run bpftool prog load "$OBJ" "$PROG_PIN" type classifier pinmaps "$MAP_DIR"

normalized_inner="${INNER_IFACES//,/ }"
read -r -a inner_items <<< "$normalized_inner"

attach_ingress "$OUTER_IFACE"
attach_egress "$OUTER_IFACE"

for iface in "${inner_items[@]}"; do
    [[ -n "$iface" ]] || continue
    attach_ingress "$iface"
done

declare -a veth_items=()
if [[ "$ATTACH_VETHS" == "1" ]]; then
    for iface_path in /sys/class/net/${VETH_GLOB}; do
        [[ -e "$iface_path" ]] || continue
        iface="$(basename "$iface_path")"
        veth_items+=("$iface")
        attach_ingress "$iface"
    done
fi

echo "program pinned at: $PROG_PIN"
echo "maps pinned under: $MAP_DIR"
echo "outer iface: $OUTER_IFACE ingress+egress"
echo "inner ingress ifaces: $INNER_IFACES"
if [[ "$ATTACH_VETHS" == "1" ]]; then
    echo "veth ingress ifaces: ${veth_items[*]:-(none)}"
else
    echo "veth ingress ifaces: disabled"
fi
