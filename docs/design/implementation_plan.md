# Mind Inbox — PoC 実装方針計画

作成: 2026-05-02 / 対象: `docs/design/basic_design.md` を実装に落とすロードマップ

---

## 0. 前提と方針

### 0.1 現状サマリ（2026-05-02 時点）

`basic_design.md` 第 2 章のターゲット構成と現リポジトリを突き合わせた差分。

| レイヤ                                                     | 設計要件                   | 現状                                                   | ギャップ                                                        |
| ---------------------------------------------------------- | -------------------------- | ------------------------------------------------------ | --------------------------------------------------------------- |
| AI Agent `/chat` `/organize` `/plan` `/approve` `/health`  | すべて                     | すべて実装済み（`apps/services/ai-agent/app/main.py`） | ほぼなし。プロンプトは `workflow.py` で Mind Inbox 用に更新済み |
| AI Agent `organizer.py` / `planner.py` / `repositories.py` | 新規                       | 実装済み                                               | ほぼなし                                                        |
| VOICEVOX Wrapper `POST /synthesize` → `audio/wav`          | バイナリ返却               | 実装済み（`apps/services/voicevox/app/main.py:53`）    | なし                                                            |
| BFF tRPC エントリポイント `/api/trpc/{*}`                  | 既存維持                   | 実装済み（`apps/bff/src/functions/trpc.ts`）           | なし                                                            |
| BFF `consultation.*` / `history.*` ルーター                | 新規（5+2 手続き）         | `chat.sendMessage` のみ                                | **未実装 — 最大ギャップ**                                       |
| BFF `aiAgentClient` の organize/plan/approve               | 追加                       | `sendChatMessage` のみ                                 | 未実装                                                          |
| BFF `voicevoxClient` の戻り値                              | `ArrayBuffer \| null`      | `{ audioUrl }`（stub URL を返すだけ）                  | 要書き換え                                                      |
| BFF `POST /api/tts`（非 tRPC Function）                    | 新規                       | なし                                                   | 未実装                                                          |
| BFF `HistoryRepository`（in-memory）                       | 新規                       | なし                                                   | 未実装                                                          |
| Frontend tRPC クライアント                                 | 新規                       | なし                                                   | 未実装                                                          |
| Frontend `api/` 抽象レイヤー                               | 新規（mock/real 切り替え） | `mockApi.ts` 直呼び                                    | 未実装                                                          |
| Frontend `Layout.tsx` の TTS 経路                          | `/api/tts` 経由            | Engine `:50021` を直接 2 ステップ呼び出し              | 要書き換え                                                      |
| Frontend Vite proxy `/api → :7071`                         | 新規                       | なし                                                   | 未実装                                                          |
| `VITE_VOICEVOX_BASE_URL` の撤去                            | 削除                       | `Layout.tsx:103` で参照中                              | 撤去対象                                                        |

### 0.2 実装原則

- **既存の動くフロントエンド（mockApi）を壊さない**順で進める。各ステップ単独で動作確認できる粒度に切る。
- **BFF を先、Frontend を後**。BFF は型のみフロントから参照されるため、先に固めれば下流が安定する。
- **stub fallback を活用**。AI_AGENT_BASE_URL / VOICEVOX_BASE_URL 未設定でも BFF が動く既存特性を維持し、各ステップを単独で smoke test できるようにする。
- **mock/real 切り替えは `VITE_USE_MOCK` で温存**。フロントエンドの api/ 層を入れた直後は `VITE_USE_MOCK=true` のまま現行挙動を維持し、BFF 疎通確認後に `false` にフリップする。
- **設計書の TODO(PoC) コメントは消さない**。in-memory の制約は本番差し替え点として明示し続ける。

---

## 1. 実装ステップ全体像

