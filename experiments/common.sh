#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACTS_ROOT="${ARTIFACTS_ROOT:-${ROOT_DIR}/artifacts}"
SYSTEM_NAMESPACE="${SYSTEM_NAMESPACE:-ebpf-nodeport-system}"
SYSTEM_LABEL="${SYSTEM_LABEL:-app=ebpf-nodeport-agent}"
TARGET_NAMESPACE="${TARGET_NAMESPACE:-ebpf-nodeport-test}"
TARGET_SERVICE="${TARGET_SERVICE:-nodeport-echo}"
TARGET_DEPLOYMENT="${TARGET_DEPLOYMENT:-nodeport-echo}"
TARGET_NODE_NAME="${TARGET_NODE_NAME:-k8s-worker1}"
TARGET_NODE_IP="${TARGET_NODE_IP:-192.168.1.202}"
EXPERIMENT_NODE_NAMES="${EXPERIMENT_NODE_NAMES:-k8s-worker1,k8s-worker2}"
FAULT_NODE_NAME="${FAULT_NODE_NAME:-k8s-worker2}"
EXPERIMENT_DURATION_SECONDS="${EXPERIMENT_DURATION_SECONDS:-30}"
RECOVERY_TAIL_MS="${RECOVERY_TAIL_MS:-3000}"
REQUEST_PAUSE_SECONDS="${REQUEST_PAUSE_SECONDS:-0.2}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-180s}"
TELEMETRY_SHARED_EVENTS_FILE="${TELEMETRY_SHARED_EVENTS_FILE:-/var/log/ebpf-nodeport/telemetry-events/current.jsonl}"
TELEMETRY_OUTPUT_ROOT="${TELEMETRY_OUTPUT_ROOT:-/var/log/ebpf-nodeport/telemetry}"
TELEMETRY_RUNTIME_ROOT="${TELEMETRY_RUNTIME_ROOT:-/var/log/ebpf-nodeport/telemetry-runtime}"
TARGET_NETEM_IFACE="${TARGET_NETEM_IFACE:-flannel.1}"
INJECTION_DURATION_SECONDS="${INJECTION_DURATION_SECONDS:-15}"
PRESSURE_DURATION_SECONDS="${PRESSURE_DURATION_SECONDS:-15}"
PRESSURE_WORKERS="${PRESSURE_WORKERS:-20}"
BURST_DURATION_SECONDS="${BURST_DURATION_SECONDS:-15}"
BURST_WORKERS="${BURST_WORKERS:-40}"
BACKGROUND_LOAD_WORKERS="${BACKGROUND_LOAD_WORKERS:-0}"
BACKGROUND_LOAD_DURATION_SECONDS="${BACKGROUND_LOAD_DURATION_SECONDS:-${EXPERIMENT_DURATION_SECONDS}}"
AGENT_READY_TIMEOUT_SECONDS="${AGENT_READY_TIMEOUT_SECONDS:-120}"
SYNCER_DISCOVERY_TIMEOUT_SECONDS="${SYNCER_DISCOVERY_TIMEOUT_SECONDS:-45}"

EXPERIMENT_ID="${EXPERIMENT_ID:-}"
ARTIFACT_DIR=""
META_FILE=""
EVENTS_FILE=""
NOTES_FILE=""
TRAFFIC_LOG_FILE=""
TELEMETRY_DIR=""
AGENT_LOG_DIR=""
TRAFFIC_PID=""
declare -a PRESSURE_PIDS=()
declare -a BACKGROUND_PIDS=()
declare -a NETEM_ACTIVE=()

now_ms() {
  date +%s%3N
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing command: $1" >&2
    exit 1
  }
}

proxyless_env() {
  env \
    -u HTTP_PROXY \
    -u HTTPS_PROXY \
    -u ALL_PROXY \
    -u NO_PROXY \
    -u http_proxy \
    -u https_proxy \
    -u all_proxy \
    -u no_proxy \
    "$@"
}

kctl() {
  proxyless_env kubectl "$@"
}

kctl_timeout() {
  local timeout_value="$1"
  shift
  proxyless_env timeout --signal=TERM --kill-after=2s "${timeout_value}" kubectl "$@"
}

http_get() {
  proxyless_env curl "$@"
}

experiment_nodes() {
  tr ',' '\n' <<< "${EXPERIMENT_NODE_NAMES}" | awk 'NF'
}

record_failure() {
  append_note "failure_reason=$*"
}

fail_experiment() {
  local reason="$1"
  record_failure "${reason}"
  echo "${reason}" >&2
  exit 1
}

require_cluster_api() {
  kctl --request-timeout=10s get namespace kube-system >/dev/null
}

append_note() {
  printf '%s\n' "$*" >> "${NOTES_FILE}"
}

