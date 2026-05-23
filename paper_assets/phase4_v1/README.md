# Phase 4 Paper Assets v1

- Source run id: `phase4-baselines-20260523T134916Z`
- Source results dir: `/home/ubuntu/wjy/ebpf_nodeport/results/phase4_baselines_v1`
- Purpose: freeze baseline outputs into paper-ready tables, figures, and source copies

## Directory layout

- `tables/`: CSV and LaTeX tables
- `figures/`: PNG figures
- `source/`: copied source JSON/CSV used to build the paper assets

## Notes

- No model retraining is performed in this export step.
- Binary results are summarized by model family best candidate.
- Multiclass results are summarized by model family best candidate.
- Held-out test metrics are only available for the best model selected in the original Phase 4 run. Other family rows keep test columns empty.
