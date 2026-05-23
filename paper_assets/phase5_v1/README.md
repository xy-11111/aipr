# Phase 5 Paper Assets v1

- Purpose: capture the minimum 2x2 overhead matrix for telemetry off/on and local/remote backends
- Raw directories live under `raw/phase5-*`
- Tables and figures are exported from the raw JSON/CSV summaries

## Metrics

- Throughput is reported in requests per second
- Latency values are p50/p99 in milliseconds
- Node CPU is host-level average CPU usage percentage during the measurement window
- Agent CPU is pod-level average CPU usage percentage of one core during the measurement window

## Fallback note

- `kubectl top` was unavailable in this environment, so CPU metrics were derived from `/proc/stat` and pod cgroup `cpu.stat`
- The exported matrix keeps `success_rate` explicitly so low-availability scenarios are visible instead of being misread as pure performance overhead