```text
Phase A: BFF 拡張（フロントは mockApi のまま動き続ける）
 ├─ A1. aiAgentClient に organize/plan/approve メソッド追加
 ├─ A2. voicevoxClient を ArrayBuffer | null に書き換え
 ├─ A3. /api/tts Azure Function 新設
 ├─ A4. HistoryRepository（in-memory）+ history ルーター新設
 └─ A5. tRPC ルーター再構成（consultation.*）

Phase B: Frontend 統合
 ├─ B1. tRPC + react-query 依存追加 + Vite proxy + trpc/client.ts
 ├─ B2. api/ 抽象レイヤー（mock/real 切り替え）
 ├─ B3. Layout.tsx を api/ レイヤー経由に差し替え（USE_MOCK=true で動作確認）
 ├─ B4. USE_MOCK=false に切り替え、BFF 疎通確認
 └─ B5. TTS 経路を /api/tts に差し替え + 不要環境変数撤去

Phase C: 結線確認・後片付け
 ├─ C1. 3 モードでの smoke test（stub のみ / AI Agent 起動 / フルスタック）
 ├─ C2. 旧 chat.sendMessage ルーターの撤去
 └─ C3. 環境変数テンプレート（local.settings.json.example, .env.local.example）の更新
```

各ステップは「変更ファイル」「動作確認手順」「完了条件」で定義する。

---

## 2. Phase A: BFF 拡張

### A1. `aiAgentClient.ts` に organize / plan / approve を追加

**目的**: AI Agent の既存エンドポイントに対応する HTTP メソッドを揃え、tRPC ルーター実装の前提を作る。

**変更ファイル**:

- `apps/bff/src/clients/aiAgentClient.ts`

**追加内容**:

- `organize({ sessionId }) → { summary, emotions, priorities }` — `POST /organize`
- `createPlan({ summary, emotions, priorities }) → { title, steps }` — `POST /plan`
- `approve({ approvalRequestId, approved }) → { reply }` — `POST /approve`
- いずれも `AI_AGENT_BASE_URL` 未設定時は stub レスポンスを返す（既存の `sendChatMessage` と同じパターン）。

**動作確認**:

- `npm run build` が通る。
- 既存の `chat.sendMessage` が壊れていない（動作変更なし）。

**完了条件**: TS コンパイルが通り、関数が export されている。tRPC からの利用は次ステップ以降。

---

### A2. `voicevoxClient.ts` を ArrayBuffer 返却に書き換え

**目的**: tRPC は JSON 前提のため、TTS のバイナリ転送は別経路。クライアントは `ArrayBuffer | null` を返し、呼び出し元（次の `/api/tts` Function）が HTTP レスポンスを組み立てる。

**変更ファイル**:

- `apps/bff/src/clients/voicevoxClient.ts`

**変更内容**:

- 戻り型: `Promise<SynthesizeResponse>` → `Promise<ArrayBuffer | null>`
- `VOICEVOX_BASE_URL` 未設定 → `null`
- 設定済み → `POST /synthesize` し `await res.arrayBuffer()` を返す
- `SynthesizeResponse` 型と `audioUrl` フィールドは削除

**注意**: 現在 `voicevoxClient` を使っているのは `router.ts` の `chat.sendMessage` だけ。`audioUrl` を使う箇所は実フロントには存在せず（`Layout.tsx` は `/audio_query` を直接叩いている）、安全に書き換えられる。`router.ts` 側の参照は次ステップ以降のルーター再構成で消えるので、A2 完了直後は `chat.sendMessage` から voicevoxClient 呼び出し部分のみ削除する一行修正を入れる（`withAudio` は該当ターンで誰も使っていない）。

**動作確認**:

- `npm run build` が通る。
- `/api/trpc/chat.sendMessage` を curl して `audioUrl` フィールド非依存でも返答が得られる。

**完了条件**: TS コンパイルが通り、`voicevoxClient.synthesize` が `ArrayBuffer | null` を返す。

---

### A3. `/api/tts` Azure Function 新設

**目的**: フロントからの `POST /api/tts` を受け、バイナリ `audio/wav` を返す（または stub 時 204）。設計書 3.2 節そのまま。

**新規ファイル**:

- `apps/bff/src/functions/tts.ts`

