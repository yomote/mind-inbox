# 0032. ユースケース受け入れテストを「mock を通らない実配線」で持つ (L3-real)

- Status: Proposed
- Date: 2026-08-09
- Deciders: yomote (PO) / 実装セッション
- Related: [ADR 0004](0004-mockapi-as-frontend-truth.md) (mockApi = フロントの真実) / [ADR 0018](0018-runtime-verification-in-the-loop.md) (動作検証をループに組み込む) / [ADR 0007](0007-problem-centric-two-layer-domain-model.md) (Problem 中心 2層) / #165 (永続化)

## Context

2026-08-09、PO が dev の SWA を触って「**ボタンを押しても何も起きない**」と報告した。
そのとき CI は全緑だった。内訳:

- L1/L2 (vitest, 92 + 74 件) — 緑
- L3 mock E2E (Playwright, 9 件) — 緑
- L4 live — 直近の golden-path-monitor は赤だったが、対象は「相談 1 往復 + TTS」だけ

調べた結果、原因は 2 つに分かれた。

**(a) 無音失敗**: `useConsultation` は `startConsultation` だけが例外を捕まえていて、
抽出 / 一覧 / 送信 / トリアージは unhandled rejection のまま画面が沈黙していた。
BFF を 500 にして実測した挙動:

| 操作           | 実際に起きたこと                       |
| -------------- | -------------------------------------- |
| 困りごとを抽出 | 遷移せず、エラーも出ない               |
| 困りごと一覧   | ホームに留まる。押した感触すらない     |
| 送信           | 自分の発話だけ残り、返事が永遠に来ない |

**(b) テストがユースケースを見ていない**: 既存の層には**構造的な穴**があった。

```
L1/L2  … BFF の router と hook を「別々に」見る (繋がっているかは見ない)
L3 mock… ブラウザ ── mockApi (フロント内の配列)      ← BFF を通らない
L4 live… ブラウザ ── 実 Azure (相談 1 往復 + TTS のみ) ← UC-02〜05 を通らない
```

つまり **`src/api/*.ts` の real 分岐 → tRPC → BFF router → Problem リポジトリ** という
実際の配線は、UC-02 (見返す) / UC-03 (繰り返しに気づく) / UC-04 (棚卸し) / UC-05 (次の一歩)
について**どのテストも一度も通していなかった**。ADR 0004 で「mockApi をフロントの真実」に
した判断は UI 開発の速度としては正しかったが、その裏返しとして「mock が緑なら real も緑だろう」
という検証されない前提が積み上がっていた。

(a) は (b) があったから見つからなかった。同じ穴は塞がない限りまた埋まる。

## Decision

**mock を通らない経路でユースケースを通す受け入れテスト層 (L3-real) を追加する。**

```
ブラウザ ── 実フロント (VITE_USE_MOCK なし) ── 実 BFF (tRPC / SSE) ── ai-agent ダブル
```

差し替えるのは **LLM の判断だけ**。フロントの api 層・tRPC・BFF の router・
materializeExtraction・Problem リポジトリはすべて本物が動く。

構成要素:

| 置き場所                                 | 役割                                                                       |
| ---------------------------------------- | -------------------------------------------------------------------------- |
| `apps/bff/src/http/handlers.ts`          | HTTP ハンドラ本体を Web 標準 Request/Response で 1 箇所に集約              |
| `apps/bff/src/functions/*.ts`            | Azure Functions 入口 (型変換のみ)                                          |
| `apps/bff/scripts/local-server.mjs`      | 素の node で同じ handlers を配信 (Functions Core Tools 不要)               |
| `apps/frontend/e2e-uc/fake-ai-agent.mjs` | 決定的な ai-agent ダブル (キーワードでグルーピング)                        |
| `apps/frontend/playwright.uc.config.ts`  | 3 サーバを起動して UC を通す Playwright プロジェクト                       |
| `apps/frontend/e2e-uc/uc0*.spec.ts`      | UC-01〜05 + 失敗系 (`use_cases.md` の基本フロー / 代替フローに 1:1 で対応) |

**BFF の HTTP 入口を共通化するのが前提条件**だった。入口が Functions と local-server の
2 つになるので、status やヘッダを入口ごとに書くと必ずズレる (実際 TTS の 202/200 で
1 回踏んでいる)。ルーティングと status の決定は `handlers.ts` に集約し、入口側は変換だけ持つ。

**ai-agent は BFF の組み込み stub ではなくダブルを立てる。** BFF の stub は毎回同じ
「[stub] 困りごと」を新規で返すだけで**既存 Problem への寄せ (段2) が起きず**、
UC-03 / UC-04 が原理的に踏めないため。

### 誰がこの層のシナリオを書くか (未決 — PO の裁定が要る)

[ADR 0019](0019-independent-judge-agents-security-qa-release.md) と `docs/testing/strategy.md` は
**L3 (mock) を qa-reviewer の所有**とし、「実装者は L3 を増やさない」と定めている。
L3-real は UC の受け入れを見る点で L3 と同種なのに、**初版 25 本は実装者が単独で書いた**。

提案する分界は「**doc の写経 = 実装者 / 基準の創出 = qa-reviewer**」:

