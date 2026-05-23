# Phase 3 Round 01 构建记录

## 1. 记录概览

- 测试时间：2026-05-23 11:34 UTC - 11:35 UTC
- 记录目的：
  - 完成 `Phase 3` 首轮数据集构建
  - 验证离线脚本链路是否可直接从 `artifacts/` 产出 `Dataset v1`
- 结论：**Round 01 通过，可进入 Phase 4 baseline 设计与实现**

## 2. 本轮新增内容

### 2.1 Phase 3 设计文档

已新增：

- [aipr_phase3_dataset_construction_design.md](/home/ubuntu/wjy/ebpf_nodeport/aipr_phase3_dataset_construction_design.md:1)

这份文档固定了：

- `Dataset v1` 的目录结构
- 样本表保留字段
- 按 `experiment_id` 切分的原则
- 质量检查门槛
- `Phase 3` 的退出标准

### 2.2 Phase 3 离线脚本

已新增三份离线脚本：

- [phase3_build_dataset.py](/home/ubuntu/wjy/ebpf_nodeport/scripts/phase3_build_dataset.py:1)
- [phase3_split_dataset.py](/home/ubuntu/wjy/ebpf_nodeport/scripts/phase3_split_dataset.py:1)
- [phase3_dataset_report.py](/home/ubuntu/wjy/ebpf_nodeport/scripts/phase3_dataset_report.py:1)

职责分别为：

1. 扫描 `artifacts/` 并构建 `raw_index.csv` 与 `windows_all.csv`
2. 按 `experiment_id` 做 split
3. 生成 `dataset_summary.md`、`quality_report.md` 和最终 manifest

## 3. 执行的命令

本轮执行顺序如下：

```bash
python3 -m py_compile scripts/phase3_build_dataset.py scripts/phase3_split_dataset.py scripts/phase3_dataset_report.py
python3 scripts/phase3_build_dataset.py
python3 scripts/phase3_split_dataset.py
python3 scripts/phase3_dataset_report.py
```

结果：

- 三个脚本均通过语法检查
- 数据集构建成功
- 质量报告未发现阻塞性错误

## 4. Dataset v1 产物

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

## 5. 构建结果摘要

### 5.1 数据规模

- 总样本行数：`979`
- 纳入实验数：`11`
- 节点数：`2`

### 5.2 Split 分布

- `train`：`541` 行
- `val`：`167` 行
- `test`：`271` 行

### 5.3 标签分布

- `normal`：`826`
- `backend_churn`：`19`
- `control_plane_recovery`：`17`
- `path_degradation`：`35`
- `conntrack_pressure`：`36`
- `service_reconcile`：`10`
- `load_surge`：`36`

### 5.4 通过的质量检查

- telemetry schema 一致
- 所有 `delta_*` 字段未发现负值
- 所有异常实验都出现了目标 `label`
- 所有异常实验都出现了 `recovery_active=1`
- `windows_train/val/test.csv` 均成功生成

## 6. 发现的非阻塞问题

本轮没有阻塞性错误，但存在数据覆盖不足的 warning。

当前以下标签尚未获得完整的 `val/test` 覆盖：

- `conntrack_pressure`
- `path_degradation`
- `service_reconcile`
- `load_surge`

另外：

- `control_plane_recovery` 当前已有 `train` 和 `test`，但还没有 `val`

这不是 Phase 3 的阻塞项，因为当前每类只有 1 个或 2 个实验属于正常现象，但它会直接影响 Phase 4 的“按标签泛化”说服力。

## 7. 判定

本轮结论为：

1. `Dataset v1` 已成功产出
2. 数据质量达到进入 baseline 实验的最低要求
3. 当前更大的短板不是构建链路，而是个别标签的实验轮次还偏少

因此：

**`Phase 3 Round 01` 通过。**

## 8. 下一步

下一步建议分成两条并行线：

1. 进入 `Phase 4`，先跑第一轮 baseline
2. 回到 `Phase 2` 补以下场景的额外实验轮次：
   - `conntrack_pressure`
   - `path_degradation_netem`
   - `service_delete_recreate`
   - `traffic_burst`

这样做的好处是：

- baseline 可以先起跑，不必空等
- 后续再补数据时，不需要回改 Phase 3 schema，只需重跑离线脚本即可刷新 `Dataset v1`