**実装要点**:

- `app.http("tts", { route: "tts", methods: ["POST"], authLevel: "anonymous", ... })`
- リクエスト: `{ text: string, speaker?: number }`（`zod` で軽くバリデーション）
- `synthesize({ text, speakerId: speaker ?? 3 })` を呼び出し、戻り値が `null` なら 204、`ArrayBuffer` なら 200 + `Content-Type: audio/wav`
- 既存 `trpc.ts` と並列に登録され、ルートが衝突しないこと（`trpc/{*trpcPath}` vs `tts`）

**動作確認**:

- BFF 起動 → `curl -X POST http://localhost:7071/api/tts -H 'Content-Type: application/json' -d '{"text":"こんにちは","speaker":3}' -o out.wav`
  - VOICEVOX_BASE_URL 設定済み: 200 + 再生可能な wav
  - 未設定: 204

**完了条件**: 上記 curl が両モードで期待通り動く。

---

### A4. HistoryRepository（in-memory）+ history ルーター追加

**目的**: 設計書 3.1 の `history.list` / `history.save` と、Phase B 後半で必要な保存先を確保する。

**新規ファイル**:

- `apps/bff/src/repositories/historyRepository.ts`

**実装要点**:

- `HistoryItem` 型と Zod schema を定義（id, title, createdAt, result, plan）
- `InMemoryHistoryRepository`（`_store: HistoryItem[]`、`list()` / `save(item)`）
- 配置場所はサーバ側のため、`apps/bff/src/repositories/` を新設
- `// TODO(PoC): 再起動で履歴が消える。本番では Cosmos DB に差し替える。` コメントを残す

**ルーター追加**: A5 で tRPC ルーターを再構成する際にまとめて配線する。A4 はリポジトリ実体だけ作る。

**完了条件**: TS コンパイルが通り、`new InMemoryHistoryRepository()` がインスタンス化できる。

---

### A5. tRPC ルーター再構成（consultation._/ history._）

**目的**: 設計書 3.1 のルーター構成に揃える。最重要ステップ。

**変更ファイル**:

- `apps/bff/src/trpc/router.ts`（全面改修）

**追加 / 変更**:

- `consultation` サブルーター
  - `start({ concern })` — `mutation`：UUID 採番 → `aiAgentClient.sendChatMessage({ sessionId, message: concern })` → `ConsultationSession` を組み立てて返す
  - `sendMessage({ sessionId, message })` — `mutation`：旧 `chat.sendMessage` を移設。`withAudio` は廃止、TTS 関連のロジックも削除
  - `organize({ sessionId })` — `mutation`
  - `createPlan({ result })` — `mutation`：`OrganizedResult` のみで完結（sessionId 不要）
  - `approve({ approvalRequestId, approved })` — `mutation`
- `history` サブルーター
  - `list()` — `query`：`HistoryRepository.list()`
  - `save({ sessionId, title, result, plan })` — `mutation`：UUID + createdAt を BFF で採番
- `health.ping` は据え置き
- 旧 `chat` サブルーターは Phase C2 で削除する。**A5 時点では残置**して既存リクエストを破壊しない（同名でなく `consultation.sendMessage` を新設するので衝突なし）

**型のフロント共有**: 既存どおり `export type AppRouter = typeof appRouter;` を維持。フロントは `import type { AppRouter } from '../../../bff/src/trpc/router'` する（PoC 範囲、設計書 9.2 に明記）。

**動作確認** (curl で全 7 手続きを叩く):

```bash
curl -X POST http://localhost:7071/api/trpc/consultation.start \
  -H 'Content-Type: application/json' \
  -d '{"concern":"テスト"}'
# → { "result": { "data": { "session": { ... } } } }
```

を `consultation.start` / `consultation.sendMessage` / `consultation.organize` / `consultation.createPlan` / `history.list` / `history.save` で実施。

**完了条件**: 全手続きが stub レスポンスでも正常応答（200）。Phase A 完了。

---

## 3. Phase B: Frontend 統合

