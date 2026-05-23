#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
GO_BIN="${GO_BIN:-go}"
OUTPUT_DIR="${OUTPUT_DIR:-${ROOT_DIR}/bin}"

mkdir -p "$OUTPUT_DIR"

cd "$ROOT_DIR"
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 "$GO_BIN" build -o "${OUTPUT_DIR}/nodeport-syncer" ./cmd/nodeport-syncer
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 "$GO_BIN" build -o "${OUTPUT_DIR}/nodeport-agent" ./cmd/nodeport-agent

echo "built: ${OUTPUT_DIR}/nodeport-syncer"
echo "built: ${OUTPUT_DIR}/nodeport-agent"
