#!/usr/bin/env bash

set -euo pipefail

CONFIG_MAP_PIN="${CONFIG_MAP_PIN:-/sys/fs/bpf/nodeport_tc/maps/nodeport_config_map}"
DRY_RUN="${DRY_RUN:-0}"

usage() {
    echo "usage: $0 <external_ifindex> <local_delivery_ifindex> <remote_delivery_ifindex> <routing_mode>" >&2
    exit 1
}

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

u32_to_le_bytes() {
    local value="$1"
    if (( value < 0 || value > 4294967295 )); then
        echo "invalid u32 value: $value" >&2
        exit 1
    fi
    printf '%02x %02x %02x %02x' \
        $(( value & 0xff )) \
        $(( (value >> 8) & 0xff )) \
        $(( (value >> 16) & 0xff )) \
        $(( (value >> 24) & 0xff ))
}

if [[ $# -ne 4 ]]; then
    usage
fi

require_root

if [[ "$DRY_RUN" != "1" && ! -e "$CONFIG_MAP_PIN" ]]; then
    echo "missing pinned config map: $CONFIG_MAP_PIN" >&2
    exit 1
fi

EXTERNAL_IFINDEX="$1"
LOCAL_DELIVERY_IFINDEX="$2"
REMOTE_DELIVERY_IFINDEX="$3"
ROUTING_MODE="$4"

run bpftool map update pinned "$CONFIG_MAP_PIN" \
    key hex 00 00 00 00 \
    value hex \
    $(u32_to_le_bytes "$EXTERNAL_IFINDEX") \
    $(u32_to_le_bytes "$LOCAL_DELIVERY_IFINDEX") \
    $(u32_to_le_bytes "$REMOTE_DELIVERY_IFINDEX") \
    $(u32_to_le_bytes "$ROUTING_MODE") any

echo "nodeport config updated: external=${EXTERNAL_IFINDEX} local=${LOCAL_DELIVERY_IFINDEX} remote=${REMOTE_DELIVERY_IFINDEX} routing_mode=${ROUTING_MODE}"