**重要原則**: B1〜B3 は `VITE_USE_MOCK=true` のままで完結させ、フロントの体感挙動を変えない。B4 で初めて BFF へ向ける。

### B1. tRPC クライアント基盤

**変更ファイル**:

- `apps/frontend/package.json`（依存追加: `@trpc/client`, `@trpc/react-query`, `@tanstack/react-query`）
- `apps/frontend/vite.config.ts`（`server.proxy['/api']` 追加）
- `apps/frontend/src/trpc/client.ts`（新規）

**実装要点**:

- `vite.config.ts` の `server` セクションに `proxy: { '/api': { target: 'http://localhost:7071', changeOrigin: true } }`。これで `/api/trpc/*` と `/api/tts` の両方がカバーされる。
- `trpc/client.ts` は設計書 9.2 のまま（`createTRPCClient<AppRouter>`、`httpBatchLink`、`getBaseUrl()`）。
- 型 import は `import type { AppRouter } from '../../../bff/src/trpc/router'`。BFF をビルドしなくても `tsc` は型解決できる（`tsconfig.app.json` の include 範囲を確認、必要なら相対パス参照を許可するよう調整）。

**動作確認**:

- `npm run dev` でフロントが立ち上がり、TypeScript エラーが出ない。
- ブラウザコンソールで `await window.__trpc_test = trpc.health.ping.query()`（試験的に export しておく）を呼ぶと `{ ok: true }` が返る。**この段階ではこれだけ確認できれば良い**。

**完了条件**: フロントから BFF の `health.ping` を呼べる。既存画面の挙動は変わらない（mockApi のまま）。

---

### B2. api/ 抽象レイヤー（mock/real 切り替え）

**新規ファイル**:

- `apps/frontend/src/api/consultation.ts`
- `apps/frontend/src/api/history.ts`
- `apps/frontend/src/api/index.ts`（再 export）

**実装要点**:

- 各関数は `import.meta.env.VITE_USE_MOCK === 'true'` で `mockApi` 関数 or 実 tRPC 呼び出しに分岐（設計書 9.4）。
- `sendMessage` の戻り値は `ChatMessage` に整形（id, role: 'assistant', text, createdAt）。
- `organize` は `messages: ChatMessage[]` ではなく `sessionId: string` を引数に取る方針（mockApi も合わせてシグネチャ調整 — mockApi 側はシグネチャだけ揃えて、中身は最後のメッセージ参照のままで良い）。
- 設計書 9.4 の例どおり、export 名は既存の mockApi と同名（`startNewConsultation`, `sendMessage`, `organizeResult`, `createActionPlan`, `loadHistories`, `saveHistory`）にして Layout.tsx の修正を最小化する。

**動作確認**:

- `VITE_USE_MOCK=true` のまま `import { startNewConsultation } from './api'` を試行 → mockApi の同名関数が呼ばれる。

**完了条件**: api/ から関数が export され、USE_MOCK=true で旧挙動を完全再現できる。

---

### B3. `Layout.tsx` を api/ レイヤー経由に切り替え

**変更ファイル**:

- `apps/frontend/src/Layout.tsx`

**変更内容**:

- `import { ... } from './mockApi'` を `import { ... } from './api'` に置換。
- 関数シグネチャ差分（特に `organizeResult`）に合わせて呼び出し側を調整。
- `loadHistories` を `api.history.list` 経由に。`saveHistory` 呼び出し点を追加（保存ボタン押下時、設計書 5.2 の最後）。

**動作確認**:

- `VITE_USE_MOCK=true` でフロント全機能が今までどおり動く（音声 STT/TTS 含む — TTS はまだ直接 VOICEVOX 呼び出しのまま）。
- DevTools の Network タブで `/api/trpc/*` 呼び出しが**発生していない**ことを確認（USE_MOCK=true なので）。

**完了条件**: フロント挙動に退行がない。

---

### B4. `VITE_USE_MOCK=false` で BFF 疎通

**変更ファイル**:

