#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
GO_BIN="${GO_BIN:-/usr/local/go/bin/go}"
IMAGE_TAG="${IMAGE_TAG:-docker.io/library/ebpf-nodeport-agent:phase2-local}"
SOURCE_IMAGE_TAG="${SOURCE_IMAGE_TAG:-docker.io/library/ebpf-nodeport-agent:snat-v1}"
SYSTEM_NAMESPACE="${SYSTEM_NAMESPACE:-ebpf-nodeport-system}"
SYSTEM_LABEL="${SYSTEM_LABEL:-app=ebpf-nodeport-agent}"
EXPERIMENT_NODE_NAMES="${EXPERIMENT_NODE_NAMES:-k8s-worker1,k8s-worker2}"
NODEPORT_BIN_HOST_PATH="${NODEPORT_BIN_HOST_PATH:-/opt/ebpf-nodeport/bin}"

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

experiment_nodes() {
  tr ',' '\n' <<< "${EXPERIMENT_NODE_NAMES}" | awk 'NF'
}

agent_pod_for_node() {
  local node_name="$1"
  kctl get pods \
    -n "${SYSTEM_NAMESPACE}" \
    -l "${SYSTEM_LABEL}" \
    --field-selector="spec.nodeName=${node_name},status.phase=Running" \
    -o jsonpath='{.items[0].metadata.name}'
}

load_image_to_node() {
  local node_name="$1"
  local pod

  pod="$(agent_pod_for_node "${node_name}")"
  [[ -n "${pod}" ]] || {
    echo "unable to find running agent pod on ${node_name}" >&2
    exit 1
  }

  echo "installing phase2 binaries on ${node_name} via ${pod}"
  kctl exec -n "${SYSTEM_NAMESPACE}" "${pod}" -- sh -c "
    nsenter -t 1 -m -u -i -n -p -- mkdir -p ${NODEPORT_BIN_HOST_PATH}
  "
  kctl exec -i -n "${SYSTEM_NAMESPACE}" "${pod}" -- sh -c "
    nsenter -t 1 -m -u -i -n -p -- sh -c 'cat > ${NODEPORT_BIN_HOST_PATH}/nodeport-agent && chmod +x ${NODEPORT_BIN_HOST_PATH}/nodeport-agent'
  " < "${ROOT_DIR}/bin/nodeport-agent"
  kctl exec -i -n "${SYSTEM_NAMESPACE}" "${pod}" -- sh -c "
    nsenter -t 1 -m -u -i -n -p -- sh -c 'cat > ${NODEPORT_BIN_HOST_PATH}/nodeport-syncer && chmod +x ${NODEPORT_BIN_HOST_PATH}/nodeport-syncer'
  " < "${ROOT_DIR}/bin/nodeport-syncer"

  echo "tagging ${SOURCE_IMAGE_TAG} as ${IMAGE_TAG} on ${node_name}"
  kctl exec -n "${SYSTEM_NAMESPACE}" "${pod}" -- sh -c "
    nsenter -t 1 -m -u -i -n -p -- sh -c '
      if ! ctr -n k8s.io images ls -q | grep -qx ${IMAGE_TAG}; then
        ctr -n k8s.io images tag ${SOURCE_IMAGE_TAG} ${IMAGE_TAG}
      fi
    '
  "
}

require_cmd kubectl
require_cmd "${GO_BIN}"

GO_BIN="${GO_BIN}" bash "${SCRIPT_DIR}/build_nodeport_syncer.sh"

while IFS= read -r node_name; do
  [[ -z "${node_name}" ]] && continue
  load_image_to_node "${node_name}"
done < <(experiment_nodes)

kctl apply -f "${ROOT_DIR}/manifests/nodeport-node-agent.yaml"
kctl rollout status ds/ebpf-nodeport-agent -n "${SYSTEM_NAMESPACE}" --timeout=180s
kctl get pods -n "${SYSTEM_NAMESPACE}" -o wide
