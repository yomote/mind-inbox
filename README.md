# data/github-settings

**観測された GitHub 設定 (事実) の置き場**。orphan ブランチ (main と履歴を共有しない)。

- `snapshots/<owner>/<repo>.json` — 最後に観測した設定。点検のたびに上書きされ、
  **内容が変わった回だけコミットが立つ**。`git log -p` が「いつ何が変わったか」の記録

あるべき姿 (意図) は main の `cicd/github/settings.yml`。差分の出し方と適用手順は
`docs/runbooks/github-settings.md`。

**手で編集しない** (書き込みは `cicd/scripts/github-settings/write-snapshot.sh` 経由のみ)。
秘密の値は入れない — 保存するのは設定の形だけで、トークンも個人名も含まない。