- `apps/frontend/.env.local`（`VITE_USE_MOCK=false`）

**前提**:

- BFF 起動済み（`apps/bff` で `npm run dev`）。AI_AGENT_BASE_URL は **未設定**で OK（stub 経由で疎通確認）。

**動作確認シナリオ**:

1. オンボーディング → 新規相談 → concern 入力 → 「相談を始める」 → セッション画面に遷移
2. メッセージ送信 → stub 返答が表示される
3. 「整理する」 → stub の OrganizedResult が表示される
4. 「行動プランを作る」 → stub の ActionPlan が表示される
5. 「保存する」 → 履歴一覧に追加される（in-memory）
6. ページリロード → 履歴は消える（PoC 制約として明示）

**完了条件**: フロントが BFF stub と疎通する。AI Agent が必要な振る舞い（実 LLM 応答）は次の Phase C で確認。

---

### B5. TTS 経路を `/api/tts` に差し替え + 不要環境変数撤去

**変更ファイル**:

- `apps/frontend/src/Layout.tsx`

**削除対象**:

- `voicevoxBaseUrl` state（103-113 行）
- `VITE_VOICEVOX_BASE_URL` 参照
- Engine 向け warm-up `useEffect`（287-305 行）— `start-voicevox.sh` がエンジンレベルで warm-up 済み

**書き換え対象** (`synthesizeWithVoicevox`):

- 旧: `POST {baseUrl}/audio_query?text=...&speaker=...` → `POST {baseUrl}/synthesis?speaker=...` の 2 ステップ
- 新: 設計書 6.3 のサンプルどおり `POST /api/tts` 1 ステップ。`res.status === 204` なら `throw new Error('TTS_STUB')` してフォールバック（Web SpeechSynthesis）をトリガー。

**残す設定**:

- `VITE_VOICEVOX_SPEAKER`（話者 ID、デフォルト `3`）

**動作確認**:

- VOICEVOX_BASE_URL 未設定の BFF: フロントは 204 を受けて Web SpeechSynthesis で読み上げ。
- VOICEVOX_BASE_URL 設定済みの BFF（`start-voicevox.sh` + Wrapper 起動）: 200 + audio/wav で VOICEVOX 音声が再生される。

**完了条件**: フロントから VOICEVOX Engine への直接通信が完全になくなり、`/api/tts` 経由で動く。Phase B 完了。

---

## 4. Phase C: 結線確認・後片付け

### C1. 3 モードでの smoke test

| モード        | AI_AGENT_BASE_URL       | VOICEVOX_BASE_URL       | 期待挙動                                                           |
| ------------- | ----------------------- | ----------------------- | ------------------------------------------------------------------ |
| stub-only     | 未設定                  | 未設定                  | 全フローが stub 応答で通る。TTS は 204 → Web Speech フォールバック |
| AI Agent live | `http://localhost:8000` | 未設定                  | LLM 実応答。TTS は Web Speech フォールバック                       |
| フルスタック  | `http://localhost:8000` | `http://localhost:8001` | LLM 実応答 + VOICEVOX 音声再生                                     |

各モードで設計書 5.1〜5.3 のシーケンス（セッション開始 → チャット → 整理 → プラン → 保存）を一通り通す。

### C2. 旧 `chat.sendMessage` ルーターの撤去

A5 で残置していた `chat` サブルーターと `chatRouter` 定義を `apps/bff/src/trpc/router.ts` から削除。`AppRouter` 型から `chat` キーが消えることでフロント側 `import type` が壊れないことを TS エラーで確認（フロントは既に `consultation.*` を使っている前提）。

### C3. 環境変数テンプレートの更新

- `apps/bff/local.settings.json.example` に `AI_AGENT_BASE_URL` / `VOICEVOX_BASE_URL` のコメント付きエントリを記載（設計書 9.5）。
- `apps/frontend/.env.local.example`（新規 or 既存）に `VITE_BFF_BASE_URL` / `VITE_VOICEVOX_SPEAKER` / `VITE_USE_MOCK` を記載。`VITE_VOICEVOX_BASE_URL` のエントリは削除。

