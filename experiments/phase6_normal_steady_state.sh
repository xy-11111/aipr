#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./phase6_common.sh
source "${SCRIPT_DIR}/phase6_common.sh"

EXPERIMENT_DURATION_SECONDS="${EXPERIMENT_DURATION_SECONDS:-60}"

setup_experiment "phase6-normal-steady-state"
trap cleanup_experiment EXIT

phase6_require_topology_mode "local,remote"
phase6_require_load_tier
phase6_prepare_environment
phase6_prepare_topology

NODE_PORT="$(resolve_service_nodeport)"
NODE_IP="$(resolve_target_node_ip)"
TARGET_URL="http://${NODE_IP}:${NODE_PORT}/"

write_meta_json "normal_steady_state" "${NODE_IP}" "${NODE_PORT}"
append_note "target_url=${TARGET_URL}"
phase6_append_common_notes

clear_shared_events
start_collectors || fail_experiment "start_collectors_failed"
start_traffic "${TARGET_URL}"
start_background_load "${TARGET_URL}"
phase6_wait_for_nodeport_ready "${TARGET_URL}" || fail_experiment "nodeport_not_ready"

START_MS="$(now_ms)"
append_note "start_ms=${START_MS}"
sleep "${EXPERIMENT_DURATION_SECONDS}"
append_note "end_ms=$(now_ms)"

stop_background_load
stop_traffic
finalize_experiment_artifacts || fail_experiment "collect_phase6_artifacts_failed"

while IFS= read -r node_name; do
  [[ -z "${node_name}" ]] && continue
  CSV_FILE="$(local_csv_path_for_node "${node_name}")"
  assert_csv_has_data_rows "${CSV_FILE}" || fail_experiment "csv_missing_rows_${node_name}"
  assert_csv_all_label "${CSV_FILE}" "normal" || fail_experiment "csv_non_normal_label_${node_name}"
done < <(experiment_nodes)

assert_traffic_has_success_between "${START_MS}" "$(now_ms)" || fail_experiment "traffic_no_success_during_baseline"

trap - EXIT
cleanup_experiment

echo "phase6 normal steady state experiment completed: ${EXPERIMENT_ID}"
