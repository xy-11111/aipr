#!/usr/bin/env bash

set -euo pipefail

PHASE5_SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${PHASE5_SCRIPT_DIR}/.." && pwd)"
PAPER_ASSETS_ROOT="${ROOT_DIR}/paper_assets"
PHASE5_ROOT="${PAPER_ASSETS_ROOT}/phase5_v1"
PHASE5_RAW_ROOT="${PHASE5_ROOT}/raw"
PHASE5_TABLES_DIR="${PHASE5_ROOT}/tables"
PHASE5_FIGURES_DIR="${PHASE5_ROOT}/figures"
BENCH_WARMUP_SECONDS="${BENCH_WARMUP_SECONDS:-10}"
BENCH_DURATION_SECONDS="${BENCH_DURATION_SECONDS:-60}"
BENCH_CONCURRENCY="${BENCH_CONCURRENCY:-16}"
BENCH_PYTHON="${BENCH_PYTHON:-${ROOT_DIR}/.venv-phase4/bin/python}"

export ARTIFACTS_ROOT="${PHASE5_RAW_ROOT}"

# shellcheck source=./common.sh
source "${ROOT_DIR}/experiments/common.sh"

mkdir -p "${PHASE5_ROOT}" "${PHASE5_RAW_ROOT}" "${PHASE5_TABLES_DIR}" "${PHASE5_FIGURES_DIR}"

phase5_require_python() {
  [[ -x "${BENCH_PYTHON}" ]] || fail_experiment "phase5_python_missing:${BENCH_PYTHON}"
}

phase5_scenario_name() {
  local telemetry_mode="$1"
  local topology_mode="$2"
  printf 'phase5-%s-%s\n' "${telemetry_mode}" "${topology_mode}"
}

phase5_target_backend_node() {
  local mode="$1"
  case "${mode}" in
    local) printf 'k8s-worker1\n' ;;
    remote) printf 'k8s-worker2\n' ;;
    *) return 1 ;;
  esac
}

phase5_backend_patch_payload() {
  local node_name="$1"
  cat <<EOF
{
  "spec": {
    "replicas": 1,
    "template": {
      "spec": {
        "affinity": {
          "nodeAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": {
              "nodeSelectorTerms": [
                {
                  "matchExpressions": [
                    {
                      "key": "kubernetes.io/hostname",
                      "operator": "In",
                      "values": ["${node_name}"]
                    }
                  ]
                }
              ]
            }
          },
          "podAntiAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": [
              {
                "labelSelector": {
                  "matchLabels": {
                    "app": "nodeport-echo"
                  }
                },
                "topologyKey": "kubernetes.io/hostname"
              }
            ]
          }
        }
      }
    }
  }
}
EOF
}

phase5_restore_balanced_topology() {
  kctl apply -f "${ROOT_DIR}/manifests/nodeport-test.yaml" >/dev/null
  kctl rollout status "deployment/${TARGET_DEPLOYMENT}" -n "${TARGET_NAMESPACE}" --timeout="${ROLLOUT_TIMEOUT}" >/dev/null
  preflight_two_node_experiment
}

phase5_assert_single_backend_on_node() {
  local expected_node="$1"
  local app_label rows count found_node
  app_label="$(deployment_app_label)"
  rows="$(kctl get pods \
    -n "${TARGET_NAMESPACE}" \
    -l "app=${app_label}" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{.spec.nodeName}{"|"}{.status.phase}{"|"}{.metadata.deletionTimestamp}{"|"}{range .status.containerStatuses[*]}{.ready}{" "}{end}{"\n"}{end}')"
  count=0
  found_node=""
  while IFS='|' read -r pod_name node_name phase deletion_ts ready_flags; do
    [[ -z "${pod_name}" ]] && continue
    [[ -n "${deletion_ts}" ]] && continue
    if [[ "${phase}" == "Running" && "${ready_flags}" == *"true"* ]]; then
      count=$((count + 1))
      found_node="${node_name}"
    fi
  done <<< "${rows}"
  [[ "${count}" -eq 1 ]] || return 1
  [[ "${found_node}" == "${expected_node}" ]] || return 1
}

phase5_wait_single_backend_on_node() {
  local expected_node="$1"
  local timeout_seconds="${2:-60}"
  local start now

  start="$(date +%s)"
  while true; do
    if phase5_assert_single_backend_on_node "${expected_node}"; then
      return 0
    fi
    now="$(date +%s)"
    if (( now - start >= timeout_seconds )); then
      return 1
    fi
    sleep 2
  done
}

