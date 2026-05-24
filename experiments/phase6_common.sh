#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "${SCRIPT_DIR}/common.sh"

PHASE6_TOPOLOGY_MODE="${PHASE6_TOPOLOGY_MODE:-}"
PHASE6_LOAD_TIER="${PHASE6_LOAD_TIER:-}"
PHASE6_READY_TIMEOUT_SECONDS="${PHASE6_READY_TIMEOUT_SECONDS:-45}"
PHASE6_READY_PROBES_REQUIRED="${PHASE6_READY_PROBES_REQUIRED:-3}"

phase6_require_topology_mode() {
  local allowed_csv="${1:-local,remote}"
  local item
  [[ -n "${PHASE6_TOPOLOGY_MODE}" ]] || fail_experiment "phase6_topology_mode_required"
  IFS=',' read -r -a allowed_items <<< "${allowed_csv}"
  for item in "${allowed_items[@]}"; do
    item="$(printf '%s' "${item}" | xargs)"
    if [[ "${PHASE6_TOPOLOGY_MODE}" == "${item}" ]]; then
      return 0
    fi
  done
  fail_experiment "phase6_invalid_topology_mode:${PHASE6_TOPOLOGY_MODE}"
}

phase6_require_load_tier() {
  case "${PHASE6_LOAD_TIER}" in
    low|medium|high)
      ;;
    *)
      fail_experiment "phase6_invalid_load_tier:${PHASE6_LOAD_TIER:-unset}"
      ;;
  esac
}

phase6_background_workers_for_tier() {
  case "${PHASE6_LOAD_TIER}" in
    low) printf '8\n' ;;
    medium) printf '16\n' ;;
    high) printf '32\n' ;;
    *) return 1 ;;
  esac
}

phase6_topology_profile() {
  case "${PHASE6_TOPOLOGY_MODE}" in
    local) printf 'local_single\n' ;;
    remote) printf 'remote_single\n' ;;
    *) return 1 ;;
  esac
}

phase6_prepare_environment() {
  local default_background
  default_background="$(phase6_background_workers_for_tier)" || fail_experiment "phase6_unknown_load_tier"
  if [[ -z "${BACKGROUND_LOAD_WORKERS:-}" || "${BACKGROUND_LOAD_WORKERS}" == "0" ]]; then
    export BACKGROUND_LOAD_WORKERS="${default_background}"
  fi
  if [[ -z "${BACKGROUND_LOAD_DURATION_SECONDS:-}" || "${BACKGROUND_LOAD_DURATION_SECONDS}" == "0" ]]; then
    export BACKGROUND_LOAD_DURATION_SECONDS="${EXPERIMENT_DURATION_SECONDS}"
  fi
}

phase6_target_url() {
  local node_port node_ip
  node_port="$(resolve_service_nodeport)"
  node_ip="$(resolve_target_node_ip)"
  printf 'http://%s:%s/\n' "${node_ip}" "${node_port}"
}

phase6_wait_for_nodeport_ready() {
  local url="$1"
  local timeout_seconds="${2:-${PHASE6_READY_TIMEOUT_SECONDS}}"
  local probes_required="${3:-${PHASE6_READY_PROBES_REQUIRED}}"
  local start now consecutive

  start="$(date +%s)"
  consecutive=0
  while true; do
    if http_get -fsS --max-time 2 "${url}" >/dev/null 2>&1; then
      consecutive=$((consecutive + 1))
      if (( consecutive >= probes_required )); then
        append_note "phase6_ready_url=${url}"
        append_note "phase6_ready_consecutive_successes=${consecutive}"
        return 0
      fi
    else
      consecutive=0
    fi

    now="$(date +%s)"
    if (( now - start >= timeout_seconds )); then
      append_note "phase6_ready_timeout_url=${url}"
      return 1
    fi
    sleep 1
  done
}

phase6_prepare_topology() {
  local profile
  prepare_topology_mode "${PHASE6_TOPOLOGY_MODE}" || fail_experiment "phase6_prepare_topology_failed:${PHASE6_TOPOLOGY_MODE}"
  profile="$(phase6_topology_profile)" || fail_experiment "phase6_topology_profile_failed"
  preflight_experiment_topology "${profile}" || fail_experiment "phase6_preflight_failed:${profile}"
}

phase6_append_common_notes() {
  append_note "phase6_topology_mode=${PHASE6_TOPOLOGY_MODE}"
  append_note "phase6_load_tier=${PHASE6_LOAD_TIER}"
  append_note "background_load_workers=${BACKGROUND_LOAD_WORKERS}"
  append_note "background_load_duration_seconds=${BACKGROUND_LOAD_DURATION_SECONDS}"
}