setup_experiment() {
  local prefix="$1"
  require_cmd kubectl
  require_cmd curl
  require_cmd awk
  require_cmd timeout
  require_cluster_api

  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  EXPERIMENT_ID="${EXPERIMENT_ID:-${prefix}-${stamp}}"
  ARTIFACT_DIR="${ARTIFACTS_ROOT}/${EXPERIMENT_ID}"
  META_FILE="${ARTIFACT_DIR}/meta.json"
  EVENTS_FILE="${ARTIFACT_DIR}/events.jsonl"
  NOTES_FILE="${ARTIFACT_DIR}/notes.txt"
  TRAFFIC_LOG_FILE="${ARTIFACT_DIR}/traffic.csv"
  TELEMETRY_DIR="${ARTIFACT_DIR}/telemetry"
  AGENT_LOG_DIR="${ARTIFACT_DIR}/agent-logs"

  mkdir -p "${ARTIFACT_DIR}" "${TELEMETRY_DIR}" "${AGENT_LOG_DIR}"
  : > "${EVENTS_FILE}"
  printf 'ts_unix_ms,ok\n' > "${TRAFFIC_LOG_FILE}"
  {
    echo "experiment_id=${EXPERIMENT_ID}"
    echo "created_at_utc=${stamp}"
    echo "target_node_name=${TARGET_NODE_NAME}"
    echo "target_node_ip=${TARGET_NODE_IP}"
    echo "experiment_nodes=${EXPERIMENT_NODE_NAMES}"
  } > "${NOTES_FILE}"
}

write_meta_json() {
  local scenario="$1"
  local node_ip="$2"
  local node_port="$3"

  cat > "${META_FILE}" <<EOF
{
  "experiment_id": "${EXPERIMENT_ID}",
  "scenario": "${scenario}",
  "target_namespace": "${TARGET_NAMESPACE}",
  "target_service": "${TARGET_SERVICE}",
  "target_deployment": "${TARGET_DEPLOYMENT}",
  "target_node_name": "${TARGET_NODE_NAME}",
  "node_ip": "${node_ip}",
  "node_port": ${node_port},
  "duration_seconds": ${EXPERIMENT_DURATION_SECONDS},
  "recovery_tail_ms": ${RECOVERY_TAIL_MS}
}
EOF
}

append_event() {
  local start_ms="$1"
  local end_ms="$2"
  local label="$3"
  local recovery_tail_ms="${4:-0}"
  local scope="${5:-service}"
  local target="${6:-${TARGET_NAMESPACE}/${TARGET_SERVICE}}"

  printf '{"ts_start_unix_ms":%s,"ts_end_unix_ms":%s,"label":"%s","scope":"%s","target":"%s","recovery_tail_ms":%s}\n' \
    "${start_ms}" "${end_ms}" "${label}" "${scope}" "${target}" "${recovery_tail_ms}" >> "${EVENTS_FILE}"
}

resolve_service_nodeport() {
  kctl get svc "${TARGET_SERVICE}" -n "${TARGET_NAMESPACE}" -o jsonpath='{.spec.ports[0].nodePort}'
}

resolve_target_node_ip() {
  if [[ -n "${TARGET_NODE_IP}" ]]; then
    printf '%s\n' "${TARGET_NODE_IP}"
    return
  fi

  kctl get node "${TARGET_NODE_NAME}" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}'
}

deployment_app_label() {
  kctl get deployment "${TARGET_DEPLOYMENT}" -n "${TARGET_NAMESPACE}" -o jsonpath='{.spec.selector.matchLabels.app}'
}

current_revision_hash() {
  local app_label="$1"

  kctl get rs \
    -n "${TARGET_NAMESPACE}" \
    -l "app=${app_label}" \
    -o jsonpath='{range .items[*]}{.metadata.annotations.deployment\.kubernetes\.io/revision}{"\t"}{.metadata.labels.pod-template-hash}{"\n"}{end}' |
    sort -k1,1n |
    tail -n 1 |
    cut -f2
}

agent_pod_for_node() {
  local node_name="$1"
  kctl get pods \
    -n "${SYSTEM_NAMESPACE}" \
    -l "${SYSTEM_LABEL}" \
    --field-selector="spec.nodeName=${node_name},status.phase=Running" \
    -o jsonpath='{.items[0].metadata.name}'
}

wait_agent_pod_for_node() {
  local node_name="$1"
  local timeout="${2:-${AGENT_READY_TIMEOUT_SECONDS}}"
  local start now rows pod phase ready_flags

  start="$(date +%s)"
  while true; do
    rows="$(kctl get pods \
      -n "${SYSTEM_NAMESPACE}" \
      -l "${SYSTEM_LABEL}" \
      --field-selector="spec.nodeName=${node_name}" \
      -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.phase}{"\t"}{range .status.containerStatuses[*]}{.ready}{" "}{end}{"\n"}{end}' || true)"
    while IFS=$'\t' read -r pod phase ready_flags; do
      [[ -z "${pod}" ]] && continue
      if [[ "${phase}" == "Running" && "${ready_flags}" == *"true"* ]]; then
        printf '%s\n' "${pod}"
        return 0
      fi
    done <<< "${rows}"

    now="$(date +%s)"
    if (( now - start >= timeout )); then
      return 1
    fi
    sleep 2
  done
}

