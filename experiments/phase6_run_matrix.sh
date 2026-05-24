#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./phase6_common.sh
source "${SCRIPT_DIR}/phase6_common.sh"

PHASE6_DATASET_DIR="${ROOT_DIR}/datasets/phase6_generalization_v1"
PHASE6_SELECTION_FILE="${PHASE6_DATASET_DIR}/experiment_selection.json"
PHASE6_EXPERIMENT_DURATION_SECONDS="${PHASE6_EXPERIMENT_DURATION_SECONDS:-60}"

declare -a PHASE6_SUCCESS_IDS=()
declare -a PHASE6_FAILED_IDS=()

phase6_write_selection_file() {
  mkdir -p "${PHASE6_DATASET_DIR}"
  python3 - "${PHASE6_SELECTION_FILE}" "${PHASE6_SUCCESS_IDS[@]:-}" -- "${PHASE6_FAILED_IDS[@]:-}" <<'PY'
import json
import sys

out = sys.argv[1]
args = sys.argv[2:]
sep = args.index("--") if "--" in args else len(args)
included = args[:sep]
excluded = args[sep + 1 :] if sep < len(args) else []
payload = {
    "included_experiments": [item for item in included if item],
    "excluded_experiments": [item for item in excluded if item],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
    handle.write("\n")
PY
}

phase6_run_one() {
  local script_name="$1"
  local topology_mode="$2"
  local load_tier="$3"
  local family_slug="$4"
  local stamp experiment_id background_workers

  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  experiment_id="phase6-${topology_mode}-${load_tier}-${family_slug}-${stamp}"
  case "${load_tier}" in
    low) background_workers=8 ;;
    medium) background_workers=16 ;;
    high) background_workers=32 ;;
    *) fail_experiment "unknown_phase6_load_tier:${load_tier}" ;;
  esac

  echo "==> running ${experiment_id}"
  if env \
    PHASE6_TOPOLOGY_MODE="${topology_mode}" \
    PHASE6_LOAD_TIER="${load_tier}" \
    EXPERIMENT_DURATION_SECONDS="${PHASE6_EXPERIMENT_DURATION_SECONDS}" \
    BACKGROUND_LOAD_WORKERS="${background_workers}" \
    BACKGROUND_LOAD_DURATION_SECONDS="${PHASE6_EXPERIMENT_DURATION_SECONDS}" \
    EXPERIMENT_ID="${experiment_id}" \
    bash "${SCRIPT_DIR}/${script_name}"; then
    PHASE6_SUCCESS_IDS+=("${experiment_id}")
  else
    echo "phase6 experiment failed: ${experiment_id}" >&2
    PHASE6_FAILED_IDS+=("${experiment_id}")
  fi
}

main() {
  require_cluster_api
  assert_selected_agents_running || fail_experiment "selected_agent_pods_not_running"
  restore_balanced_topology || fail_experiment "restore_balanced_topology_failed"
  trap 'restore_balanced_topology >/dev/null 2>&1 || true' EXIT

  phase6_run_one "phase6_normal_steady_state.sh" "local" "low" "normal"
  phase6_run_one "phase6_normal_steady_state.sh" "remote" "low" "normal"
  phase6_run_one "phase6_normal_steady_state.sh" "local" "medium" "normal"
  phase6_run_one "phase6_normal_steady_state.sh" "remote" "medium" "normal"
  phase6_run_one "phase6_normal_steady_state.sh" "local" "high" "normal"
  phase6_run_one "phase6_normal_steady_state.sh" "remote" "high" "normal"

  phase6_run_one "phase6_backend_churn.sh" "local" "medium" "backend-churn"
  phase6_run_one "phase6_backend_churn.sh" "remote" "medium" "backend-churn"
  phase6_run_one "phase6_backend_churn.sh" "local" "high" "backend-churn"
  phase6_run_one "phase6_backend_churn.sh" "remote" "high" "backend-churn"

  phase6_run_one "phase6_conntrack_pressure.sh" "local" "medium" "conntrack-pressure"
  phase6_run_one "phase6_conntrack_pressure.sh" "remote" "medium" "conntrack-pressure"
  phase6_run_one "phase6_conntrack_pressure.sh" "local" "high" "conntrack-pressure"
  phase6_run_one "phase6_conntrack_pressure.sh" "remote" "high" "conntrack-pressure"

  phase6_run_one "phase6_path_degradation.sh" "remote" "medium" "path-degradation"
  phase6_run_one "phase6_path_degradation.sh" "remote" "high" "path-degradation"

  phase6_write_selection_file
  restore_balanced_topology || fail_experiment "final_restore_balanced_topology_failed"
  trap - EXIT

  echo "phase6 successful experiments: ${#PHASE6_SUCCESS_IDS[@]}"
  echo "phase6 failed experiments: ${#PHASE6_FAILED_IDS[@]}"
  printf '%s\n' "${PHASE6_SUCCESS_IDS[@]}"
}

main "$@"
