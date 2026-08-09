# CD watchdog Routine — 赤くなった CD の無人診断・修正

> 判断の背景: [ADR 0026](../adr/0026-cd-watchdog-routine.md)。
> deploy / golden-path-monitor / build-images が赤のとき、PO が気付く前に診断と fix PR を進める毎時の Routine。

## 何が動いているか

- **Routine 名**: `cd-watchdog — CD 赤の自動診断と fix PR`
- **スケジュール**: 毎時 (Routine の最短間隔)。発火ごとに新規セッションを起動 (fresh session)
- **通知**: 完了時にプッシュ通知 (特筆事項があるときのみ届く)
- **管理場所**: Claude Code on the web の Routine (リポジトリ内のファイルではない)。一覧・停止・削除は claude.ai/code の Routines 画面、またはセッションから `list_triggers` / `update_trigger` / `delete_trigger`

## Routine が毎時やること

1. deploy.yml / golden-path-monitor.yml / build-images.yml の直近 run (main) の conclusion を確認
2. **全緑 → 何も作らず終了** (Issue / PR / コメントを残さない)
3. 赤があれば重複チェック: `cd-watchdog` ラベルの open Issue、該当 run ID に言及する open PR/Issue を検索。対応中なら新情報がある場合のみ追記して終了
4. 未対応の赤は失敗 job のログから診断:
   - 小さく確実な修正 (CD スクリプト / workflow / 設定のバグ) → `claude/cd-watchdog-*` ブランチで fix PR を作成。**merge はしない**
   - 一過性の疑い → 該当 workflow を workflow_dispatch で 1 回だけ再実行して確認
   - 実環境の状態確認が必要 → golden-path-monitor.yml を workflow_dispatch で実測 (実 AI + 合成で数円/回、1 セッション 1 回まで)
   - 不可逆な判断 (インフラ削除 / 課金追加 / 公開 API の形 / データ操作) → 実装せず `needs-human` Issue に選択肢形式で積む (ADR 0020)
5. 対応内容を `cd-watchdog` ラベルの Issue に記録 (open があれば追記、なければ作成)

### 触ってはいけない境界 (Routine プロンプトにも明記)

- main への直接 push / PR の merge / Azure リソースの変更 (読み取りと workflow 再実行のみ)
- プロダクトコードの機能変更 (直せるのは CD・テスト・スクリプトの確実なバグまで)

## 運用

### 一時停止 / 再開

claude.ai/code の Routines 画面で該当 Routine を disable / enable。またはセッションから:

```text
list_triggers で trigger_id を確認 → update_trigger { trigger_id, enabled: false }
```

### 作り直す (プロンプトを変えたいとき)

`update_trigger` の prompt 差し替えで足りる (削除→再作成は run 履歴が消えるので避ける)。プロンプトの正本はこの Runbook の「Routine が毎時やること」+「境界」であり、変更するときは**この Runbook を先に直してから** Routine に反映する。

### うるさいとき

- 同じ赤に PR/Issue が重複した → 重複分を閉じ、プロンプトの重複チェック節を強化して update_trigger
- 全緑なのに通知が来る → 完了通知は「特筆事項あり」のみのはず。Routine の最終レポートが不要な報告をしていないかセッションログを確認

## 既知の限界

- 反応開始は最大 1 時間遅れる (毎時ポーリング)。赤の**発生**自体は deploy.yml 内の実測 (smoke → golden-path → UI E2E, ADR 0018/#111) が数分で出す
- Routine のセッションは Azure 資格情報を持たない。環境の実測は golden-path-monitor の workflow_dispatch 経由で行う
