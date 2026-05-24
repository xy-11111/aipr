# Phase 5 Paper Assets v1

- Purpose: capture the minimum 2x2 overhead matrix for telemetry off/on and local/remote backends
- Raw directories live under `raw/phase5-*`
- Tables and figures are exported from the raw JSON/CSV summaries
- Status: `completed`
- Interpretation note:
  - `Round 01` once misread the `local backend` path as unsupported.
  - That conclusion was corrected in `aipr_phase5_test_record_round02.md`.
  - Current interpretation is: under `externalTrafficPolicy=Cluster`, the `worker1 ingress + worker1 only backend` path can work, but local-only experiments are more sensitive to topology-convergence timing than remote ones.

## Metrics

- Throughput is reported in requests per second
- Latency values are p50/p99 in milliseconds
- Node CPU is host-level average CPU usage percentage during the measurement window
- Agent CPU is pod-level average CPU usage percentage of one core during the measurement window

## Fallback note

- `kubectl top` was unavailable in this environment, so CPU metrics were derived from `/proc/stat` and pod cgroup `cpu.stat`
- The exported matrix keeps `success_rate` explicitly so low-availability scenarios are visible instead of being misread as pure performance overhead