phase5_prepare_topology_mode() {
  local mode="$1"
  local target_node
  target_node="$(phase5_target_backend_node "${mode}")" || return 1
  kctl patch deployment "${TARGET_DEPLOYMENT}" -n "${TARGET_NAMESPACE}" --type merge -p "$(phase5_backend_patch_payload "${target_node}")" >/dev/null
  kctl rollout status "deployment/${TARGET_DEPLOYMENT}" -n "${TARGET_NAMESPACE}" --timeout="${ROLLOUT_TIMEOUT}" >/dev/null
  phase5_wait_single_backend_on_node "${target_node}" 60
}

phase5_snapshot_host_cpu() {
  local node_name="$1"
  local pod
  pod="$(wait_agent_pod_for_node "${node_name}" 30)" || return 1
  kctl exec -n "${SYSTEM_NAMESPACE}" "${pod}" -- nsenter -t 1 -m -u -n -i sh -c \
    "awk '/^cpu / { idle=\$5+\$6; total=0; for (i=2; i<=NF; i++) total+=\$i; print total, idle; exit }' /proc/stat" 2>/dev/null
}

phase5_snapshot_agent_cpu_usec() {
  local node_name="$1"
  local pod
  pod="$(wait_agent_pod_for_node "${node_name}" 30)" || return 1
  kctl exec -n "${SYSTEM_NAMESPACE}" "${pod}" -- sh -c \
    "awk '/^usage_usec / { print \$2; exit }' /sys/fs/cgroup/cpu.stat 2>/dev/null || awk '/^usage_usec / { print \$2; exit }' /sys/fs/cgroup/cpu/cpu.stat 2>/dev/null" 2>/dev/null
}

phase5_host_cpu_pct_from_snapshots() {
  local start_total="$1"
  local start_idle="$2"
  local end_total="$3"
  local end_idle="$4"
  awk -v st="${start_total}" -v si="${start_idle}" -v et="${end_total}" -v ei="${end_idle}" '
    BEGIN {
      dt = et - st
      di = ei - si
      if (dt <= 0) {
        print "0.000"
      } else {
        printf "%.3f\n", ((dt - di) / dt) * 100.0
      }
    }
  '
}

phase5_agent_cpu_pct_from_snapshots() {
  local start_usage_usec="$1"
  local end_usage_usec="$2"
  local wall_ms="$3"
  awk -v su="${start_usage_usec}" -v eu="${end_usage_usec}" -v wm="${wall_ms}" '
    BEGIN {
      du = eu - su
      if (du < 0 || wm <= 0) {
        print "0.000"
      } else {
        printf "%.3f\n", (du / (wm * 1000.0)) * 100.0
      }
    }
  '
}

phase5_write_cpu_summary() {
  local output_file="$1"
  local wall_ms="$2"
  local w1_start_total="$3"
  local w1_start_idle="$4"
  local w1_end_total="$5"
  local w1_end_idle="$6"
  local w2_start_total="$7"
  local w2_start_idle="$8"
  local w2_end_total="$9"
  local w2_end_idle="${10}"
  local w1_agent_start="${11}"
  local w1_agent_end="${12}"
  local w2_agent_start="${13}"
  local w2_agent_end="${14}"

  local w1_node_pct w2_node_pct w1_agent_pct w2_agent_pct
  w1_node_pct="$(phase5_host_cpu_pct_from_snapshots "${w1_start_total}" "${w1_start_idle}" "${w1_end_total}" "${w1_end_idle}")"
  w2_node_pct="$(phase5_host_cpu_pct_from_snapshots "${w2_start_total}" "${w2_start_idle}" "${w2_end_total}" "${w2_end_idle}")"
  w1_agent_pct="$(phase5_agent_cpu_pct_from_snapshots "${w1_agent_start}" "${w1_agent_end}" "${wall_ms}")"
  w2_agent_pct="$(phase5_agent_cpu_pct_from_snapshots "${w2_agent_start}" "${w2_agent_end}" "${wall_ms}")"

  cat > "${output_file}" <<EOF
{
  "wall_duration_ms": ${wall_ms},
  "worker1_node_cpu_pct": ${w1_node_pct},
  "worker2_node_cpu_pct": ${w2_node_pct},
  "worker1_agent_cpu_pct": ${w1_agent_pct},
  "worker2_agent_cpu_pct": ${w2_agent_pct}
}
EOF
}

phase5_run_bench() {
  local url="$1"
  local output_dir="$2"
  "${BENCH_PYTHON}" "${ROOT_DIR}/scripts/phase5_http_bench.py" \
    --url "${url}" \
    --warmup-seconds "${BENCH_WARMUP_SECONDS}" \
    --duration-seconds "${BENCH_DURATION_SECONDS}" \
    --concurrency "${BENCH_CONCURRENCY}" \
    --output-dir "${output_dir}"
}

phase5_toggle_telemetry() {
  local mode="$1"
  clear_shared_events
  case "${mode}" in
    on) start_collectors ;;
    off) stop_collectors ;;
    *) return 1 ;;
  esac
}