- L3-real が固定するのは `use_cases.md` に**既に書いてあるフロー**であり、「何が良い体験か」を
  新しく判断していない。doc と spec の乖離を防ぐ責任は doc を変える人にある。ここを judge に
  外注すると doc 変更と spec 変更が別 PR に割れ、**乖離そのものを生む**
- 「この UC は本当にこうあるべきか」「体験として十分か」の判断は引き続き qa-reviewer

**ただしこれは提案であって決定ではない。** 初版が実装者単独になったのは実環境の無音失敗を
止める緊急対応という事情もあり、独立 judge 原則を緩める前例にしたくない。
**この ADR を Accept する際に、PO がこの所有の置き方も併せて裁定すること。**

### 併せて決めたこと: 再燃は自動で `open` に戻す

`use_cases.md` UC-03 の事後条件は「既存 Problem が再点火 (→ `open`) し、再出現が記録される」
だが、BFF は「状態遷移は事後トリアージに委ねる」として**再点火していなかった**。
結果、抽出結果レビューは「🔁2回目 / 再燃」と表示するのに、一覧の既定 (追跡中のみ) には
現れない。**「また話していることに気づかせる」というプロダクトの芯が黙って死んでいた。**

ADR 0007 の方針 (自動で寄せて気づきを返し、違えば事後トリアージで直す) を状態にも適用し、
`resolved` / `shelved` に再言及があったら `open` に戻す。誤検知なら詳細から「解決した」で戻せる。

**これは [ADR 0007](0007-problem-centric-two-layer-domain-model.md) の「状態遷移は user の
編集権 (トリアージ) に残す」という境界を、自動化の側へ動かす変更である。** 根拠は
「ADR 0007 が *グルーピング* について選んだ論理 (自動で寄せる方が、承認の摩擦で蓄積体験が
続かなくなるより良い) は、*status* にもそのまま効く」という一点に尽きる。

**そしてこの判断は一度否定されている。** [debrief #1 (2026-08-06)](../debrief/journal.md) で
PO は「再出現したら status も自動で open に戻る」という直感を述べ、エージェント側が
「自動化したのはグルーピングのみ。状態遷移は user の編集権に残す (ADR 0007)」と説明して
**PO の理解を訂正した**経緯がある。今回はその訂正を撤回して PO の当初の直感を採る形になる。

⚠️ **Accept 時に PO はここを明示的に裁定すること。** 裁定事項は 1 つ —
**「解決 / 棚上げした困りごとを再度話したとき、自動で追跡に戻ってよいか」**。
戻さない方を選ぶなら、代わりに「`resolved` / `shelved` の Problem には『再燃』と
表示せず、トリアージの提案として見せる」形に作り直す (表示と一覧が食い違う状態は
どちらの設計でもバグなので、放置はしない)。

## Consequences

### 良くなること

- UC-01〜05 の基本フロー・主要な代替フローが**機械的に検証される** (25 ケース / 約 46 秒)
- 「押しても何も起きない」クラスの退行が CI で落ちる (BFF を落として操作する失敗系テスト)
- `func start` (Azure Functions Core Tools) 無しでローカルから実 BFF を叩けるようになる (#64 に前進)
- BFF の HTTP 入口が 1 箇所になり、Functions とローカルで挙動がズレなくなる

### 代償・限界

- **層が 1 つ増える**。L3 mock (9 件) と L3-real (25 件) の役割分担を守らないと二重メンテになる。
  分担は「mock = バンドル/認証ゲート/UI 仕様の確認」「real = ユースケースの受け入れ」。
  UI 仕様の網羅は引き続き MDX が真実で、E2E では追わない
- ai-agent ダブルは**語彙一致**であって意味類似ではない。実 LLM のグルーピング精度は
  この層では測れない (L4 / ux-probe の担当)
- **永続化は検証できない** (#165)。この層は `COSMOS_ENDPOINT` を渡さずに走らせるため
  リポジトリは in-memory の singleton に落ちる。「Functions がリサイクルしても困りごとが
  残るか」は別途 L4 で見る必要がある。なお永続化の実装自体は
  [ADR 0030](0030-persistence-on-cosmos-db-single-store-behind-bff.md) として着地済み (#182)
  なので、**この層の in-memory 前提 (spec の直列実行・題材トピック分割) は
  Cosmos を差した構成で回せるかを含めて見直す余地がある**
- Problem リポジトリがプロセス共有のため spec は直列実行 + 題材トピックを spec ごとに分ける
  必要がある (並列化できない)。永続化が入ってユーザー単位に分離できたら見直す

### 却下した代替案

- **mock E2E を厚くする** — mockApi は BFF を通らないので、今回の穴 (real 配線) は原理的に埋まらない
- **L4 live を厚くする** — 実 Azure に依存するので遅く不安定で、PR ゲートに置けない。
  コールドスタート込みで数分かかり、下流の一時障害で赤くなる
- **Azure Functions Core Tools をローカル/CI に入れる** — 起動が重く CI 時間を食う。
  検証したいのは「Azure が動くか」ではなく「ユースケースが通るか」なので、
  同じ handlers を素の node で配信すれば足りる