sync_events_to_agents() {
  local shared_dir
  shared_dir="$(dirname "${TELEMETRY_SHARED_EVENTS_FILE}")"

  while IFS= read -r node_name; do
    [[ -z "${node_name}" ]] && continue
    local pod
    pod="$(wait_agent_pod_for_node "${node_name}" 30)" || return 1
    kctl exec -n "${SYSTEM_NAMESPACE}" "${pod}" -- mkdir -p "${shared_dir}" >/dev/null
    kctl exec -i -n "${SYSTEM_NAMESPACE}" "${pod}" -- sh -c "cat > '${TELEMETRY_SHARED_EVENTS_FILE}'" < "${EVENTS_FILE}"
  done < <(experiment_nodes)
}

clear_shared_events() {
  : > "${EVENTS_FILE}"
  sync_events_to_agents
}

start_traffic() {
  local url="$1"
  (
    while true; do
      local ok=0
      if http_get -fsS --max-time 2 "${url}" >/dev/null; then
        ok=1
      fi
      printf '%s,%s\n' "$(now_ms)" "${ok}" >> "${TRAFFIC_LOG_FILE}"
      sleep "${REQUEST_PAUSE_SECONDS}"
    done
  ) &
  TRAFFIC_PID="$!"
  append_note "traffic_pid=${TRAFFIC_PID}"
}

stop_traffic() {
  if [[ -n "${TRAFFIC_PID}" ]]; then
    kill "${TRAFFIC_PID}" 2>/dev/null || true
    wait "${TRAFFIC_PID}" 2>/dev/null || true
    TRAFFIC_PID=""
  fi
}

run_pressure_traffic() {
  local url="$1"
  local workers="$2"
  local duration_seconds="$3"
  local end_epoch
  end_epoch=$(( $(date +%s) + duration_seconds ))

  PRESSURE_PIDS=()
  for _ in $(seq 1 "${workers}"); do
    (
      while (( $(date +%s) < end_epoch )); do
        http_get -fsS --max-time 2 "${url}" >/dev/null || true
      done
    ) &
    PRESSURE_PIDS+=("$!")
  done
}

wait_pressure_traffic() {
  local pid
  for pid in "${PRESSURE_PIDS[@]:-}"; do
    [[ -n "${pid}" ]] || continue
    wait "${pid}" 2>/dev/null || true
  done
  PRESSURE_PIDS=()
}

stop_pressure_traffic() {
  local pid
  for pid in "${PRESSURE_PIDS[@]:-}"; do
    [[ -n "${pid}" ]] || continue
    kill "${pid}" 2>/dev/null || true
  done
  wait_pressure_traffic
}

start_background_load() {
  local url="$1"
  local workers="${BACKGROUND_LOAD_WORKERS}"
  local duration_seconds="${BACKGROUND_LOAD_DURATION_SECONDS}"
  local end_epoch

  BACKGROUND_PIDS=()
  if [[ "${workers}" -le 0 ]]; then
    return 0
  fi

  append_note "background_load_workers=${workers}"
  append_note "background_load_duration_seconds=${duration_seconds}"
  end_epoch=$(( $(date +%s) + duration_seconds ))

  for _ in $(seq 1 "${workers}"); do
    (
      while (( $(date +%s) < end_epoch )); do
        http_get -fsS --max-time 2 "${url}" >/dev/null || true
      done
    ) &
    BACKGROUND_PIDS+=("$!")
  done
}

wait_background_load() {
  local pid
  for pid in "${BACKGROUND_PIDS[@]:-}"; do
    [[ -n "${pid}" ]] || continue
    wait "${pid}" 2>/dev/null || true
  done
  BACKGROUND_PIDS=()
}

stop_background_load() {
  local pid
  for pid in "${BACKGROUND_PIDS[@]:-}"; do
    [[ -n "${pid}" ]] || continue
    kill "${pid}" 2>/dev/null || true
  done
  wait_background_load
}

assert_selected_agents_running() {
  while IFS= read -r node_name; do
    [[ -z "${node_name}" ]] && continue
    wait_agent_pod_for_node "${node_name}" 20 >/dev/null || return 1
  done < <(experiment_nodes)
}

deployment_ready_replicas() {
  kctl get deployment "${TARGET_DEPLOYMENT}" -n "${TARGET_NAMESPACE}" -o jsonpath='{.status.readyReplicas}'
}

