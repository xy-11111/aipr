#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PHASE5_SCENARIOS="${PHASE5_SCENARIOS:-off-local,on-local,off-remote,on-remote}"
# shellcheck source=./phase5_common.sh
source "${SCRIPT_DIR}/phase5_common.sh"

phase5_run_scenario() {
  local telemetry_mode="$1"
  local topology_mode="$2"
  local scenario_name url node_port node_ip
  local w1_host_start w2_host_start w1_host_end w2_host_end
  local w1_total_start w1_idle_start w2_total_start w2_idle_start
  local w1_total_end w1_idle_end w2_total_end w2_idle_end
  local w1_agent_start w2_agent_start w1_agent_end w2_agent_end
  local wall_start_ms wall_end_ms wall_duration_ms

  scenario_name="$(phase5_scenario_name "${telemetry_mode}" "${topology_mode}")"
  export EXPERIMENT_ID="${scenario_name}"
  rm -rf "${PHASE5_RAW_ROOT}/${scenario_name}"
  setup_experiment "${scenario_name}"
  phase5_require_python
  append_note "phase5_topology=${topology_mode}"
  append_note "phase5_telemetry=${telemetry_mode}"
  append_note "bench_warmup_seconds=${BENCH_WARMUP_SECONDS}"
  append_note "bench_duration_seconds=${BENCH_DURATION_SECONDS}"
  append_note "bench_concurrency=${BENCH_CONCURRENCY}"

  phase5_prepare_topology_mode "${topology_mode}" || fail_experiment "prepare_topology_failed:${scenario_name}"
  node_port="$(resolve_service_nodeport)"
  node_ip="$(resolve_target_node_ip)"
  url="http://${node_ip}:${node_port}/"
  write_meta_json "${scenario_name}" "${node_ip}" "${node_port}"
  append_note "target_url=${url}"

  clear_shared_events
  phase5_toggle_telemetry "${telemetry_mode}" || fail_experiment "telemetry_toggle_failed:${scenario_name}"
  phase5_wait_for_nodeport_ready "${url}" || fail_experiment "nodeport_not_ready:${scenario_name}"

  read -r w1_total_start w1_idle_start <<< "$(phase5_snapshot_host_cpu k8s-worker1)"
  read -r w2_total_start w2_idle_start <<< "$(phase5_snapshot_host_cpu k8s-worker2)"
  w1_agent_start="$(phase5_snapshot_agent_cpu_usec k8s-worker1)"
  w2_agent_start="$(phase5_snapshot_agent_cpu_usec k8s-worker2)"
  wall_start_ms="$(now_ms)"

  phase5_run_bench "${url}" "${ARTIFACT_DIR}" || fail_experiment "bench_failed:${scenario_name}"

  wall_end_ms="$(now_ms)"
  wall_duration_ms=$((wall_end_ms - wall_start_ms))
  read -r w1_total_end w1_idle_end <<< "$(phase5_snapshot_host_cpu k8s-worker1)"
  read -r w2_total_end w2_idle_end <<< "$(phase5_snapshot_host_cpu k8s-worker2)"
  w1_agent_end="$(phase5_snapshot_agent_cpu_usec k8s-worker1)"
  w2_agent_end="$(phase5_snapshot_agent_cpu_usec k8s-worker2)"

  phase5_write_cpu_summary \
    "${ARTIFACT_DIR}/cpu_summary.json" \
    "${wall_duration_ms}" \
    "${w1_total_start}" "${w1_idle_start}" "${w1_total_end}" "${w1_idle_end}" \
    "${w2_total_start}" "${w2_idle_start}" "${w2_total_end}" "${w2_idle_end}" \
    "${w1_agent_start}" "${w1_agent_end}" "${w2_agent_start}" "${w2_agent_end}"

  if [[ "${telemetry_mode}" == "on" ]]; then
    finalize_experiment_artifacts || fail_experiment "collect_phase5_artifacts_failed:${scenario_name}"
    assert_all_experiment_csvs_have_rows || fail_experiment "telemetry_csv_missing_rows:${scenario_name}"
  else
    stop_collectors
    collect_agent_logs || fail_experiment "collect_agent_logs_failed:${scenario_name}"
  fi

  cleanup_experiment
}

phase5_run_selected_scenarios() {
  local item telemetry topology
  IFS=',' read -r -a scenario_items <<< "${PHASE5_SCENARIOS}"
  for item in "${scenario_items[@]}"; do
    item="$(printf '%s' "${item}" | xargs)"
    [[ -z "${item}" ]] && continue
    case "${item}" in
      off-local)
        telemetry="off"
        topology="local"
        ;;
      on-local)
        telemetry="on"
        topology="local"
        ;;
      off-remote)
        telemetry="off"
        topology="remote"
        ;;
      on-remote)
        telemetry="on"
        topology="remote"
        ;;
      *)
        fail_experiment "unknown_phase5_scenario:${item}"
        ;;
    esac
    phase5_run_scenario "${telemetry}" "${topology}"
  done
}

main() {
  require_cluster_api
  assert_selected_agents_running || fail_experiment "selected_agent_pods_not_running"
  phase5_restore_balanced_topology || fail_experiment "restore_balanced_topology_failed"

  trap 'phase5_restore_balanced_topology >/dev/null 2>&1 || true; cleanup_experiment' EXIT

  phase5_run_selected_scenarios

  phase5_restore_balanced_topology || fail_experiment "final_restore_failed"
  "${BENCH_PYTHON}" "${ROOT_DIR}/scripts/phase5_export_paper_assets.py" || fail_experiment "phase5_export_failed"
  trap - EXIT
}

main "$@"
