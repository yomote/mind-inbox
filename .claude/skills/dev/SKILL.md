---
name: dev
description: ローカルで Mind Inbox を起動して動かす・ブラウザで確かめる・テストやリントを回すときの手順。frontend / BFF / AI Agent / VOICEVOX の起動コマンド、BFF も認証も要らないモックモード、`npm run test:fast` / `npm run lint` / `npm test` の使い分けを扱う。UI や API を変えて動作を確認するとき、PR を出す前に緑を取るとき、テストが落ちて切り分けたいとき、user が「/dev」「ローカルで動かして」「起動して」「テスト回して」等と言ったときに起動。
---

# dev

ローカルでアプリを起動し、**動かして確かめる**ための手順。詳細な通し手順は [ローカルフルスタック起動 Runbook](../../../docs/runbooks/local-fullstack-dev.md) が正典。

## 最初に踏む地雷

- **アプリ (frontend / BFF) は pnpm、リポジトリ root だけ npm** (`package.json` / `package-lock.json`)。root の script が各アプリへ委譲する。
- **`apps/frontend` `apps/bff` に `npm install` を打たない** (取り違えると lock が二重になる)。BFF は preinstall で弾くが、npm は弾かれても `package-lock.json` を書くので、生えていたら消す (#420)。
- **Python は uv が正典** (`apps/services/ai-agent/uv.lock`)。

## 最短で動かす (BFF も認証も Azure も不要)

```bash
pnpm --dir apps/frontend install
VITE_USE_MOCK=true pnpm --dir apps/frontend dev   # → http://localhost:5173/
```

`VITE_USE_MOCK=true` は `mockApi.ts` で自己完結し、BFF・Entra 認証・課金リソースを一切呼ばない。画面と動線はこれで一周できる ([ADR 0004](../../../docs/adr/0004-mockapi-as-frontend-truth.md))。

**UI を変えたら、このモードでブラウザまで開いて確認する** — 「テストが緑」で代替しない。確認したことは PR に**振る舞い**で書く (「設定した」ではなく「叩いたらこう返った」/ [ADR 0018](../../../docs/adr/archive/operations/runtime-verification-in-the-loop.md))。取れなかった結果を「異常なし」と書かない。

## 各サービスを起動する

| サービス          | ポート  | 起動                                                                                                        |
| ----------------- | ------- | ----------------------------------------------------------------------------------------------------------- |
| frontend (Vite)   | `5173`  | `pnpm --dir apps/frontend dev` (実 BFF を叩くなら `VITE_USE_MOCK=false`)                                    |
| BFF (Functions)   | `7071`  | `cd apps/bff && pnpm install && pnpm run dev` (build:watch + `func start`)                                  |
| AI Agent          | `8000`  | `cd apps/services/ai-agent && pip install -e . && uvicorn app.main:app --reload --port 8000`                |
| VOICEVOX wrapper  | `8001`  | `cd apps/services/voicevox && pip install -r requirements.txt && uvicorn app.main:app --reload --port 8001` |
| VOICEVOX エンジン | `50021` | `cicd/scripts/local-voicevox/start-voicevox.sh` (Docker / 停止は `stop-voicevox.sh`)                        |

- BFF は `func` (Azure Functions Core Tools v4) が要る。**入れずに済ませたい**なら `pnpm --dir apps/bff run build` 後に `node scripts/local-server.mjs` で同じハンドラを配信できる (ホットリロード無し + CORS 開放の差分のみ)。
- BFF の設定は `apps/bff/local.settings.json` (`local.settings.json.example` を `cp`)。**未設定の外部サービスは stub にフォールバックする**ので、声だけ見たいなら `VOICEVOX_BASE_URL` だけ入れれば足りる (詳細は `apps/bff/CLAUDE.md`)。
- **声 (読み上げ) は VOICEVOX、マイク入力はブラウザの Web Speech** — マイクは **PC の Chrome** で開かないと動かない (Safari / Firefox は非対応)。

## テストとリント

```bash
npm run test:fast   # bff / frontend / python / scripts を並列 — PR 前はまずこれを緑に
npm run lint        # eslint (bff/frontend) + ruff + markdownlint
npm test            # test:contract → test:fast → test:e2e (通し)
```

個別に回すとき:

- `npm run test:bff` / `npm run test:frontend` / `npm run test:python` / `npm run test:scripts`
- `npm run test:contract` — tRPC 契約チェック
- `npm run test:e2e` — Playwright (旧 L3 mock E2E。**廃止方針なので新規シナリオを足さない** / [テスト戦略](../../../docs/testing/strategy.md) §6.3)
- `npm run lint:md` だけ回したいときは `npx markdownlint-cli2 "<変えたファイル>"`
- 整形は `npm run format` (prettier + ruff format)

**PR を出す前に `npm run test:fast` をローカルで緑にする。** テストを足すときの判断基準は [テスト戦略](../../../docs/testing/strategy.md) が正典 (「無いと何が静かに通るか?」を 1 文で書けないテストは書かない)。

## 動かないときの当たり

| 症状                           | 当たり                                                                               |
| ------------------------------ | ------------------------------------------------------------------------------------ |
| `/api/*` が 404                | `VITE_USE_MOCK` が `false` になっているか / BFF (`:7071`) が起動しているか           |
| 読み上げが無音・`/api/tts` 204 | `VOICEVOX_BASE_URL` 未設定 (stub の 204 → ブラウザ読み上げにフォールバック)          |
| 抽出やプランが固定文言         | `AI_AGENT_BASE_URL` 未設定で stub 応答 (声の評価には不要)                            |
| マイクが認識しない             | Chrome 以外で開いている / マイク未許可 (`localhost` は安全オリジン扱いで HTTPS 不要) |
