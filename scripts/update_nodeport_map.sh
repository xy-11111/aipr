#!/usr/bin/env bash

set -euo pipefail

SERVICE_MAP_PIN="${SERVICE_MAP_PIN:-/sys/fs/bpf/nodeport_tc/maps/nodeport_service_map}"
BACKEND_MAP_PIN="${BACKEND_MAP_PIN:-/sys/fs/bpf/nodeport_tc/maps/nodeport_backend_map}"
DRY_RUN="${DRY_RUN:-0}"
PROTO_TCP_HEX="06"

usage() {
    echo "usage: $0 <node_ip> <node_port> <snat_ip> <backend_ip> <backend_port> <backend_node_ip> [backend_index] [backend_count]" >&2
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

ip_to_hex_bytes() {
    local ip="$1"
    local a b c d
    IFS='.' read -r a b c d <<< "$ip"
    for octet in "$a" "$b" "$c" "$d"; do
        if [[ -z "$octet" ]] || (( octet < 0 || octet > 255 )); then
            echo "invalid ip: $ip" >&2
            exit 1
        fi
    done
    printf '%02x %02x %02x %02x' "$a" "$b" "$c" "$d"
}

port_to_be16_bytes() {
    local port="$1"
    if (( port < 1 || port > 65535 )); then
        echo "invalid port: $port" >&2
        exit 1
    fi
    printf '%02x %02x' $(( (port >> 8) & 0xff )) $(( port & 0xff ))
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

if [[ $# -lt 6 || $# -gt 8 ]]; then
    usage
fi

require_root

if [[ "$DRY_RUN" != "1" && ( ! -e "$SERVICE_MAP_PIN" || ! -e "$BACKEND_MAP_PIN" ) ]]; then
    echo "missing pinned map under /sys/fs/bpf/nodeport_tc/maps" >&2
    exit 1
fi

NODE_IP="$1"
NODE_PORT="$2"
SNAT_IP="$3"
BACKEND_IP="$4"
BACKEND_PORT="$5"
BACKEND_NODE_IP="$6"
BACKEND_INDEX="${7:-0}"
BACKEND_COUNT="${8:-$((BACKEND_INDEX + 1))}"

KEY_HEX="$(ip_to_hex_bytes "$NODE_IP") $(port_to_be16_bytes "$NODE_PORT") $PROTO_TCP_HEX 00"
SERVICE_VALUE_HEX="$(u32_to_le_bytes "$BACKEND_COUNT") 00 00 00 00 $(ip_to_hex_bytes "$SNAT_IP")"
BACKEND_KEY_HEX="$KEY_HEX $(u32_to_le_bytes "$BACKEND_INDEX")"
BACKEND_VALUE_HEX="$(ip_to_hex_bytes "$BACKEND_IP") $(port_to_be16_bytes "$BACKEND_PORT") 00 00 $(ip_to_hex_bytes "$BACKEND_NODE_IP")"

run bpftool map update pinned "$SERVICE_MAP_PIN" key hex $KEY_HEX value hex $SERVICE_VALUE_HEX any
run bpftool map update pinned "$BACKEND_MAP_PIN" key hex $BACKEND_KEY_HEX value hex $BACKEND_VALUE_HEX any
echo "nodeport map updated: ${NODE_IP}:${NODE_PORT} snat ${SNAT_IP} slot ${BACKEND_INDEX}/${BACKEND_COUNT} -> ${BACKEND_IP}:${BACKEND_PORT} on ${BACKEND_NODE_IP}"
