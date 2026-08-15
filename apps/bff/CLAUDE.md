# CLAUDE.md — BFF

**このファイルは `apps/bff/` を触るときだけ読まれる。全セッション共通の規約は root の [`CLAUDE.md`](../../CLAUDE.md)。**

Azure Functions v4 + tRPC (単一エントリ `/api/trpc/*`)。起動・テストの手順は `dev` skill。

```bash
cd apps/bff
pnpm install          # Node 側は frontend も BFF も pnpm (#420)
pnpm run dev          # build:watch + func start (:7071)
pnpm run build        # tsc のみ
pnpm test             # vitest
pnpm run lint
pnpm run docs:openapi  # OpenAPI 再生成 (手書きしない)
```

**ここで `npm install` / `npm ci` を打たない。** 打っても*その場では*何も壊れず、
`package-lock.json` が生えて `pnpm-lock.yaml` と二重管理になり、CI の
`--frozen-lockfile` かデプロイの node_modules で初めて壊れる。`preinstall`
([`scripts/only-pnpm.mjs`](scripts/only-pnpm.mjs)) が npm を弾くが、**npm は
preinstall が落ちても `package-lock.json` を書く** — 生えていたら消す
(`.gitignore` で commit だけは止めている)。

デプロイ用の node_modules は開発用と作りが違う: Azure Functions の zip deploy は
symlink を復元しないので、[`cicd/scripts/deploy/lib/build-bff-package.sh`](../../cicd/scripts/deploy/lib/build-bff-package.sh)
が `--node-linker=hoisted` で実体のツリーを作り、symlink が 1 本も無いことを
確認してから zip する。**依存の追加でここが壊れていないかは、az 無しでこの
スクリプトを単体実行すれば確かめられる。**

## BFF は chat の素通しではない

AI Agent への薄いプロキシではなく、**アーティファクト生成を組み立てる層**。副作用を伴うツール呼び出しは `requiresApproval` を立てて人間に返し、承認を経てから実行する。ここを「そのまま流す」に単純化しない — 承認の門が消えても自動テストは緑のまま通る。

## stub fallback を壊さない

外部サービスの URL が未設定でも BFF は動く。これはローカルで外部依存ゼロで触れるための特性であり、**壊すと開発の入口が閉じる**。

| 変数                                      | 用途                               | 未設定時                                      |
| ----------------------------------------- | ---------------------------------- | --------------------------------------------- |
| `AI_AGENT_BASE_URL`                       | AI Agent service URL               | stub 応答                                     |
| `VOICEVOX_BASE_URL`                       | VOICEVOX Wrapper URL               | `/api/tts` が 204 → ブラウザ読み上げへ        |
| `AI_AGENT_AUDIENCE` / `VOICEVOX_AUDIENCE` | Container Apps 認証の門 (ADR 0017) | Authorization を付けない (門の無いローカル用) |
| `SPEECH_RESOURCE_ID` / `SPEECH_REGION`    | Azure Speech STT (ADR 0023)        | `speech.issueToken` が `available:false`      |
| `COSMOS_ENDPOINT`                         | Cosmos DB 永続化 (ADR 0030)        | in-memory リポジトリ (再起動で消える)         |

**正典は [`local.settings.json.example`](local.settings.json.example)** — 変数を増やしたらまずそこを更新し、未設定時の挙動をコメントで書く。値は Azure 上でのみ bicep が配線するので、ローカルは空のままが既定。

新しい外部依存を足すときも**未設定で動く経路を必ず用意する**。「設定されていなければ落ちる」にすると、ローカルとテストが同時に死ぬ。

## ログは相談の本文を運ばない

実環境の BFF のログは Application Insights / Log Analytics に 30 日残り、Azure のロールを持つ人なら誰でも読める。**相談の本文 (発話 / 応答 / statement / excerpt / title / summary) をそこへ出さない。**

- **テレメトリの出口は [`src/observability/telemetry.ts`](src/observability/telemetry.ts) 1 箇所**。`console.log` を直接書かない — Functions v4 では console はどの invocation の話か紐づかず、並行リクエストで突き合わせ不能になる
- 出せるフィールドは同ファイルの `ALLOWED_FIELDS` が持つ。**そこに無い名前は値ごと捨てられ `dropped=<名前>` だけが残る**。足したい名前が無いなら、それは本文である可能性が高い
- **ID (`sessionId` / `userId`) は呼び出し側でハッシュしない** — 出口の `HASHED_FIELDS` が `sessionHash=` / `userHash=` に変える。`sessionId` はスキーマが長さしか見ていない自由文字列で、**相関キーの顔をして本文を運べる** (#413)
- **許可リストはアプリが書いた行にしか効かない** — Functions ホストは `telemetry.ts` を通さず `AppRequests` に**受信 URL をそのまま**入れる。tRPC の query 入力は URL に載るので、[`host.json`](host.json) の `enableHttpTriggerExtendedInfoCollection: false` を**戻さない** (#413)
- 何を記録し何を落とすかの正典は [`docs/runbooks/bff-telemetry.md`](../../docs/runbooks/bff-telemetry.md)
- 下流ホップは `trackDependency` で挟む。**開始と終了を対で出す** — 終了だけにすると「呼んだが返ってこなかった」が沈黙になり、正常時の沈黙と混ざる (#293)

## 型は zod が真実、OpenAPI は生成物

- tRPC ルータの zod スキーマが API の正典。**`docs/api/` の OpenAPI は手書きしない** — `pnpm run docs:openapi` (root からは `npm run docs:openapi:bff`) で再生成する。
- 契約チェックは root の `npm run test:contract` (`scripts/contract-check.mjs`)。フロントと共有する型を変えたらここが落ちる。
- zod は BFF も frontend も v4 (#449 で BFF を追随)。OpenAPI / 契約チェックの JSON Schema 生成は zod-to-json-schema ではなく本体の `z.toJSONSchema` を使う。

## 触るときの注意

- `src/http/handlers.ts` が本番の Functions とローカルの `scripts/local-server.mjs` の**共通の入口**。片方だけに分岐を足さない (応答が一致しなくなる)。
- 承認・入力上限・stub 判定のような「静かに間違う」ロジックは純粋関数に切り出してテストする (`src/limits.ts` がその形)。入力サイズの上限は `limits.ts` が唯一の定義場所 — スキーマ側に直書きしない。
- **認証の門は Functions の EasyAuth** (401 を返すのは Functions 側)。アプリ側で独自の認可分岐を生やさない。ユーザー識別は `src/auth/clientPrincipal.ts` が `x-ms-client-principal` から解決する — userId は Cosmos のパーティションキーなので、ここを触ると「他人のデータが見える」に直結する。