assert_deployment_ready() {
  kctl rollout status "deployment/${TARGET_DEPLOYMENT}" -n "${TARGET_NAMESPACE}" --timeout=20s >/dev/null

  local replicas ready updated available
  replicas="$(kctl get deployment "${TARGET_DEPLOYMENT}" -n "${TARGET_NAMESPACE}" -o jsonpath='{.spec.replicas}')"
  ready="$(kctl get deployment "${TARGET_DEPLOYMENT}" -n "${TARGET_NAMESPACE}" -o jsonpath='{.status.readyReplicas}')"
  updated="$(kctl get deployment "${TARGET_DEPLOYMENT}" -n "${TARGET_NAMESPACE}" -o jsonpath='{.status.updatedReplicas}')"
  available="$(kctl get deployment "${TARGET_DEPLOYMENT}" -n "${TARGET_NAMESPACE}" -o jsonpath='{.status.availableReplicas}')"

  [[ "${ready:-0}" == "${replicas}" ]] || return 1
  [[ "${updated:-0}" == "${replicas}" ]] || return 1
  [[ "${available:-0}" == "${replicas}" ]] || return 1
}

backend_ready_rows() {
  local app_label="$1"
  kctl get pods \
    -n "${TARGET_NAMESPACE}" \
    -l "app=${app_label}" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{.spec.nodeName}{"|"}{.status.phase}{"|"}{.metadata.deletionTimestamp}{"|"}{range .status.containerStatuses[*]}{.ready}{" "}{end}{"\n"}{end}'
}

remote_backend_node_name() {
  local node_name
  while IFS= read -r node_name; do
    [[ -z "${node_name}" ]] && continue
    if [[ "${node_name}" != "${TARGET_NODE_NAME}" ]]; then
      printf '%s\n' "${node_name}"
      return 0
    fi
  done < <(experiment_nodes)
  return 1
}

single_backend_expected_node() {
  local topology_mode="$1"
  case "${topology_mode}" in
    local_single)
      printf '%s\n' "${TARGET_NODE_NAME}"
      ;;
    remote_single)
      remote_backend_node_name
      ;;
    *)
      return 1
      ;;
  esac
}

assert_single_backend_on_node() {
  local expected_node="$1"
  local app_label rows count found_node
  app_label="$(deployment_app_label)"
  rows="$(backend_ready_rows "${app_label}")"
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

wait_single_backend_on_node() {
  local expected_node="$1"
  local timeout_seconds="${2:-60}"
  local start now

  start="$(date +%s)"
  while true; do
    if assert_single_backend_on_node "${expected_node}"; then
      return 0
    fi
    now="$(date +%s)"
    if (( now - start >= timeout_seconds )); then
      return 1
    fi
    sleep 2
  done
}

assert_running_backends_on_experiment_nodes() {
  local app_label="$1"
  local expected_count=0
  local running_count=0
  declare -A seen=()

  while IFS= read -r node_name; do
    [[ -z "${node_name}" ]] && continue
    seen["${node_name}"]=0
    expected_count=$((expected_count + 1))
  done < <(experiment_nodes)

  local rows
  rows="$(kctl get pods \
    -n "${TARGET_NAMESPACE}" \
    -l "app=${app_label}" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.nodeName}{"\t"}{.status.phase}{"\t"}{range .status.containerStatuses[*]}{.ready}{" "}{end}{"\n"}{end}')"

  while IFS=$'\t' read -r pod_name node_name phase ready_flags; do
    [[ -z "${pod_name}" ]] && continue
    if [[ "${phase}" == "Running" && "${ready_flags}" == *"true"* ]]; then
      [[ -v seen["${node_name}"] ]] || return 1
      seen["${node_name}"]=$((seen["${node_name}"] + 1))
      running_count=$((running_count + 1))
    fi
  done <<< "${rows}"

  [[ "${running_count}" -eq "${expected_count}" ]] || return 1

  while IFS= read -r node_name; do
    [[ -z "${node_name}" ]] && continue
    [[ "${seen["${node_name}"]}" -eq 1 ]] || return 1
  done < <(experiment_nodes)
}

assert_backend_topology_mode() {
  local app_label="$1"
  local topology_mode="$2"
  local rows running_count expected_node
  declare -A counts=()
  local node_name pod_name phase deletion_ts ready_flags

  while IFS= read -r node_name; do
    [[ -z "${node_name}" ]] && continue
    counts["${node_name}"]=0
  done < <(experiment_nodes)

  rows="$(backend_ready_rows "${app_label}")"
  running_count=0
  while IFS='|' read -r pod_name node_name phase deletion_ts ready_flags; do
    [[ -z "${pod_name}" ]] && continue
    [[ -n "${deletion_ts}" ]] && continue
    if [[ "${phase}" == "Running" && "${ready_flags}" == *"true"* ]]; then
      [[ -v counts["${node_name}"] ]] || return 1
      counts["${node_name}"]=$((counts["${node_name}"] + 1))
      running_count=$((running_count + 1))
    fi
  done <<< "${rows}"

  case "${topology_mode}" in
    balanced)
      local expected_count=0
      while IFS= read -r node_name; do
        [[ -z "${node_name}" ]] && continue
        expected_count=$((expected_count + 1))
        [[ "${counts["${node_name}"]}" -eq 1 ]] || return 1
      done < <(experiment_nodes)
      [[ "${running_count}" -eq "${expected_count}" ]] || return 1
      ;;
    local_single|remote_single)
      expected_node="$(single_backend_expected_node "${topology_mode}")" || return 1
      [[ "${running_count}" -eq 1 ]] || return 1
      while IFS= read -r node_name; do
        [[ -z "${node_name}" ]] && continue
        if [[ "${node_name}" == "${expected_node}" ]]; then
          [[ "${counts["${node_name}"]}" -eq 1 ]] || return 1
        else
          [[ "${counts["${node_name}"]}" -eq 0 ]] || return 1
        fi
      done < <(experiment_nodes)
      ;;
    *)
      return 1
      ;;
  esac
}

