# Phase 4 Baseline 摘要

- 运行 ID：`phase4-baselines-20260523T134916Z`
- 数据集目录：`/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1`
- 最终特征列数：`27`
- 被移除的常量列数：`10`

## 1. 二分类最佳模型

- 模型：`rf_n100_dnone_leaf5_cwnone`
- 验证集 anomaly F1：`0.7349`
- 验证集 PR-AUC：`0.8277`
- 测试集 anomaly F1：`0.7774`
- 测试集 PR-AUC：`0.8449`

## 2. 多分类最佳模型

- 模型：`rf_n100_dnone_leaf5_cwbalanced`
- 验证集 macro-F1：`0.3722`
- 验证集 balanced accuracy：`0.3744`
- 测试集 macro-F1：`0.4181`
- 测试集 balanced accuracy：`0.4159`

## 3. 多分类测试集按类表现

- `backend_churn`：precision=`0.0000`， recall=`0.0000`，f1=`0.0000`，support=`6`
- `conntrack_pressure`：precision=`0.5957`， recall=`0.6667`，f1=`0.6292`，support=`42`
- `control_plane_recovery`：precision=`0.8333`， recall=`0.5000`，f1=`0.6250`，support=`10`
- `load_surge`：precision=`0.4038`， recall=`0.5000`，f1=`0.4468`，support=`42`
- `path_degradation`：precision=`0.6667`， recall=`0.4286`，f1=`0.5217`，support=`42`
- `service_reconcile`：precision=`0.2222`， recall=`0.4000`，f1=`0.2857`，support=`10`

## 4. 说明

- 二分类正类固定为 `anomaly`
- 多分类只在异常窗口上训练和评估
- 本轮所有结果均基于固定的 `phase3_v1` 数据集
