#!/usr/bin/env bash

set -euo pipefail

RECOVERY_TAIL_MS="${RECOVERY_TAIL_MS:-5000}"

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "${SCRIPT_DIR}/common.sh"

setup_experiment "map-rebuild"
trap cleanup_experiment EXIT

preflight_two_node_experiment || fail_experiment "preflight_failed"

NODE_PORT="$(resolve_service_nodeport)"
NODE_IP="$(resolve_target_node_ip)"
TARGET_URL="http://${NODE_IP}:${NODE_PORT}/"

write_meta_json "map_rebuild" "${NODE_IP}" "${NODE_PORT}"
append_note "target_url=${TARGET_URL}"

clear_shared_events
start_collectors || fail_experiment "start_collectors_failed"
start_traffic "${TARGET_URL}"

sleep 5

EVENT_START_MS="$(now_ms)"
append_note "map_rebuild_start_ms=${EVENT_START_MS}"
run_targeted_oneshot_clear "${TARGET_NODE_NAME}" || fail_experiment "targeted_oneshot_clear_failed"
EVENT_END_MS="$(now_ms)"
append_note "map_rebuild_end_ms=${EVENT_END_MS}"

append_event "${EVENT_START_MS}" "${EVENT_END_MS}" "control_plane_recovery" "${RECOVERY_TAIL_MS}"
sync_events_to_agents || fail_experiment "sync_events_failed"

sleep 6

EXPERIMENT_END_MS="$(now_ms)"
append_note "experiment_end_ms=${EXPERIMENT_END_MS}"
stop_traffic

finalize_experiment_artifacts || fail_experiment "collect_phase2_artifacts_failed"
assert_all_experiment_csvs_have_rows || fail_experiment "csv_missing_rows"
assert_all_experiment_csvs_have_label "control_plane_recovery" || fail_experiment "csv_missing_control_plane_recovery"
assert_all_experiment_csvs_have_recovery || fail_experiment "csv_missing_recovery"
assert_traffic_has_success_between "${EVENT_END_MS}" "${EXPERIMENT_END_MS}" || fail_experiment "traffic_not_recovered_after_map_rebuild"

trap - EXIT
cleanup_experiment

echo "map rebuild experiment completed: ${EXPERIMENT_ID}"
