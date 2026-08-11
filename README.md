# data/ux-observations

UX 観測データの蓄積ブランチ (orphan / main と履歴を共有しない)。

- `probes/YYYY-MM.jsonl` — プローブ記録 (kind: `ux-probe-record`)
- `evals/YYYY-MM.jsonl` — 機械計測 (`ux-eval-mech`) と LLM 採点 (`ux-judge-score`)

1 行 = 1 観測。**手で編集しない** (書き込みは `cicd/scripts/ux-data/append-observation.sh` 経由のみ)。
判断の記録は ADR 0041、運用は docs/runbooks/ux-probe-judge.md。