assert_current_revision_ready_count() {
  local app_label="$1"
  local expected_count="$2"
  local revision_hash
  revision_hash="$(current_revision_hash "${app_label}")"
  [[ -n "${revision_hash}" ]] || return 1

  local ready_count=0

  local rows
  rows="$(kctl get pods \
    -n "${TARGET_NAMESPACE}" \
    -l "app=${app_label},pod-template-hash=${revision_hash}" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.phase}{"\t"}{range .status.containerStatuses[*]}{.ready}{" "}{end}{"\n"}{end}')"

  while IFS=$'\t' read -r pod_name phase ready_flags; do
    [[ -z "${pod_name}" ]] && continue
    [[ "${phase}" == "Running" ]] || return 1
    [[ "${ready_flags}" == *"true"* ]] || return 1
    ready_count=$((ready_count + 1))
  done <<< "${rows}"

  [[ "${ready_count}" -eq "${expected_count}" ]] || return 1
}

preflight_experiment_topology() {
  local topology_mode="${1:-balanced}"
  local app_label
  local expected_ready_replicas=0

  require_cluster_api || {
    record_failure "cluster_api_unreachable"
    return 1
  }

  assert_selected_agents_running || {
    record_failure "selected_agent_pods_not_running"
    return 1
  }

  app_label="$(deployment_app_label)"
  [[ -n "${app_label}" ]] || {
    record_failure "deployment_selector_missing"
    return 1
  }

  assert_deployment_ready || {
    record_failure "deployment_not_ready"
    return 1
  }

  assert_backend_topology_mode "${app_label}" "${topology_mode}" || {
    record_failure "backend_topology_not_stable"
    return 1
  }

  case "${topology_mode}" in
    balanced)
      while IFS= read -r node_name; do
        [[ -z "${node_name}" ]] && continue
        expected_ready_replicas=$((expected_ready_replicas + 1))
      done < <(experiment_nodes)
      ;;
    local_single|remote_single)
      expected_ready_replicas=1
      ;;
    *)
      record_failure "unknown_topology_mode:${topology_mode}"
      return 1
      ;;
  esac

  assert_current_revision_ready_count "${app_label}" "${expected_ready_replicas}" || {
    record_failure "current_revision_not_fully_ready"
    return 1
  }
}

preflight_two_node_experiment() {
  preflight_experiment_topology "balanced"
}

backend_patch_payload() {
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

restore_balanced_topology() {
  kctl apply -f "${ROOT_DIR}/manifests/nodeport-test.yaml" >/dev/null
  kctl rollout status "deployment/${TARGET_DEPLOYMENT}" -n "${TARGET_NAMESPACE}" --timeout="${ROLLOUT_TIMEOUT}" >/dev/null
  preflight_experiment_topology "balanced"
}

prepare_topology_mode() {
  local mode="$1"
  local target_node
  case "${mode}" in
    balanced)
      restore_balanced_topology
      return
      ;;
    local)
      target_node="${TARGET_NODE_NAME}"
      ;;
    remote)
      target_node="$(remote_backend_node_name)" || return 1
      ;;
    *)
      return 1
      ;;
  esac

  kctl patch deployment "${TARGET_DEPLOYMENT}" -n "${TARGET_NAMESPACE}" --type merge -p "$(backend_patch_payload "${target_node}")" >/dev/null
  kctl rollout status "deployment/${TARGET_DEPLOYMENT}" -n "${TARGET_NAMESPACE}" --timeout="${ROLLOUT_TIMEOUT}" >/dev/null
  wait_single_backend_on_node "${target_node}" 60
}

sh_quote() {
  printf "'%s'" "${1//\'/\'\"\'\"\'}"
}

join_shell_words() {
  local joined=""
  local word
  for word in "$@"; do
    joined+=$(sh_quote "${word}")
    joined+=" "
  done
  printf '%s' "${joined% }"
}

