# Phase 3 Round 02 刷新记录

## 1. 记录概览

- 测试时间：2026-05-23 11:55 UTC - 11:57 UTC
- 记录目的：
  - 将 `Phase 2 Round 04` 中通过验收的 9 个补采实验纳入 `Dataset v1`
  - 直接覆盖刷新 [datasets/phase3_v1](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1)
  - 验证补采后 `train/val/test` 覆盖是否满足预期
- 结论：**Round 02 通过，`phase3_v1` 已刷新完成**

## 2. 本轮新增变更

### 2.1 数据集纳入清单改为 checked-in 文件

已将 `Dataset v1` 的纳入来源固定为：

- [experiment_selection.json](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/experiment_selection.json:1)

对应实现位于：

- [phase3_build_dataset.py](/home/ubuntu/wjy/ebpf_nodeport/scripts/phase3_build_dataset.py:1)

本轮把以下 9 个补采实验加入 `included_experiments`：

- `agent-restart-20260523T113700Z`
- `conntrack-pressure-20260523T113900Z`
- `conntrack-pressure-20260523T114000Z`
- `traffic-burst-20260523T114100Z`
- `traffic-burst-20260523T114200Z`
- `path-degradation-netem-20260523T114300Z`
- `path-degradation-netem-20260523T114400Z`
- `service-delete-recreate-20260523T114500Z`
- `service-delete-recreate-20260523T114600Z`

### 2.2 刷新过程中修复的一处报告脚本问题

在刷新 `dataset_summary.md` / `quality_report.md` 的过程中，发现 [phase3_dataset_report.py](/home/ubuntu/wjy/ebpf_nodeport/scripts/phase3_dataset_report.py:1) 对场景计数排序的实现不够稳健。

本轮已修复为：

- `scenario_counter` 显式使用 `(row.get("experiment_type") or "")`
- 排序统一转成 `str(...)`
- 空场景名统一显示为 `unknown`

修复后重新运行报告生成，结果稳定。

## 3. 执行命令

本轮执行顺序如下：

```bash
python3 scripts/phase3_build_dataset.py
python3 scripts/phase3_split_dataset.py
python3 scripts/phase3_dataset_report.py
```

另外对本轮涉及的脚本做了基础检查：

```bash
bash -n experiments/service_delete_recreate.sh
python3 -m py_compile scripts/phase3_build_dataset.py scripts/phase3_dataset_report.py
```

结果：

- 构建脚本通过
- split 脚本通过
- 报告脚本通过
- 刷新后未发现阻塞性错误

## 4. 刷新后的 `Dataset v1` 结果

输出目录：

- [datasets/phase3_v1](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1)

核心文件包括：

- [raw_index.csv](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/raw_index.csv)
- [windows_all.csv](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/windows_all.csv)
- [windows_train.csv](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/windows_train.csv)
- [windows_val.csv](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/windows_val.csv)
- [windows_test.csv](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/windows_test.csv)
- [splits.json](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/splits.json)
- [dataset_manifest.json](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/dataset_manifest.json)
- [dataset_summary.md](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/dataset_summary.md)
- [quality_report.md](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/quality_report.md)

## 5. 刷新结果摘要

### 5.1 数据规模

- 总样本行数：`1450`
- 纳入实验数：`20`
- 节点数：`2`

### 5.2 Split 分布

- `train`：`541` 行
- `val`：`400` 行
- `test`：`509` 行

### 5.3 标签分布

- `backend_churn`：`19` 行（train=`6`，val=`7`，test=`6`）
- `conntrack_pressure`：`107` 行（train=`36`，val=`29`，test=`42`）
- `control_plane_recovery`：`22` 行（train=`7`，val=`5`，test=`10`）
- `load_surge`：`108` 行（train=`36`，val=`30`，test=`42`）
- `normal`：`1057` 行（train=`411`，val=`289`，test=`357`）
- `path_degradation`：`107` 行（train=`35`，val=`30`，test=`42`）
- `service_reconcile`：`30` 行（train=`10`，val=`10`，test=`10`）

### 5.4 关键 split 映射

本轮最关心的稀疏标签映射已经达到预期：

- `control_plane_recovery`
  - `agent-restart-20260523T083729Z` -> `train`
  - `agent-restart-20260523T113700Z` -> `val`
  - `map-rebuild-20260523T083956Z` -> `test`
- `conntrack_pressure`
  - `conntrack-pressure-20260523T084229Z` -> `train`
  - `conntrack-pressure-20260523T113900Z` -> `val`
  - `conntrack-pressure-20260523T114000Z` -> `test`
- `path_degradation`
  - `path-degradation-netem-20260523T084038Z` -> `train`
  - `path-degradation-netem-20260523T114300Z` -> `val`
  - `path-degradation-netem-20260523T114400Z` -> `test`
- `service_reconcile`
  - `service-delete-recreate-20260523T084325Z` -> `train`
  - `service-delete-recreate-20260523T114500Z` -> `val`
  - `service-delete-recreate-20260523T114600Z` -> `test`
- `load_surge`
  - `traffic-burst-20260523T084558Z` -> `train`
  - `traffic-burst-20260523T114100Z` -> `val`
  - `traffic-burst-20260523T114200Z` -> `test`

## 6. 质量检查结果

刷新后的质量结果见：

- [quality_report.md](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/quality_report.md)

关键结论：

- 错误数：`0`
- 警告数：`0`

这意味着：

- 上一轮遗留的 9 条 warning 已全部消失
- 所有异常标签现在都具备独立的 `train/val/test` 实验来源
- `windows_all.csv` 中未发现负值 `delta_*`
- 所有异常实验仍然带有目标 `label` 与 `recovery_active=1`

## 7. 判定

本轮结论为：

1. `phase3_v1` 已按计划直接覆盖刷新
2. 补采实验全部纳入成功
3. 标签覆盖与 split 结果符合预期
4. 数据质量报告达到 `errors=0`、`warnings=0`

因此：

**`Phase 3 Round 02` 通过。**

## 8. 下一步

`Dataset v1` 现在已经从“可用”变成了“覆盖完整且可直接做 baseline”。

下一步最自然的是进入 `Phase 4`：

1. 先写 `Phase 4` 中文 baseline 设计文档
2. 先跑规则法、`Logistic Regression`、`Random Forest`
3. 以当前 `phase3_v1` 作为固定输入，避免一边改数据一边跑模型
