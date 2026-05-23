# Phase 3 Dataset v1 摘要

- 生成时间：2026-05-23 11:57 UTC
- 样本总行数：`1450`
- 实验总数：`20`
- 节点总数：`2`

## 1. Split 分布

- `train`：`541` 行
- `val`：`400` 行
- `test`：`509` 行

## 2. 标签分布

- `backend_churn`：`19` 行（train=6, val=7, test=6）
- `conntrack_pressure`：`107` 行（train=36, val=29, test=42）
- `control_plane_recovery`：`22` 行（train=7, val=5, test=10）
- `load_surge`：`108` 行（train=36, val=30, test=42）
- `normal`：`1057` 行（train=411, val=289, test=357）
- `path_degradation`：`107` 行（train=35, val=30, test=42）
- `service_reconcile`：`30` 行（train=10, val=10, test=10）

## 3. 场景分布

- `agent_restart`：`69` 行
- `backend_rollout_restart`：`364` 行
- `conntrack_pressure`：`175` 行
- `endpointslice_churn`：`177` 行
- `map_rebuild`：`31` 行
- `normal_steady_state`：`156` 行
- `path_degradation_netem`：`183` 行
- `service_delete_recreate`：`117` 行
- `traffic_burst`：`178` 行

## 4. 节点分布

- `k8s-worker1`：`742` 行
- `k8s-worker2`：`708` 行

## 5. 实验级 split 映射

- `agent-restart-20260523T083729Z` -> `train` / `control_plane_recovery` / `agent_restart`
- `agent-restart-20260523T113700Z` -> `val` / `control_plane_recovery` / `agent_restart`
- `backend-rollout-restart-20260523T072229Z` -> `train` / `backend_churn` / `backend_rollout_restart`
- `backend-rollout-restart-20260523T083411Z` -> `val` / `backend_churn` / `backend_rollout_restart`
- `conntrack-pressure-20260523T084229Z` -> `train` / `conntrack_pressure` / `conntrack_pressure`
- `conntrack-pressure-20260523T113900Z` -> `val` / `conntrack_pressure` / `conntrack_pressure`
- `conntrack-pressure-20260523T114000Z` -> `test` / `conntrack_pressure` / `conntrack_pressure`
- `endpointslice-churn-20260523T084408Z` -> `test` / `backend_churn` / `endpointslice_churn`
- `map-rebuild-20260523T083956Z` -> `test` / `control_plane_recovery` / `map_rebuild`
- `normal-steady-state-20260523T072104Z` -> `train` / `normal` / `normal_steady_state`
- `normal-steady-state-20260523T083153Z` -> `test` / `normal` / `normal_steady_state`
- `path-degradation-netem-20260523T084038Z` -> `train` / `path_degradation` / `path_degradation_netem`
- `path-degradation-netem-20260523T114300Z` -> `val` / `path_degradation` / `path_degradation_netem`
- `path-degradation-netem-20260523T114400Z` -> `test` / `path_degradation` / `path_degradation_netem`
- `service-delete-recreate-20260523T084325Z` -> `train` / `service_reconcile` / `service_delete_recreate`
- `service-delete-recreate-20260523T114500Z` -> `val` / `service_reconcile` / `service_delete_recreate`
- `service-delete-recreate-20260523T114600Z` -> `test` / `service_reconcile` / `service_delete_recreate`
- `traffic-burst-20260523T084558Z` -> `train` / `load_surge` / `traffic_burst`
- `traffic-burst-20260523T114100Z` -> `val` / `load_surge` / `traffic_burst`
- `traffic-burst-20260523T114200Z` -> `test` / `load_surge` / `traffic_burst`

