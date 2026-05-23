#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "${SCRIPT_DIR}/common.sh"

setup_experiment "endpointslice-churn"
trap cleanup_experiment EXIT

preflight_two_node_experiment || fail_experiment "preflight_failed"

NODE_PORT="$(resolve_service_nodeport)"
NODE_IP="$(resolve_target_node_ip)"
TARGET_URL="http://${NODE_IP}:${NODE_PORT}/"

write_meta_json "endpointslice_churn" "${NODE_IP}" "${NODE_PORT}"
append_note "target_url=${TARGET_URL}"

clear_shared_events
start_collectors || fail_experiment "start_collectors_failed"
start_traffic "${TARGET_URL}"

sleep 5

EVENT_START_MS="$(now_ms)"
append_note "endpointslice_churn_start_ms=${EVENT_START_MS}"
APP_LABEL="$(deployment_app_label)"

for cycle in 1 2; do
  append_note "endpointslice_churn_cycle_${cycle}=scale_down"
  scale_target_deployment 1 || fail_experiment "scale_down_failed_cycle_${cycle}"
  sleep 2
  append_note "endpointslice_churn_cycle_${cycle}=scale_up"
  scale_target_deployment 2 || fail_experiment "scale_up_failed_cycle_${cycle}"
  assert_running_backends_on_experiment_nodes "${APP_LABEL}" || fail_experiment "backend_topology_unstable_cycle_${cycle}"
  sleep 2
done

EVENT_END_MS="$(now_ms)"
append_note "endpointslice_churn_end_ms=${EVENT_END_MS}"

append_event "${EVENT_START_MS}" "${EVENT_END_MS}" "backend_churn" "${RECOVERY_TAIL_MS}"
sync_events_to_agents || fail_experiment "sync_events_failed"

sleep 6

EXPERIMENT_END_MS="$(now_ms)"
append_note "experiment_end_ms=${EXPERIMENT_END_MS}"
stop_traffic

finalize_experiment_artifacts || fail_experiment "collect_phase2_artifacts_failed"
assert_all_experiment_csvs_have_rows || fail_experiment "csv_missing_rows"
assert_all_experiment_csvs_have_label "backend_churn" || fail_experiment "csv_missing_backend_churn"
assert_all_experiment_csvs_have_recovery || fail_experiment "csv_missing_recovery"
assert_running_backends_on_experiment_nodes "${APP_LABEL}" || fail_experiment "backend_topology_not_restored"

trap - EXIT
cleanup_experiment

echo "endpointslice churn experiment completed: ${EXPERIMENT_ID}"