resolve_main_syncer_cmdline() {
  local node_name="$1"
  local timeout="${2:-${SYNCER_DISCOVERY_TIMEOUT_SECONDS}}"
  local pod start now
  local -a cmd=()

  start="$(date +%s)"
  while true; do
    pod="$(wait_agent_pod_for_node "${node_name}" 10)" || pod=""
    if [[ -n "${pod}" ]]; then
      mapfile -t cmd < <(kctl exec -n "${SYSTEM_NAMESPACE}" "${pod}" -- sh -c '
        for p in /proc/[0-9]*/cmdline; do
          cmd="$(tr "\000" "\n" < "$p" 2>/dev/null || true)"
          [ -n "$cmd" ] || continue
          first="$(printf "%s\n" "$cmd" | sed -n "1p")"
          case "$first" in
            */nodeport-syncer|*/nodeport-syncer-phase1)
              if ! printf "%s\n" "$cmd" | grep -qx -- "--telemetry-enable"; then
                printf "%s\n" "$cmd"
                exit 0
              fi
              ;;
          esac
        done
        exit 1
      ' 2>/dev/null || true)
      if (( ${#cmd[@]} > 0 )); then
        printf '%s\n' "${cmd[@]}"
        return 0
      fi
    fi

    now="$(date +%s)"
    if (( now - start >= timeout )); then
      return 1
    fi
    sleep 1
  done
}

build_syncer_shell_command() {
  local node_name="$1"
  local mode="$2"
  local enable_telemetry="$3"
  local clear_target_state="$4"
  local -a raw filtered
  local arg
  local skip_next=0

  mapfile -t raw < <(resolve_main_syncer_cmdline "${node_name}")
  (( ${#raw[@]} > 0 )) || return 1

  filtered=("${raw[0]}")
  for ((i = 1; i < ${#raw[@]}; i++)); do
    arg="${raw[i]}"
    if (( skip_next )); then
      skip_next=0
      continue
    fi

    case "${arg}" in
      --service|--sync-mode|--telemetry-window|--telemetry-format|--telemetry-output|--telemetry-experiment-id|--telemetry-events-file|--telemetry-service)
        skip_next=1
        continue
        ;;
      --telemetry-enable|--clear-target-state)
        continue
        ;;
    esac
    filtered+=("${arg}")
  done

  filtered+=("--sync-mode" "${mode}")
  filtered+=("--service" "${TARGET_NAMESPACE}/${TARGET_SERVICE}")
  if [[ "${enable_telemetry}" == "1" ]]; then
    filtered+=("--telemetry-enable")
    filtered+=("--telemetry-window" "1s")
    filtered+=("--telemetry-format" "csv")
    filtered+=("--telemetry-output" "${TELEMETRY_OUTPUT_ROOT}")
    filtered+=("--telemetry-experiment-id" "${EXPERIMENT_ID}")
    filtered+=("--telemetry-events-file" "${TELEMETRY_SHARED_EVENTS_FILE}")
    filtered+=("--telemetry-service" "${TARGET_NAMESPACE}/${TARGET_SERVICE}")
  fi
  if [[ "${clear_target_state}" == "1" ]]; then
    filtered+=("--clear-target-state")
  fi

  join_shell_words "${filtered[@]}"
}

remote_csv_path_for_node() {
  local node_name="$1"
  printf '%s/%s/%s.csv\n' "${TELEMETRY_OUTPUT_ROOT}" "${EXPERIMENT_ID}" "${node_name}"
}

remote_collector_runtime_dir_for_node() {
  local node_name="$1"
  printf '%s/%s/%s\n' "${TELEMETRY_RUNTIME_ROOT}" "${EXPERIMENT_ID}" "${node_name}"
}

remote_collector_pid_path_for_node() {
  local node_name="$1"
  printf '%s/collector.pid\n' "$(remote_collector_runtime_dir_for_node "${node_name}")"
}

remote_collector_log_path_for_node() {
  local node_name="$1"
  printf '%s/collector.log\n' "$(remote_collector_runtime_dir_for_node "${node_name}")"
}

local_csv_path_for_node() {
  local node_name="$1"
  printf '%s/%s.csv\n' "${TELEMETRY_DIR}" "${node_name}"
}

local_agent_log_path_for_node() {
  local node_name="$1"
  printf '%s/%s.log\n' "${AGENT_LOG_DIR}" "${node_name}"
}

start_collector_for_node() {
  local node_name="$1"
  local pod command_string runtime_dir pid_path log_path remote_csv_path remote_events_dir remote_csv_dir

  pod="$(wait_agent_pod_for_node "${node_name}")" || return 1
  stop_collector_for_node "${node_name}" || true
  command_string="$(build_syncer_shell_command "${node_name}" "watch" "1" "0")" || return 1
  runtime_dir="$(remote_collector_runtime_dir_for_node "${node_name}")"
  pid_path="$(remote_collector_pid_path_for_node "${node_name}")"
  log_path="$(remote_collector_log_path_for_node "${node_name}")"
  remote_csv_path="$(remote_csv_path_for_node "${node_name}")"
  remote_events_dir="$(dirname "${TELEMETRY_SHARED_EVENTS_FILE}")"
  remote_csv_dir="$(dirname "${remote_csv_path}")"

  kctl exec -n "${SYSTEM_NAMESPACE}" "${pod}" -- sh -c "
    set -eu
    mkdir -p $(sh_quote "${runtime_dir}") $(sh_quote "${remote_events_dir}") $(sh_quote "${remote_csv_dir}")
    rm -f $(sh_quote "${pid_path}")
    nohup ${command_string} > $(sh_quote "${log_path}") 2>&1 < /dev/null &
    echo \$! > $(sh_quote "${pid_path}")
  "
  append_note "collector_started_${node_name}=1"
  append_note "collector_log_${node_name}=${log_path}"
}

stop_collector_for_node() {
  local node_name="$1"
  local pod pid_path

  pod="$(agent_pod_for_node "${node_name}" || true)"
  [[ -n "${pod}" ]] || return 0

  pid_path="$(remote_collector_pid_path_for_node "${node_name}")"
  kctl_timeout 15s exec -n "${SYSTEM_NAMESPACE}" "${pod}" -- sh -c "
    pid=
    if [ -f $(sh_quote "${pid_path}") ]; then
      pid=\$(cat $(sh_quote "${pid_path}") 2>/dev/null || true)
    fi
    if [ -n \"\${pid}\" ] && kill -0 \"\${pid}\" 2>/dev/null; then
      kill \"\${pid}\" 2>/dev/null || true
      sleep 1
      if kill -0 \"\${pid}\" 2>/dev/null; then
        kill -9 \"\${pid}\" 2>/dev/null || true
      fi
    fi
    rm -f $(sh_quote "${pid_path}")
  " >/dev/null 2>&1 || true
}

restart_collector_for_node() {
  local node_name="$1"
  stop_collector_for_node "${node_name}" || true
  start_collector_for_node "${node_name}"
}

start_collectors() {
  while IFS= read -r node_name; do
    [[ -z "${node_name}" ]] && continue
    start_collector_for_node "${node_name}" || return 1
  done < <(experiment_nodes)
}

stop_collectors() {
  while IFS= read -r node_name; do
    [[ -z "${node_name}" ]] && continue
    stop_collector_for_node "${node_name}" || true
  done < <(experiment_nodes)
}

collect_agent_logs() {
  while IFS= read -r node_name; do
    [[ -z "${node_name}" ]] && continue
    local pod local_path
    pod="$(wait_agent_pod_for_node "${node_name}" 30)" || return 1
    local_path="$(local_agent_log_path_for_node "${node_name}")"
    kctl logs -n "${SYSTEM_NAMESPACE}" "${pod}" > "${local_path}"
    append_note "agent_pod_${node_name}=${pod}"
  done < <(experiment_nodes)
}

collect_telemetry_csvs() {
  while IFS= read -r node_name; do
    [[ -z "${node_name}" ]] && continue
    local pod remote_path local_path
    pod="$(wait_agent_pod_for_node "${node_name}" 30)" || return 1
    remote_path="$(remote_csv_path_for_node "${node_name}")"
    local_path="$(local_csv_path_for_node "${node_name}")"
    kctl exec -n "${SYSTEM_NAMESPACE}" "${pod}" -- test -f "${remote_path}" || return 1
    kctl exec -n "${SYSTEM_NAMESPACE}" "${pod}" -- cat "${remote_path}" > "${local_path}"
  done < <(experiment_nodes)
}

finalize_experiment_artifacts() {
  stop_collectors
  sleep 2
  collect_telemetry_csvs || return 1
  collect_agent_logs || return 1
}

apply_netem_on_node() {
  local node_name="$1"
  local iface="$2"
  local delay_ms="$3"
  local loss_percent="$4"
  local pod

  pod="$(wait_agent_pod_for_node "${node_name}")" || return 1
  kctl exec -n "${SYSTEM_NAMESPACE}" "${pod}" -- sh -c "
    nsenter -t 1 -m -u -i -n -p -- tc qdisc replace dev $(sh_quote "${iface}") root netem delay ${delay_ms}ms loss ${loss_percent}%
  "
  NETEM_ACTIVE+=("${node_name}:${iface}")
}

clear_netem_on_node() {
  local node_name="$1"
  local iface="$2"
  local pod

  pod="$(agent_pod_for_node "${node_name}" || true)"
  [[ -n "${pod}" ]] || return 0
  kctl exec -n "${SYSTEM_NAMESPACE}" "${pod}" -- sh -c "
    nsenter -t 1 -m -u -i -n -p -- tc qdisc del dev $(sh_quote "${iface}") root 2>/dev/null || true
  " >/dev/null 2>&1 || true
}

cleanup_experiment() {
  local target
  stop_background_load
  stop_pressure_traffic
  stop_traffic
  for target in "${NETEM_ACTIVE[@]:-}"; do
    [[ -n "${target}" ]] || continue
    clear_netem_on_node "${target%%:*}" "${target#*:}"
  done
  NETEM_ACTIVE=()
  stop_collectors
}

scale_target_deployment() {
  local replicas="$1"
  kctl scale deployment "${TARGET_DEPLOYMENT}" -n "${TARGET_NAMESPACE}" --replicas="${replicas}" >/dev/null
  kctl rollout status "deployment/${TARGET_DEPLOYMENT}" -n "${TARGET_NAMESPACE}" --timeout="${ROLLOUT_TIMEOUT}" >/dev/null
}

wait_service_absent() {
  local timeout="${1:-30}"
  local start now
  start="$(date +%s)"
  while kctl get svc "${TARGET_SERVICE}" -n "${TARGET_NAMESPACE}" >/dev/null 2>&1; do
    now="$(date +%s)"
    if (( now - start >= timeout )); then
      return 1
    fi
    sleep 1
  done
}

run_targeted_oneshot_clear() {
  local node_name="$1"
  local pod command_string log_path runtime_dir

  pod="$(wait_agent_pod_for_node "${node_name}")" || return 1
  command_string="$(build_syncer_shell_command "${node_name}" "oneshot" "0" "1")" || return 1
  runtime_dir="$(remote_collector_runtime_dir_for_node "${node_name}")"
  log_path="${runtime_dir}/oneshot-clear.log"
  kctl exec -n "${SYSTEM_NAMESPACE}" "${pod}" -- sh -c "
    set -eu
    mkdir -p $(sh_quote "${runtime_dir}")
    ${command_string} > $(sh_quote "${log_path}") 2>&1
  "
  append_note "oneshot_clear_log_${node_name}=${log_path}"
}

csv_field_index() {
  local file="$1"
  local field_name="$2"

  awk -F, -v target="${field_name}" '
    NR == 1 {
      for (i = 1; i <= NF; i++) {
        if ($i == target) {
          print i
          exit
        }
      }
    }
  ' "${file}"
}

assert_csv_has_data_rows() {
  local file="$1"
  [[ -f "${file}" ]] || return 1
  [[ "$(wc -l < "${file}")" -gt 1 ]] || return 1
}

assert_csv_all_label() {
  local file="$1"
  local expected_label="$2"
  local label_idx
  label_idx="$(csv_field_index "${file}" "label")"
  [[ -n "${label_idx}" ]] || return 1

  awk -F, -v idx="${label_idx}" -v expected="${expected_label}" '
    NR == 1 { next }
    $idx != expected { exit 1 }
  ' "${file}"
}

assert_csv_has_label() {
  local file="$1"
  local expected_label="$2"
  local label_idx
  label_idx="$(csv_field_index "${file}" "label")"
  [[ -n "${label_idx}" ]] || return 1

  awk -F, -v idx="${label_idx}" -v expected="${expected_label}" '
    NR == 1 { next }
    $idx == expected { found = 1 }
    END { exit(found ? 0 : 1) }
  ' "${file}"
}

assert_csv_has_recovery_active() {
  local file="$1"
  local recovery_idx
  recovery_idx="$(csv_field_index "${file}" "recovery_active")"
  [[ -n "${recovery_idx}" ]] || return 1

  awk -F, -v idx="${recovery_idx}" '
    NR == 1 { next }
    $idx == 1 { found = 1 }
    END { exit(found ? 0 : 1) }
  ' "${file}"
}

assert_all_experiment_csvs_have_label() {
  local expected_label="$1"
  while IFS= read -r node_name; do
    [[ -z "${node_name}" ]] && continue
    assert_csv_has_label "$(local_csv_path_for_node "${node_name}")" "${expected_label}" || return 1
  done < <(experiment_nodes)
}

assert_all_experiment_csvs_have_recovery() {
  while IFS= read -r node_name; do
    [[ -z "${node_name}" ]] && continue
    assert_csv_has_recovery_active "$(local_csv_path_for_node "${node_name}")" || return 1
  done < <(experiment_nodes)
}

assert_all_experiment_csvs_have_rows() {
  while IFS= read -r node_name; do
    [[ -z "${node_name}" ]] && continue
    assert_csv_has_data_rows "$(local_csv_path_for_node "${node_name}")" || return 1
  done < <(experiment_nodes)
}

assert_traffic_has_success_between() {
  local start_ms="$1"
  local end_ms="$2"

  awk -F, -v start="${start_ms}" -v end="${end_ms}" '
    NR == 1 { next }
    ($1 >= start && $1 <= end && $2 == 1) { found = 1 }
    END { exit(found ? 0 : 1) }
  ' "${TRAFFIC_LOG_FILE}"
}

assert_csv_metric_peak_during_window_exceeds_before_window() {
  local file="$1"
  local metric_name="$2"
  local start_ms="$3"
  local end_ms="$4"
  local metric_idx
  metric_idx="$(csv_field_index "${file}" "${metric_name}")"
  [[ -n "${metric_idx}" ]] || return 1

  awk -F, -v metric_idx="${metric_idx}" -v start_ms="${start_ms}" -v end_ms="${end_ms}" '
    NR == 1 { next }
    {
      window_start = $1 + 0
      window_end = $2 + 0
      value = $metric_idx + 0
      if (window_end <= start_ms && value > pre_peak) {
        pre_peak = value
      }
      if (window_start < end_ms && window_end > start_ms && value > event_peak) {
        event_peak = value
      }
    }
    END { exit(event_peak > pre_peak ? 0 : 1) }
  ' "${file}"
}
