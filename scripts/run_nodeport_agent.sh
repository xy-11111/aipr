#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ATTACH_SCRIPT="${ATTACH_SCRIPT:-${SCRIPT_DIR}/attach_nodeport_tc.sh}"
CLEANUP_SCRIPT="${CLEANUP_SCRIPT:-${SCRIPT_DIR}/cleanup_nodeport_tc.sh}"
SYNC_SCRIPT="${SYNC_SCRIPT:-${SCRIPT_DIR}/sync_nodeport_maps.py}"

NODEPORT_ATTACH_IFACE="${NODEPORT_ATTACH_IFACE:-enp1s0}"
NODEPORT_INNER_IFACES="${NODEPORT_INNER_IFACES:-cni0,flannel.1}"
NODEPORT_ATTACH_VETHS="${NODEPORT_ATTACH_VETHS:-0}"
NODEPORT_VETH_GLOB="${NODEPORT_VETH_GLOB:-veth*}"
NODEPORT_SET_ACCEPT_LOCAL="${NODEPORT_SET_ACCEPT_LOCAL:-1}"
NODEPORT_SNAT_IFACE="${NODEPORT_SNAT_IFACE:-cni0}"
NODEPORT_SNAT_IP="${NODEPORT_SNAT_IP:-}"
BPF_CFLAGS="${BPF_CFLAGS:-}"

NODEPORT_SYNC_MODE="${NODEPORT_SYNC_MODE:-poll}"
NODEPORT_SYNC_POLL_INTERVAL="${NODEPORT_SYNC_POLL_INTERVAL:-5}"
NODEPORT_SERVICE_SELECTOR="${NODEPORT_SERVICE_SELECTOR:-}"
NODEPORT_EXTRA_ARGS="${NODEPORT_EXTRA_ARGS:-}"

PRE_CLEANUP="${PRE_CLEANUP:-1}"
CLEANUP_ON_EXIT="${CLEANUP_ON_EXIT:-1}"

SYNC_PID=""
STOPPING=0

log() {
    echo "[$(date '+%H:%M:%S')] $*"
}

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "missing command: $1" >&2
        exit 1
    fi
}

run_cleanup() {
    if [[ "$CLEANUP_ON_EXIT" != "1" ]]; then
        return 0
    fi

    log "cleaning up NodePort tc attachment"
    NODEPORT_ATTACH_IFACE="$NODEPORT_ATTACH_IFACE" NODEPORT_INNER_IFACES="$NODEPORT_INNER_IFACES" NODEPORT_ATTACH_VETHS="$NODEPORT_ATTACH_VETHS" NODEPORT_VETH_GLOB="$NODEPORT_VETH_GLOB" "$CLEANUP_SCRIPT" || true
}

cleanup() {
    local rc="$1"

    if [[ "$STOPPING" == "1" ]]; then
        return
    fi
    STOPPING=1

    if [[ -n "$SYNC_PID" ]] && kill -0 "$SYNC_PID" 2>/dev/null; then
        log "stopping NodePort syncer (pid=${SYNC_PID})"
        kill "$SYNC_PID" 2>/dev/null || true
        wait "$SYNC_PID" 2>/dev/null || true
    fi

    run_cleanup
    return "$rc"
}

handle_signal() {
    cleanup 0
    exit 0
}

trap 'rc=$?; cleanup "$rc"' EXIT
trap handle_signal INT TERM

require_cmd bpftool
require_cmd clang
require_cmd kubectl
require_cmd python3
require_cmd tc

if [[ "$PRE_CLEANUP" == "1" ]]; then
    log "pre-cleaning existing NodePort eBPF state"
    NODEPORT_ATTACH_IFACE="$NODEPORT_ATTACH_IFACE" NODEPORT_INNER_IFACES="$NODEPORT_INNER_IFACES" NODEPORT_ATTACH_VETHS="$NODEPORT_ATTACH_VETHS" NODEPORT_VETH_GLOB="$NODEPORT_VETH_GLOB" "$CLEANUP_SCRIPT" || true
fi

log "attaching NodePort tc program on outer=${NODEPORT_ATTACH_IFACE} inner=${NODEPORT_INNER_IFACES} attach_veths=${NODEPORT_ATTACH_VETHS}"
NODEPORT_ATTACH_IFACE="$NODEPORT_ATTACH_IFACE" NODEPORT_INNER_IFACES="$NODEPORT_INNER_IFACES" NODEPORT_ATTACH_VETHS="$NODEPORT_ATTACH_VETHS" NODEPORT_VETH_GLOB="$NODEPORT_VETH_GLOB" NODEPORT_SET_ACCEPT_LOCAL="$NODEPORT_SET_ACCEPT_LOCAL" BPF_CFLAGS="$BPF_CFLAGS" "$ATTACH_SCRIPT"

sync_args=("--kubectl" "kubectl")

if [[ -n "${NODE_NAME:-}" ]]; then
    sync_args+=("--node-name" "$NODE_NAME")
fi

if [[ -n "$NODEPORT_SERVICE_SELECTOR" ]]; then
    sync_args+=("--service" "$NODEPORT_SERVICE_SELECTOR")
fi

if [[ -n "$NODEPORT_SNAT_IP" ]]; then
    sync_args+=("--snat-ip" "$NODEPORT_SNAT_IP")
else
    sync_args+=("--snat-iface" "$NODEPORT_SNAT_IFACE")
fi

case "$NODEPORT_SYNC_MODE" in
    poll)
        sync_args+=("--poll-interval" "$NODEPORT_SYNC_POLL_INTERVAL")
        ;;
    oneshot)
        ;;
    *)
        echo "unsupported NODEPORT_SYNC_MODE: $NODEPORT_SYNC_MODE" >&2
        exit 1
        ;;
esac

if [[ -n "$NODEPORT_EXTRA_ARGS" ]]; then
    read -r -a extra_args <<< "$NODEPORT_EXTRA_ARGS"
    sync_args+=("${extra_args[@]}")
fi

log "starting NodePort syncer with mode=${NODEPORT_SYNC_MODE}"
python3 "$SYNC_SCRIPT" "${sync_args[@]}" &
SYNC_PID="$!"

set +e
wait "$SYNC_PID"
sync_rc=$?
set -e

SYNC_PID=""
if [[ "$STOPPING" == "1" ]]; then
    exit 0
fi

log "NodePort syncer exited with code ${sync_rc}"
exit "$sync_rc"
