#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "${SCRIPT_DIR}/common.sh"

setup_experiment "backend-rollout-restart"
trap cleanup_experiment EXIT

preflight_two_node_experiment || fail_experiment "preflight_failed"

NODE_PORT="$(resolve_service_nodeport)"
NODE_IP="$(resolve_target_node_ip)"
TARGET_URL="http://${NODE_IP}:${NODE_PORT}/"

write_meta_json "backend_rollout_restart" "${NODE_IP}" "${NODE_PORT}"
append_note "target_url=${TARGET_URL}"

clear_shared_events
start_collectors || fail_experiment "start_collectors_failed"
start_traffic "${TARGET_URL}"

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
stop_traffic

finalize_experiment_artifacts || fail_experiment "collect_phase2_artifacts_failed"

while IFS= read -r node_name; do
  [[ -z "${node_name}" ]] && continue
  CSV_FILE="$(local_csv_path_for_node "${node_name}")"
  assert_csv_has_data_rows "${CSV_FILE}" || fail_experiment "csv_missing_rows_${node_name}"
  assert_csv_has_label "${CSV_FILE}" "backend_churn" || fail_experiment "csv_missing_backend_churn_${node_name}"
  assert_csv_has_recovery_active "${CSV_FILE}" || fail_experiment "csv_missing_recovery_${node_name}"
done < <(experiment_nodes)

assert_traffic_has_success_between "${ROLLOUT_START_MS}" "${ROLLOUT_END_MS}" || fail_experiment "traffic_fully_unavailable_during_rollout"

trap - EXIT
cleanup_experiment

echo "backend rollout restart experiment completed: ${EXPERIMENT_ID}"
