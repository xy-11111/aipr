# Phase 5 Test Record Round 01

## Summary

本轮完成了两项工作：

1. 将 `Phase 4` baseline 结果冻结为论文表图资产。
2. 交付并运行 `Phase 5` 的最小 `2x2` 开销评估矩阵：
   - `telemetry off/on`
   - `local backend / remote backend`

`Phase 5` 的自动化链路、原始数据、表格和图片已经全部生成；但实验结果同时暴露出一个重要系统现象：**当前实现下，`worker1 ingress + worker1 only backend` 的 local-only NodePort 路径不可达**。因此，remote 场景的数据可以直接作为部署开销证据，local 场景的数据则更适合作为已发现的功能性限制记录，而不是纯 overhead 结论。

## Test Time

- Date: `2026-05-23`
- Workspace: `/home/ubuntu/wjy/ebpf_nodeport`

## Implemented Files

- `scripts/phase4_export_paper_assets.py`
- `scripts/phase5_http_bench.py`
- `scripts/phase5_export_paper_assets.py`
- `experiments/phase5_common.sh`
- `experiments/phase5_prepare_topology.sh`
- `experiments/phase5_run_matrix.sh`

## Key Fixes During Execution

在首轮执行 `Phase 5` 时发现并修正了 3 个实现问题：

1. `phase5_prepare_topology_mode` 在判断“单 backend 已收敛”时，把 `Terminating` 旧 pod 也算进去了。
   - 修复：在 `experiments/phase5_common.sh` 中忽略 `deletionTimestamp` 非空的 pod。
2. `phase5_assert_single_backend_on_node` 使用 `tab` 作为分隔符，空字段会被 Bash 折叠，导致空的 `deletionTimestamp` 读错位。
   - 修复：改为 `|` 分隔。
3. `phase5_run_matrix.sh` 在单场景结束后清掉了全局 `EXIT trap`，导致失败时不一定能恢复 balanced topology。
   - 修复：保留全局 trap，只做场景级 `cleanup_experiment`。

此外，`scripts/phase5_export_paper_assets.py` 也修正了 raw 目录命名与场景顺序不一致的问题。

## Commands

### Phase 4 Assets Export

```bash
./.venv-phase4/bin/python scripts/phase4_export_paper_assets.py
```

### Phase 5 Matrix

```bash
bash experiments/phase5_run_matrix.sh
./.venv-phase4/bin/python scripts/phase5_export_paper_assets.py
```

### Local-path Verification

```bash
bash experiments/phase5_prepare_topology.sh --mode local
curl http://10.244.1.249:8080/
curl http://192.168.1.202:30080/
bash -lc 'source experiments/phase5_common.sh; phase5_restore_balanced_topology'
```

## Output Locations

### Phase 4

- `paper_assets/phase4_v1/README.md`
- `paper_assets/phase4_v1/tables/`
- `paper_assets/phase4_v1/figures/`
- `paper_assets/phase4_v1/source/`

### Phase 5

- `paper_assets/phase5_v1/README.md`
- `paper_assets/phase5_v1/raw/phase5-off-local/`
- `paper_assets/phase5_v1/raw/phase5-on-local/`
- `paper_assets/phase5_v1/raw/phase5-off-remote/`
- `paper_assets/phase5_v1/raw/phase5-on-remote/`
- `paper_assets/phase5_v1/tables/table_phase5_overhead_matrix.csv`
- `paper_assets/phase5_v1/tables/table_phase5_overhead_delta.csv`
- `paper_assets/phase5_v1/figures/fig_phase5_throughput_latency.png`
- `paper_assets/phase5_v1/figures/fig_phase5_cpu_overhead.png`

## Phase 5 Scenario Metrics

| Scenario | Throughput (rps) | p50 (ms) | p99 (ms) | Success rate | worker1 node CPU | worker2 node CPU | worker1 agent CPU | worker2 agent CPU |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `phase5-off-local` | 8.000 | 0.000 | 0.000 | 0.000000 | 19.932 | 19.329 | 159.028 | 154.772 |
| `phase5-on-local` | 9.550 | 2.910 | 1027.928 | 0.162304 | 21.316 | 19.163 | 170.349 | 154.321 |
| `phase5-off-remote` | 814.817 | 7.034 | 26.211 | 0.994232 | 20.374 | 32.866 | 162.088 | 262.798 |
| `phase5-on-remote` | 764.183 | 6.492 | 23.756 | 0.993195 | 22.126 | 32.895 | 176.561 | 262.554 |

## Remote-path Overhead Readout

对于 `remote backend`，当前数据已经可以直接支撑论文中的部署开销讨论：

- Throughput delta: `-50.633 rps` (`-6.21%`)
- p50 delta: `-0.542 ms` (`-7.71%`)
- p99 delta: `-2.455 ms` (`-9.37%`)
- worker1 agent CPU delta: `+14.473`
- worker2 agent CPU delta: `-0.244`
- worker1 node CPU delta: `+1.752`
- worker2 node CPU delta: `+0.029`

## Local-path Functional Finding

`local backend` 场景没有得到可直接解释为“开销”的结果，而是暴露出当前 datapath 的功能性限制：

- `phase5-off-local`：`success_rate = 0.0`
- `phase5-on-local`：`success_rate = 0.162304`

进一步的手工验证表明：

- 直接访问本地 backend pod IP `10.244.1.249:8080` 成功。
- 访问同一服务的 NodePort `192.168.1.202:30080` 超时。

这说明：

- `echo` workload 本身健康；
- 问题发生在 **NodePort local-only path**，不是 benchmark 工具或 backend 容器本身。

## Final Cluster State

实验结束后已恢复到 balanced topology：

- `worker1`: `1` backend
- `worker2`: `1` backend

## Exit Criteria

- Phase 4 paper assets exported: `passed`
- Phase 5 automation implemented: `passed`
- Phase 5 raw data captured for all 4 scenarios: `passed`
- Phase 5 tables/figures exported: `passed`
- Phase 5 remote overhead comparison usable for paper: `passed`
- Phase 5 local overhead comparison usable for paper: `blocked by local-only NodePort reachability issue`
