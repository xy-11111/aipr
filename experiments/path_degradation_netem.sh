#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "${SCRIPT_DIR}/common.sh"

setup_experiment "path-degradation-netem"
trap cleanup_experiment EXIT

preflight_two_node_experiment || fail_experiment "preflight_failed"

NODE_PORT="$(resolve_service_nodeport)"
NODE_IP="$(resolve_target_node_ip)"
TARGET_URL="http://${NODE_IP}:${NODE_PORT}/"

write_meta_json "path_degradation_netem" "${NODE_IP}" "${NODE_PORT}"
append_note "target_url=${TARGET_URL}"
append_note "netem_iface=${TARGET_NETEM_IFACE}"

clear_shared_events
start_collectors || fail_experiment "start_collectors_failed"
start_traffic "${TARGET_URL}"

sleep 5

EVENT_START_MS="$(now_ms)"
EVENT_END_MS=$(( EVENT_START_MS + (INJECTION_DURATION_SECONDS * 1000) ))
append_note "path_degradation_start_ms=${EVENT_START_MS}"
append_note "path_degradation_end_ms=${EVENT_END_MS}"
append_event "${EVENT_START_MS}" "${EVENT_END_MS}" "path_degradation" "${RECOVERY_TAIL_MS}"
sync_events_to_agents || fail_experiment "sync_events_failed"
apply_netem_on_node "${TARGET_NODE_NAME}" "${TARGET_NETEM_IFACE}" "${NETEM_DELAY_MS:-100}" "${NETEM_LOSS_PERCENT:-5}" || fail_experiment "apply_netem_failed"

sleep "${INJECTION_DURATION_SECONDS}"
clear_netem_on_node "${TARGET_NODE_NAME}" "${TARGET_NETEM_IFACE}"

sleep 6

EXPERIMENT_END_MS="$(now_ms)"
append_note "experiment_end_ms=${EXPERIMENT_END_MS}"
stop_traffic

finalize_experiment_artifacts || fail_experiment "collect_phase2_artifacts_failed"
assert_all_experiment_csvs_have_rows || fail_experiment "csv_missing_rows"
assert_all_experiment_csvs_have_label "path_degradation" || fail_experiment "csv_missing_path_degradation"
assert_all_experiment_csvs_have_recovery || fail_experiment "csv_missing_recovery"
assert_traffic_has_success_between "${EVENT_END_MS}" "${EXPERIMENT_END_MS}" || fail_experiment "traffic_not_recovered_after_netem"

trap - EXIT
cleanup_experiment

echo "path degradation netem experiment completed: ${EXPERIMENT_ID}"
