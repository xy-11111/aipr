#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./phase6_common.sh
source "${SCRIPT_DIR}/phase6_common.sh"

EXPERIMENT_DURATION_SECONDS="${EXPERIMENT_DURATION_SECONDS:-60}"

setup_experiment "phase6-conntrack-pressure"
trap cleanup_experiment EXIT

phase6_require_topology_mode "local,remote"
phase6_require_load_tier
phase6_prepare_environment
phase6_prepare_topology

NODE_PORT="$(resolve_service_nodeport)"
NODE_IP="$(resolve_target_node_ip)"
TARGET_URL="http://${NODE_IP}:${NODE_PORT}/"

write_meta_json "conntrack_pressure" "${NODE_IP}" "${NODE_PORT}"
append_note "target_url=${TARGET_URL}"
append_note "pressure_workers=${PRESSURE_WORKERS}"
append_note "pressure_duration_seconds=${PRESSURE_DURATION_SECONDS}"
phase6_append_common_notes

clear_shared_events
start_collectors || fail_experiment "start_collectors_failed"
start_traffic "${TARGET_URL}"
start_background_load "${TARGET_URL}"
phase6_wait_for_nodeport_ready "${TARGET_URL}" || fail_experiment "nodeport_not_ready"

sleep 5

EVENT_START_MS="$(now_ms)"
EVENT_END_MS=$(( EVENT_START_MS + (PRESSURE_DURATION_SECONDS * 1000) ))
append_note "conntrack_pressure_start_ms=${EVENT_START_MS}"
append_note "conntrack_pressure_end_ms=${EVENT_END_MS}"
append_event "${EVENT_START_MS}" "${EVENT_END_MS}" "conntrack_pressure" "${RECOVERY_TAIL_MS}"
sync_events_to_agents || fail_experiment "sync_events_failed"
run_pressure_traffic "${TARGET_URL}" "${PRESSURE_WORKERS}" "${PRESSURE_DURATION_SECONDS}"
wait_pressure_traffic

sleep 6

EXPERIMENT_END_MS="$(now_ms)"
append_note "experiment_end_ms=${EXPERIMENT_END_MS}"
stop_background_load
stop_traffic

finalize_experiment_artifacts || fail_experiment "collect_phase6_artifacts_failed"
assert_all_experiment_csvs_have_rows || fail_experiment "csv_missing_rows"
assert_all_experiment_csvs_have_label "conntrack_pressure" || fail_experiment "csv_missing_conntrack_pressure"
assert_all_experiment_csvs_have_recovery || fail_experiment "csv_missing_recovery"
assert_traffic_has_success_between "${EVENT_END_MS}" "${EXPERIMENT_END_MS}" || fail_experiment "traffic_not_recovered_after_conntrack_pressure"

pressure_peak_found=0
while IFS= read -r node_name; do
  [[ -z "${node_name}" ]] && continue
  CSV_FILE="$(local_csv_path_for_node "${node_name}")"
  if assert_csv_metric_peak_during_window_exceeds_before_window "${CSV_FILE}" "ct_active_count" "${EVENT_START_MS}" "${EVENT_END_MS}"; then
    pressure_peak_found=1
    break
  fi
done < <(experiment_nodes)
[[ "${pressure_peak_found}" -eq 1 ]] || fail_experiment "ct_active_count_did_not_increase"

trap - EXIT
cleanup_experiment

echo "phase6 conntrack pressure experiment completed: ${EXPERIMENT_ID}"
