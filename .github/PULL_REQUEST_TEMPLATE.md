## 概要

<!-- 1〜2 行で何が変わったか / なぜ -->

## テスト設計

<!-- docs / refactor で実コード Δ なしなら "n/a" でよい -->

- **対象レイヤ**: <!-- L0 contract / L1 unit / L2 service / L3 e2e / L4 smoke / 該当なし -->
- **追加 or 変更したテスト**: <!-- 例: [L2] consultation.organize の異常系 1 本 -->
- **あえてテストしないこと** (スコープ外): <!-- 例: 個別スクリーンの UI snapshot -->

## チェックリスト

- [ ] `npm run test:fast` がローカルで緑
- [ ] テスト名に `[L0]`/`[L1]`/`[L2]`/`[L3]` プレフィックスを付けた (該当する場合)
- [ ] 新機能 → L2 を最低 1 本追加 / バグ修正 → 再現テストを 1 本追加
- [ ] snapshot を更新した場合、差分を目視で確認した
- [ ] 新しい mock を増やしていない (既存 fixture を再利用)

---

戦略: [docs/testing/strategy.md](../blob/main/docs/testing/strategy.md)
