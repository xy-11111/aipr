# Phase 5 Test Record Round 02

## Summary

本轮目标是只重跑 `Phase 5` 的 `local backend` 两个场景，校正 `Round 01` 中把 local-only NodePort 路径误判为“不支持”的结论。

结果：

- `phase5-off-local` 与 `phase5-on-local` 均成功重跑。
- `worker1 ingress + worker1 only backend` 的 local-only NodePort 路径在当前实现下**可以工作**。
- `NODEPORT_ATTACH_VETHS=1` 不是必要条件；在恢复为 `NODEPORT_ATTACH_VETHS=0` 后，local-only 路径仍能稳定提供服务。
- `paper_assets/phase5_v1/` 中的 local 场景数据与总表已完成刷新。

## Test Time

- Date: `2026-05-24`
- Workspace: `/home/ubuntu/wjy/ebpf_nodeport`

## Code/Script Changes

本轮为 `Phase 5` 自动化链路补了两个收敛性改动：

1. `experiments/phase5_common.sh`
   - 新增 `phase5_wait_for_nodeport_ready`
   - 在正式 benchmark 前要求 NodePort 连续成功探活若干次
2. `experiments/phase5_run_matrix.sh`
   - 新增 `PHASE5_SCENARIOS`
   - 支持只重跑指定场景，本轮使用 `off-local,on-local`

## Verification Steps

### A. 先验证 local-only 路径确实可工作

1. 将 agent ConfigMap 里的 `NODEPORT_ATTACH_VETHS` 改成 `1`
2. rollout restart `ebpf-nodeport-agent`
3. 切换到 `--mode local`
4. 连续访问 `http://192.168.1.202:30080/`

结果：`10/10` 成功。

### B. 再反证 `attach_veths` 不是必要条件

1. 将 `NODEPORT_ATTACH_VETHS` 改回 `0`
2. 再次 rollout restart `ebpf-nodeport-agent`
3. 保持 local-only 拓扑
4. 连续访问 `http://192.168.1.202:30080/`

结果：再次连续成功。

### C. 简化 benchmark 复核

在 `NODEPORT_ATTACH_VETHS=0` 且 local-only 拓扑下，单独跑一轮简化压测：

```bash
./.venv-phase4/bin/python scripts/phase5_http_bench.py \
  --url http://192.168.1.202:30080/ \
  --warmup-seconds 3 \
  --duration-seconds 20 \
  --concurrency 8 \
  --output-dir tmp_phase5_local_probe
```

结果：

- success rate: `1.0`
- throughput: `1040.25 rps`
- p50: `7.203 ms`
- p99: `16.650 ms`

这说明 `Round 01` 的 local failure 更像是**拓扑切换后立即开测导致的收敛时机问题**，而不是 datapath 天生不支持 local-only。

## Re-run Command

```bash
PHASE5_SCENARIOS='off-local,on-local' bash experiments/phase5_run_matrix.sh
```

脚本完成后自动重新导出：

- `paper_assets/phase5_v1/tables/table_phase5_overhead_matrix.csv`
- `paper_assets/phase5_v1/tables/table_phase5_overhead_delta.csv`
- `paper_assets/phase5_v1/figures/fig_phase5_throughput_latency.png`
- `paper_assets/phase5_v1/figures/fig_phase5_cpu_overhead.png`

## Local Scenario Metrics (Corrected)

| Scenario | Throughput (rps) | p50 (ms) | p99 (ms) | Success rate | worker1 node CPU | worker2 node CPU | worker1 agent CPU | worker2 agent CPU |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `phase5-off-local` | 694.833 | 13.976 | 34.457 | 0.954186 | 35.855 | 19.672 | 297.632 | 159.289 |
| `phase5-on-local` | 870.983 | 9.069 | 25.000 | 0.947339 | 36.935 | 18.494 | 303.148 | 150.633 |

## Corrected Local Delta

- throughput delta: `+176.150 rps` (`+25.35%`)
- p50 delta: `-4.907 ms` (`-35.11%`)
- p99 delta: `-9.457 ms` (`-27.45%`)
- worker1 agent CPU delta: `+5.516`
- worker2 agent CPU delta: `-8.656`
- worker1 node CPU delta: `+1.080`
- worker2 node CPU delta: `-1.178`

## Interpretation

本轮的主要结论不是“telemetry 打开后 local 竟然更快”，而是：

1. `Round 01` 的 local 失败样本不能再被解释为“当前系统不支持 local-only NodePort”。
2. local-only 场景对实验时机和收敛状态更敏感，至少需要在 benchmark 前增加显式探活门槛。
3. `paper_assets/phase5_v1` 现在的 local 数据已经比 `Round 01` 更可信，但是否将 local delta 直接写成论文中的正式 overhead 结论，仍建议保留谨慎表述。

## Current Cluster State

本轮结束后，脚本已自动恢复 balanced topology：

- `worker1`: `1` backend
- `worker2`: `1` backend

## Exit Criteria

- local-only path re-investigated: `passed`
- local pair re-run completed: `passed`
- phase5 paper assets refreshed: `passed`
- “local-only path unsupported” hypothesis: `rejected`
