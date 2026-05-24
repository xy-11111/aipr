#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./phase6_common.sh
source "${SCRIPT_DIR}/phase6_common.sh"

EXPERIMENT_DURATION_SECONDS="${EXPERIMENT_DURATION_SECONDS:-60}"

setup_experiment "phase6-backend-churn"
trap cleanup_experiment EXIT

phase6_require_topology_mode "local,remote"
phase6_require_load_tier
phase6_prepare_environment
phase6_prepare_topology

NODE_PORT="$(resolve_service_nodeport)"
NODE_IP="$(resolve_target_node_ip)"
TARGET_URL="http://${NODE_IP}:${NODE_PORT}/"

write_meta_json "backend_rollout_restart" "${NODE_IP}" "${NODE_PORT}"
append_note "target_url=${TARGET_URL}"
phase6_append_common_notes

clear_shared_events
start_collectors || fail_experiment "start_collectors_failed"
start_traffic "${TARGET_URL}"
start_background_load "${TARGET_URL}"
phase6_wait_for_nodeport_ready "${TARGET_URL}" || fail_experiment "nodeport_not_ready"

sleep 5

ROLLOUT_START_MS="$(now_ms)"
append_note "rollout_restart_start_ms=${ROLLOUT_START_MS}"
if ! kctl rollout restart "deployment/${TARGET_DEPLOYMENT}" -n "${TARGET_NAMESPACE}"; then
  fail_experiment "rollout_restart_command_failed"
fi
if ! kctl rollout status "deployment/${TARGET_DEPLOYMENT}" -n "${TARGET_NAMESPACE}" --timeout="${ROLLOUT_TIMEOUT}"; then
  fail_experiment "rollout_status_failed"
fi
ROLLOUT_END_MS="$(now_ms)"
append_note "rollout_restart_end_ms=${ROLLOUT_END_MS}"

append_event "${ROLLOUT_START_MS}" "${ROLLOUT_END_MS}" "backend_churn" "${RECOVERY_TAIL_MS}"
sync_events_to_agents || fail_experiment "sync_events_failed"

sleep 5

EXPERIMENT_END_MS="$(now_ms)"
append_note "experiment_end_ms=${EXPERIMENT_END_MS}"
stop_background_load
stop_traffic

finalize_experiment_artifacts || fail_experiment "collect_phase6_artifacts_failed"
assert_all_experiment_csvs_have_rows || fail_experiment "csv_missing_rows"
assert_all_experiment_csvs_have_label "backend_churn" || fail_experiment "csv_missing_backend_churn"
assert_all_experiment_csvs_have_recovery || fail_experiment "csv_missing_recovery"
assert_traffic_has_success_between "${ROLLOUT_START_MS}" "${ROLLOUT_END_MS}" || fail_experiment "traffic_fully_unavailable_during_rollout"

trap - EXIT
cleanup_experiment

echo "phase6 backend churn experiment completed: ${EXPERIMENT_ID}"
