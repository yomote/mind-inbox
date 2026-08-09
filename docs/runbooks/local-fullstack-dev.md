# ローカルでフルスタック起動（声の UX を評価する）

## Trigger

GitHub Pages のモック（フロントのみ）では **声の UX が評価できない**ときに使う。
モックは画面・動線までで、(1) ずんだもん読み上げは BFF→VOICEVOX が無いと出ない、
(2) 整理結果/プランの AI 応答も BFF が要る。本物の体験を手元で触るための手順。

> **音声認識（マイク）はサーバーではなくブラウザの Web Speech API 依存**。
> 本手順でも **PC の Chrome（要マイク許可・localhost は安全オリジン扱い）**で開くこと。
> Safari / Firefox / 一部スマホは非対応で「認識しない」。これはデプロイでは直らない。

## Prerequisites

- **Docker**（VOICEVOX エンジン用）
- **Node 22** + **pnpm 9** + **Azure Functions Core Tools v4**（`func`）
- **Python 3.11**（VOICEVOX wrapper 用。`pip` か `uv`）
- ポート空き: `50021`(エンジン) / `8001`(wrapper) / `7071`(BFF) / `5173`(frontend)
- Azure OpenAI のキーは**不要**（未設定なら BFF が chat をスタブ応答で返す。読み上げ＝VOICEVOX は本物のまま）

## Steps

ターミナルを 4 つ使う（各サービスを前面で起動して挙動を見る）。

1. **VOICEVOX エンジン**（Docker, :50021）

   ```bash
   cicd/scripts/local-voicevox/start-voicevox.sh
   # "VOICEVOX ready: http://127.0.0.1:50021" が出れば OK
   ```

2. **VOICEVOX wrapper**（FastAPI, :8001 — エンジンを叩く）

   ```bash
   cd apps/services/voicevox
   pip install -r requirements.txt
   # エンジンURLの既定は http://localhost:50021 なので未設定でOK
   uvicorn app.main:app --port 8001
   ```

3. **BFF**（Azure Functions, :7071 — wrapper を指す）

   `apps/bff/local.settings.json` を用意して `VOICEVOX_BASE_URL` を wrapper に向ける:

   ```bash
   cd apps/bff
   cp -n local.settings.json.example local.settings.json
   # local.settings.json の Values を編集:
   #   "VOICEVOX_BASE_URL": "http://localhost:8001"
   #   "AI_AGENT_BASE_URL": ""   ← 空のまま = chat はスタブ応答（OpenAIキー不要）
   npm install
   npm run dev    # build:watch + func start（:7071）
   ```

   > **`func` を入れたくない / 声を評価しない場合**は、Azure Functions Core Tools を使わずに
   > 素の node で同じ BFF を配信できる（[ADR 0032](../adr/0032-use-case-acceptance-tests-against-real-wiring.md)）。
   > 本番の Functions と**同じ `src/http/handlers.ts`** を呼ぶので応答は一致する。
   >
   > ```bash
   > cd apps/bff
   > npm install && npm run build          # dist を読むので build が要る
   > VOICEVOX_BASE_URL=http://localhost:8001 node scripts/local-server.mjs   # :7071
   > ```
   >
   > 差分は 2 点だけ: **ホットリロードが無い**（`npm run build` を都度叩く）ことと、
   > フロントが別ポートに居るため **CORS を開けている**こと。声の UX を通しで見るなら
   > `npm run dev`（`func start`）のままが楽。

4. **フロントエンド**（Vite, :5173 — 実 BFF を叩く）

   ```bash
   cd apps/frontend
   pnpm install
   VITE_USE_MOCK=false pnpm dev
   # ずんだもん話者を変えたい場合: VITE_VOICEVOX_SPEAKER=3 (既定=ずんだもん)
   ```

   → **PC の Chrome** で `http://localhost:5173` を開く。

## Verification

- [ ] `curl -fsS http://127.0.0.1:50021/version` がバージョンを返す（エンジン生存）
- [ ] `curl -fsS -X POST http://localhost:8001/synthesize -H 'Content-Type: application/json' -d '{"text":"テスト","speaker":3}' -o /tmp/t.wav` で wav が落ちる（wrapper→エンジン疎通）
- [ ] フロントで相談を開始 → アシスタント返信が **声で読み上げられる**（ずんだもん）
- [ ] Chrome でマイク許可 → 喋ると文字起こしが入力欄に入る（STT）
- [ ] 困りごと一覧/詳細/抽出は mock データで一周する（Phase D の Problem 系は現状 mock のまま）

## Rollback

1. 各ターミナルで `Ctrl+C`
2. VOICEVOX コンテナ停止: `docker rm -f voicevox-engine`

## Common Issues

### マイクが「認識しない」

- 原因: ブラウザが Web Speech API 非対応（Safari/Firefox/一部スマホ）、またはマイク未許可。
- 対処: **PC の Chrome** で開き、アドレスバーのマイクを「許可」。`localhost` は安全オリジン扱いなので HTTPS 不要。

### 読み上げが無音 / `/api/tts` が 204

- 原因: `VOICEVOX_BASE_URL` 未設定（BFF が stub の 204 を返し、ブラウザ TTS にフォールバック）。
- 対処: `apps/bff/local.settings.json` の `VOICEVOX_BASE_URL=http://localhost:8001` を確認して `func` を再起動。

### `/api/*` が 404 / フロントが BFF を叩けない

- 原因: `VITE_USE_MOCK` が `false` でない、または BFF(:7071) 未起動。
- 対処: フロントを `VITE_USE_MOCK=false` で起動し、BFF が起動済みか確認（Vite は `/api` を :7071 にプロキシ）。

### 整理結果やプランの中身が固定文言

- 原因: `AI_AGENT_BASE_URL` 未設定で BFF が chat/organize をスタブ応答。
- 対処: 声の UX 評価には不要。実 AI 応答が要るなら ai-agent(:8000) を起動し `AI_AGENT_BASE_URL=http://localhost:8000` を設定（Azure OpenAI キーが必要）。

## Related

- ADR: [0004 mockApi as frontend truth](../adr/0004-mockapi-as-frontend-truth.md)
- スクリプト: `cicd/scripts/local-voicevox/start-voicevox.sh`
- 設定例: `apps/bff/local.settings.json.example`