---

## 5. 順序まとめ（チェックリスト）

```text
[ ] A1  apps/bff/src/clients/aiAgentClient.ts        — organize/createPlan/approve を追加
[ ] A2  apps/bff/src/clients/voicevoxClient.ts       — ArrayBuffer | null へ書き換え
[ ] A3  apps/bff/src/functions/tts.ts                — 新規 /api/tts Function
[ ] A4  apps/bff/src/repositories/historyRepository.ts — in-memory 実装
[ ] A5  apps/bff/src/trpc/router.ts                  — consultation.* / history.* に再構成
[ ] B1  apps/frontend/{package.json,vite.config.ts,src/trpc/client.ts} — tRPC 基盤
[ ] B2  apps/frontend/src/api/{consultation,history,index}.ts — mock/real 切り替え
[ ] B3  apps/frontend/src/Layout.tsx                 — api/ 経由に差し替え（USE_MOCK=true で確認）
[ ] B4  apps/frontend/.env.local                     — USE_MOCK=false で BFF 疎通確認
[ ] B5  apps/frontend/src/Layout.tsx                 — TTS を /api/tts に / VITE_VOICEVOX_BASE_URL 撤去
[ ] C1  smoke test 3 モード                          — stub / AI Agent / フルスタック
[ ] C2  apps/bff/src/trpc/router.ts                  — 旧 chat ルーター削除
[ ] C3  各種 *.example 更新                          — 環境変数テンプレート整備
```

---

## 6. リスクと先回りメモ

| リスク                                                                                                                   | 影響                                                | 対策                                                                                                                                  |
| ------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `import type { AppRouter }` の相対パスが `tsconfig.app.json` の `include` 外でエラー                                     | B1 で詰まる                                         | `tsconfig.app.json` の include に `../bff/src/trpc/router.ts` を追加。または `paths` で alias 化                                      |
| Vite proxy の preflight で 405                                                                                           | TTS の OPTIONS リクエストが Function に到達せず失敗 | Azure Functions Core Tools はデフォルトで CORS preflight を返すため通常問題ないが、ハマったら `apps/bff/host.json` の CORS 設定を確認 |
| AI Agent の `/organize` がセッションを見つけられない                                                                     | `consultation.organize` が 404 になる               | フロントから `sessionId` を必ず渡す。BFF 側で `sessionId` を検証してから AI Agent に投げる                                            |
| `consultation.start` の 2 段呼び出し（UUID 採番 → AI Agent `/chat`）でレイテンシ増                                       | UX 悪化                                             | PoC では許容。Phase 2 で AI Agent 側に `start_session` API を新設する選択肢もあり                                                     |
| `history.save` が UUID/createdAt を BFF 側で採番する設計と、フロントの mock が即時に id を割り当てている既存挙動との差異 | 楽観更新の整合性                                    | mock 側もシグネチャを「BFF が返した HistoryItem を state に push」に揃える                                                            |
| TTS のフォールバック発火条件                                                                                             | 204 だけでなく fetch 失敗時もフォールバックすべき   | `synthesizeWithVoicevox` の catch でも Web Speech 経路に流す（既存ロジックを温存）                                                    |

---

## 7. 完了の定義（PoC ゴール）

設計書 1.1 の 4 ユーザーフローを、フルスタックモードで一気通貫に動作させる:

1. **音声入力**: マイクボタン → STT → メッセージ送信 → AI 応答が VOICEVOX 音声で再生
2. **チャット**: 複数ターン会話が成立し、各応答が音声化される
3. **整理**: 「整理する」で `OrganizedResult` が表示される
4. **プラン**: 「行動プランを作る」で `ActionPlan` が表示される
5. **保存**: 履歴に追加され、`/history` 画面で参照できる（再起動で消えることは TODO として明示）

これを stub-only / AI Agent live / フルスタックの 3 モードで再現できれば PoC 完了。
