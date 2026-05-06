## 概要

<!-- 1〜2 行で何が変わったか / なぜ -->

## テスト設計

<!-- docs / refactor で実コード Δ なしなら "n/a" でよい -->

- **対象レイヤ**: <!-- L0 contract / L1 unit / L2 service / L3 e2e / L4 smoke / 該当なし -->
- **追加 or 変更したテスト** — 各 test に「無いと何が静かに通るか」を 1 文添える: <!-- 例: `[L2] consultation.organize 異常系` — LLM 出力が malformed の時に 500 で落ちる退行を止める -->
- **書かなかった理由** (テストを書かない箇所がある場合、何故か): <!-- 例: 内部 helper は L2 が通し検証するので L1 重複を避けた -->

## Docs 更新

<!-- typo / 内部リファクタで public I/O 不変なら "n/a" でよい -->

- **更新したドキュメント** — 種別を明示 (MDX UI 仕様 / OpenAPI / ADR / Runbook / CLAUDE.md / 戦略 doc): <!-- 例: `docs/frontend/ui_specs/result.mdx` を先に更新 → 実装が追従 -->
- **更新しなかった理由** (該当領域があるが触らない場合、何故か): <!-- 例: 環境変数追加だが local-only でドキュメント化対象外 -->

## チェックリスト

- [ ] `npm run test:fast` がローカルで緑
- [ ] テスト名に `[L0]`/`[L1]`/`[L2]`/`[L3]` プレフィックスを付けた (該当する場合)
- [ ] 新機能 → L2 を最低 1 本追加 / バグ修正 → 再現テストを 1 本追加
- [ ] snapshot を更新した場合、差分を目視で確認した
- [ ] 新しい mock を増やしていない (既存 fixture を再利用)
- [ ] UI 仕様 (MDX) を更新した / 不要
- [ ] OpenAPI が再生成済み (CI 緑) / 不要
- [ ] アーキテクチャ判断は ADR に書いた / 不要
- [ ] 運用手順の変更は Runbook に反映した / 不要

---

戦略: [docs/testing/strategy.md](../blob/main/docs/testing/strategy.md) / [docs/documentation/strategy.md](../blob/main/docs/documentation/strategy.md)
