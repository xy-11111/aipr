#!/usr/bin/env bash

set -euo pipefail

RECOVERY_TAIL_MS="${RECOVERY_TAIL_MS:-5000}"

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "${SCRIPT_DIR}/common.sh"

setup_experiment "agent-restart"
trap cleanup_experiment EXIT

preflight_two_node_experiment || fail_experiment "preflight_failed"

NODE_PORT="$(resolve_service_nodeport)"
NODE_IP="$(resolve_target_node_ip)"
TARGET_URL="http://${NODE_IP}:${NODE_PORT}/"

write_meta_json "agent_restart" "${NODE_IP}" "${NODE_PORT}"
append_note "target_url=${TARGET_URL}"
append_note "fault_node_name=${FAULT_NODE_NAME}"

clear_shared_events
start_collectors || fail_experiment "start_collectors_failed"
start_traffic "${TARGET_URL}"

sleep 5

FAULT_POD_OLD="$(wait_agent_pod_for_node "${FAULT_NODE_NAME}")" || fail_experiment "fault_agent_missing_before_delete"
EVENT_START_MS="$(now_ms)"
append_note "fault_agent_old_pod=${FAULT_POD_OLD}"
append_note "agent_restart_start_ms=${EVENT_START_MS}"

if ! kctl delete pod -n "${SYSTEM_NAMESPACE}" "${FAULT_POD_OLD}" --wait=false >/dev/null; then
  fail_experiment "delete_fault_agent_failed"
fi

FAULT_POD_NEW=""
deadline=$(( $(date +%s) + AGENT_READY_TIMEOUT_SECONDS ))
while (( $(date +%s) < deadline )); do
  current_pod="$(wait_agent_pod_for_node "${FAULT_NODE_NAME}" 5 || true)"
  if [[ -n "${current_pod}" && "${current_pod}" != "${FAULT_POD_OLD}" ]]; then
    FAULT_POD_NEW="${current_pod}"
    break
  fi
  sleep 2
done
[[ -n "${FAULT_POD_NEW}" ]] || fail_experiment "fault_agent_new_pod_not_ready"

EVENT_END_MS="$(now_ms)"
append_note "fault_agent_new_pod=${FAULT_POD_NEW}"
append_note "agent_restart_end_ms=${EVENT_END_MS}"

append_event "${EVENT_START_MS}" "${EVENT_END_MS}" "control_plane_recovery" "${RECOVERY_TAIL_MS}"
sync_events_to_agents || fail_experiment "sync_events_failed"
restart_collector_for_node "${FAULT_NODE_NAME}" || fail_experiment "restart_fault_collector_failed"

sleep 6

EXPERIMENT_END_MS="$(now_ms)"
append_note "experiment_end_ms=${EXPERIMENT_END_MS}"
stop_traffic

finalize_experiment_artifacts || fail_experiment "collect_phase2_artifacts_failed"
assert_all_experiment_csvs_have_rows || fail_experiment "csv_missing_rows"
assert_all_experiment_csvs_have_label "control_plane_recovery" || fail_experiment "csv_missing_control_plane_recovery"
assert_all_experiment_csvs_have_recovery || fail_experiment "csv_missing_recovery"
assert_traffic_has_success_between "${EVENT_START_MS}" "${EVENT_END_MS}" || fail_experiment "traffic_no_success_during_agent_restart"

trap - EXIT
cleanup_experiment

echo "agent restart experiment completed: ${EXPERIMENT_ID}"
