# Phase 6 Paper Assets v1

- Purpose: freeze Phase 6 generalization and ablation outputs into paper-ready tables and figures
- Source results:
  - `results/phase6_generalization_v1/`
  - `results/phase6_ablation_v1/`

## Directory layout

- `tables/`: CSV and LaTeX tables
- `figures/`: PNG figures
- `source/`: copied source summaries and manifests

## Notes

- Phase 6 generalization keeps the Phase 4 best models fixed and evaluates them on newly collected experiments.
- Phase 6 ablation reuses the Phase 4 best random-forest configurations and only changes feature groups.
