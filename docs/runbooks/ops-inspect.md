# Runbook: ops-inspect — サンドボックスの外を見る

エージェントのセッションから届かない「事実」を、GitHub Actions の runner を踏み台にして取るための手順。判断の根拠は [ADR 0031](../adr/archive/operations/agent-reaches-outside-via-github-actions.md)。

## いつ使うか

| 知りたいこと                                                  | `check`             | 備考                                                                                      |
| ------------------------------------------------------------- | ------------------- | ----------------------------------------------------------------------------------------- |
| Azure に今どのリソースがあるか                                | `azure-resources`   | `inspect-env.sh` の詳細ダンプも併せて出る                                                 |
| Cosmos DB の free tier がこのサブスクリプションで空いているか | `cosmos-free-tier`  | [ADR 0030](../adr/0030-persistence-on-cosmos-db-single-store-behind-bff.md) D2 の判断材料 |
| 当月いくら使っているか (予算 ¥3,000 に対して)                 | `cost-summary`      | SP に課金データの参照権が無いと `(未検証)` になる                                         |
| egress の外にあるページの本文                                 | `fetch-doc` + `url` | `https://` のみ。HTML をテキスト化して 60,000 文字まで                                    |

## エージェントからの使い方

```
actions_run_trigger {
  method: "run_workflow", workflow_id: "ops-inspect.yml",
  ref: "ops/inspect", inputs: { check: "cosmos-free-tier" }
}
```

1. 起動したら `actions_list` (`list_workflow_runs` → `list_workflow_jobs`) で `job_id` を取る
2. `get_job_logs` で `return_content: true` にして読む
3. 出力の末尾に `kind: "ops-inspect-result"` の JSON ブロックが 1 つある。`status` が `unverified` のときは **取得できていない** — `note` に理由が入る

