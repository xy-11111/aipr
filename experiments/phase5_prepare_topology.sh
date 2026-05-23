#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./phase5_common.sh
source "${SCRIPT_DIR}/phase5_common.sh"

MODE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="$2"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

[[ -n "${MODE}" ]] || fail_experiment "mode_required"
require_cluster_api
assert_selected_agents_running || fail_experiment "selected_agent_pods_not_running"
phase5_prepare_topology_mode "${MODE}" || fail_experiment "prepare_topology_failed:${MODE}"
echo "phase5 topology prepared: ${MODE}"
