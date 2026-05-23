#!/usr/bin/env bash

set -euo pipefail

RECOVERY_TAIL_MS="${RECOVERY_TAIL_MS:-5000}"
SERVICE_RECREATE_DELAY_SECONDS="${SERVICE_RECREATE_DELAY_SECONDS:-0}"

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "${SCRIPT_DIR}/common.sh"

setup_experiment "service-delete-recreate"
trap cleanup_experiment EXIT

preflight_two_node_experiment || fail_experiment "preflight_failed"

NODE_PORT="$(resolve_service_nodeport)"
NODE_IP="$(resolve_target_node_ip)"
TARGET_URL="http://${NODE_IP}:${NODE_PORT}/"
SERVICE_SNAPSHOT_FILE="${ARTIFACT_DIR}/service-live.yaml"
SERVICE_RESTORE_FILE="${ARTIFACT_DIR}/service-restore.yaml"

write_meta_json "service_delete_recreate" "${NODE_IP}" "${NODE_PORT}"
append_note "target_url=${TARGET_URL}"
append_note "service_recreate_delay_seconds=${SERVICE_RECREATE_DELAY_SECONDS}"

kctl get svc "${TARGET_SERVICE}" -n "${TARGET_NAMESPACE}" -o yaml > "${SERVICE_SNAPSHOT_FILE}"
cat > "${SERVICE_RESTORE_FILE}" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: ${TARGET_SERVICE}
  namespace: ${TARGET_NAMESPACE}
spec:
  type: NodePort
  externalTrafficPolicy: Cluster
  selector:
    app: nodeport-echo
  ports:
    - name: http
      port: 80
      targetPort: 8080
      nodePort: 30080
      protocol: TCP
EOF

clear_shared_events
start_collectors || fail_experiment "start_collectors_failed"
start_traffic "${TARGET_URL}"

sleep 5

EVENT_START_MS="$(now_ms)"
append_note "service_delete_start_ms=${EVENT_START_MS}"
if ! kctl delete svc "${TARGET_SERVICE}" -n "${TARGET_NAMESPACE}" >/dev/null; then
  fail_experiment "delete_service_failed"
fi
if ! wait_service_absent 30; then
  kctl apply -f "${SERVICE_RESTORE_FILE}" >/dev/null 2>&1 || true
  fail_experiment "service_not_deleted"
fi
sleep "${SERVICE_RECREATE_DELAY_SECONDS}"
if ! kctl apply -f "${SERVICE_RESTORE_FILE}" >/dev/null; then
  fail_experiment "service_recreate_failed"
fi
preflight_two_node_experiment || fail_experiment "post_service_recreate_preflight_failed"
EVENT_END_MS="$(now_ms)"
append_note "service_recreate_end_ms=${EVENT_END_MS}"

append_event "${EVENT_START_MS}" "${EVENT_END_MS}" "service_reconcile" "${RECOVERY_TAIL_MS}"
sync_events_to_agents || fail_experiment "sync_events_failed"

sleep 6

EXPERIMENT_END_MS="$(now_ms)"
append_note "experiment_end_ms=${EXPERIMENT_END_MS}"
stop_traffic

finalize_experiment_artifacts || fail_experiment "collect_phase2_artifacts_failed"
assert_all_experiment_csvs_have_rows || fail_experiment "csv_missing_rows"
assert_all_experiment_csvs_have_label "service_reconcile" || fail_experiment "csv_missing_service_reconcile"
assert_all_experiment_csvs_have_recovery || fail_experiment "csv_missing_recovery"
assert_traffic_has_success_between "${EVENT_END_MS}" "${EXPERIMENT_END_MS}" || fail_experiment "traffic_not_recovered_after_service_recreate"

trap - EXIT
cleanup_experiment

echo "service delete/recreate experiment completed: ${EXPERIMENT_ID}"