> ⚠️ **`ref` は普段 `ops/inspect` を指定すること。** guard が ref で Azure へのログイン識別を切り替える — `main` 以外は **read-only 識別 (`AZURE_CLIENT_ID_RO`)**、`main` はデプロイ SP (`AZURE_CLIENT_ID`)。読むだけの調査に書き込み識別を使わないため、また **クエリを 1 行直すたびに main へマージしなくて済む**ため (#209)、`ops/inspect` から起動する。仕組みは [azure-oidc-cd-setup.md](azure-oidc-cd-setup.md) の「read-only の識別を足す」。
>
> - `workflow_dispatch` は**既定ブランチに存在するワークフローしか起動できない** (メニューに載る条件)。新しい `check` は main にマージされて初めて選べる — ただし実行される YAML は `ref` に指定したブランチのもの
> - `check` を変えて試すときは `ops/inspect` へ直 push して dispatch する (このブランチだけは直 push が正規の運用)。`ops/inspect` が main から遅れているときは main をマージして push しておく

## 人間からの使い方

GitHub の Actions タブ → `ops-inspect` → **Run workflow** → Branch で **`ops/inspect`** を選び、`check` を選んで実行。結果は job summary に出る。Branch を既定の `main` のままにするとデプロイ SP でログインする (動くが read-only ではない)。

## workflow artifact をエージェントが取る (失敗した run の証拠)

> **できる。** 2026-08-09 の [ADR 0029](../adr/archive/operations/probe-record-transport-via-issue-comment.md) は「agent セッションからは artifact をダウンロードできない」を前提に書かれたが、その直後に `*.blob.core.windows.net` が egress 許可に入った (#168 / 下の節)。**前提が変わったのに再測定されず、「取れない」だけが 4 日間リポジトリ中に残って調査を止めていた** (#287 / #293)。

`gh` は使えないので **MCP + `curl` の 2 段**で取る。

1. `actions_list` (`list_workflow_run_artifacts`, `resource_id` = run ID) で artifact の `id` を引く
2. `actions_get` (`download_workflow_run_artifact`, `resource_id` = artifact ID) が **署名付き URL** を返す
3. その URL を `curl -o out.zip "<url>"` で落として `unzip` する (URL は数分で失効する。**必ずクエリ文字列ごと引用符で囲む**)

`e2e-live-failure-*` / `playwright-live-report-*` に入っている `error-context.md` (aria スナップショット) と `test-failed-1.png` は、E2E が落ちた瞬間の画面そのもの。**「入力欄が disabled だったのか」級の問いはこれで決着する。**

**取れないもの**: `e2e-live-trace-*` (Playwright の trace) は公開鍵で暗号化されている ([ADR 0045](../adr/archive/operations/e2e-artifacts-are-secret-by-default.md) — 記録は archive だが暗号化そのものは現役)。ダウンロードはできるが**復号できるのは PO だけ** (手順は [`e2e-trace-keys.md`](e2e-trace-keys.md))。trace にしか無い情報 (アクション実行の記録・DOM の時系列) が要る場合はここで人手が要る — その一歩手前まではエージェントで進む。

**動作確認 (2026-08-13)**: run `31648071011` の `playwright-live-report-31648071011` を `http=200 size=83809` で取得し、`error-context.md` を読めた。

## 触るときの約束 (ADR 0031 D2)

このワークフローを拡張するとき、**次の 2 つは絶対に破らないこと**。

1. **自由入力のコマンドを受け取らない。** 操作は `type: choice` の固定値だけ。任意コマンドを受ける口を作ると、この便利屋がリポジトリで最も強い書き込み経路になる。
   - #46 で SP の権限は **サブスクリプション Contributor → RG スコープ**に縮んだ ([azure-oidc-cd-setup.md](azure-oidc-cd-setup.md) の「権限モデル」)。被害半径は減ったが、**約束は変えない** — dev の RG ごと壊せる主体であることに変わりはない
   - さらに、このワークフローは読むだけなので **read-only 識別 (`AZURE_CLIENT_ID_RO`) で起動するのが本筋** — 上の「エージェントからの使い方」のとおり `ref: ops/inspect` で起動すれば guard がそちらでログインする。`ref: main` だと今もデプロイ SP なので、read-only で走らせたい調査を main からやらない。仕組みと受容条件は [azure-oidc-cd-setup.md](azure-oidc-cd-setup.md) の「read-only の識別を足す」「read-only 識別の受容条件 (正典)」
2. **入力をシェルに展開しない。** `run:` の中に `${{ inputs.* }}` を直接書かず、必ず `env:` 経由で渡して `"$VAR"` として引用符付きで参照する (`${{ }}` は run の実行前にテキスト置換されるため、直接書くと注入口になる)

調べたい項目が増えたら、**使い捨てワークフローを作らずにこのファイルへ `check` を足す** (ADR 0031 D4)。

## 現在許可されているネットワークドメイン (環境設定の写し)

環境設定は claude.ai 側にあり**リポジトリ管理外**なので、ここは写しであって真実ではない。乖離しうる。実体は claude.ai/code の環境ダイアログ → **Network access**。

- **レベル**: `Custom` (+ 「Also include default list of common package managers」を有効)
- **追加した許可**: `learn.microsoft.com` / `azure.microsoft.com` / `prices.azure.com` / `*.blob.core.windows.net`
- **理由と経緯**: [#168](https://github.com/yomote/mind-inbox/issues/168) (2026-08-09 適用)。ADR 0030 の設計時に Azure の料金・仕様の一次情報へ到達できず、月額の判断材料が二次情報の概算になったため

変更したらこの節を更新する。

## 関連

- [ADR 0031](../adr/archive/operations/agent-reaches-outside-via-github-actions.md) (この仕組みの判断) / [ADR 0018](../adr/archive/operations/runtime-verification-in-the-loop.md) (実態の読み取り) / [ADR 0006](../adr/0006-azure-access-via-device-code.md) (Azure 対話ログインの制約)
- `cicd/scripts/smoke-test/inspect-env.sh` — `azure-resources` が内部で流す詳細ダンプ
- 対: `smoke-test.sh` (合否を出して CD を止める) / `inspect-env.sh` (人が読む。判定しない) / **`ops-inspect` (エージェントが読む。判定しない)**
