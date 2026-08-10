# 0034. UC に無い会話中心モデルの残骸 (整理結果 / 行動プラン / 履歴) を撤去する

- Status: Proposed
- Date: 2026-08-10
- Deciders: yomote (PO / 2026-08-10 に撤去を選択) / 実装セッション
- Related: [ADR 0007](0007-problem-centric-two-layer-domain-model.md) (Problem 中心 2層) / [ADR 0005](0005-mdx-ui-spec-as-truth.md) (MDX が UI の真実) / [ADR 0030](0030-persistence-on-cosmos-db-single-store-behind-bff.md) (永続化) / [ADR 0032](0032-use-case-acceptance-tests-against-real-wiring.md) (UC 受け入れテスト) / #183 (抽出の 404 根治)

## Context

2026-08-10、PO が dev の SWA を触って「**エクストラクトとオーガナイズがちゃんと機能していない。
ゴールデンパスが通っていない**」と報告した。

調べると、**壊れていたのは「UC にも FR にも存在しない画面」だった**。

### 事実 1: `/organize` は #183 の修正から取り残されていた

`#183` / `#191` は「ai-agent が会話をプロセスメモリから引くため 404 になる」問題を、
**呼び出し側が会話全文 (`messages`) を渡す**ことで根治した。しかしこれは `/extract` にだけ
適用され、`/organize` は手つかずだった。

```python
class OrganizeRequest(BaseModel):
    session_id: str          # ← 会話を受け取らない
# ExtractRequest には messages がある

history = await session_repo.get(session_id)
if history is None:
    raise ValueError(...)    # → 404

class InMemorySessionRepository:
    """TODO(PoC): 再起動でセッションが消える。本番では Redis に差し替える。"""
```

ai-agent は Container Apps の scale-to-zero なので、会話は普通に消える。
**つまり `整理結果へ` は実環境で常時 404 になる導線だった。**

### 事実 2: そもそも UC にも FR にも対応が無い

| 画面                      | 対応する UC        | 対応する FR                                 |
| ------------------------- | ------------------ | ------------------------------------------- |
| 整理結果 (`ResultScreen`) | **無し**           | **無し**                                    |
| 行動プラン (整理結果起点) | UC-05 の**別実装** | FR-9 は「**Problem を選んで**」と書いている |
| 履歴・振り返り            | **無し**           | **無し**                                    |

`use_cases.md` を grep すると「整理結果」「organize」は **0 件**。
これらは PoC 期の**会話中心モデル (集約ルート = Session)** の残骸で、
[ADR 0007](0007-problem-centric-two-layer-domain-model.md) が Problem 中心モデルへ移した時に
UI だけ取り残されたもの。コード側も自覚していた —
`SessionControls.tsx` には「**`整理結果へ`(旧 PoC) は段階廃止で secondary(text) に降格**」と
書いてあった。**廃止すると決めた導線が、廃止されないまま壊れて表に出ていた。**

### 事実 3: 「段階廃止」は 3 か月動かなかった

降格させて併存させる判断は、移行期としては妥当だった。しかし実際には

- 併存したまま誰も撤去せず、**下流だけが腐った** (`/organize` が 404 化)
- `#183` の修正時に「使われていない方」として素通しされた
- PO が最初に触ったとき、**主導線ではなく腐った方を踏んだ**

「いつか消す」は期限が無いと消えない。

## Decision

**UC / FR に対応の無い会話中心モデルの残骸を、UI から下流まで一括で撤去する。**

| 層              | 撤去するもの                                                                                                                                                                                                     |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| フロント        | `ResultScreen` / `ActionPlanScreen` / `HistoryScreen`、`result` / `actionPlan` / `history` ルート、「整理結果へ」「履歴・振り返り」導線、`organizeResult` / `createActionPlan` / `loadHistories` / `saveHistory` |
| MDX (UI の真実) | `result.mdx` / `action-plan.mdx` / `history.mdx`                                                                                                                                                                 |
| BFF             | `consultation.organize` / `consultation.createPlan` / `history` router、`historyRepository` (in-memory / Cosmos)、`OrganizedResult` / `HistoryItem` スキーマ                                                     |
| ai-agent        | `POST /organize` / `organizer.py` / `OrganizeRequest` / `OrganizeResponse`                                                                                                                                       |

**残すもの**: `ActionPlan` 型と ai-agent の `/plan` — UC-05 の「次の一歩」は
**Problem を起点に**生き続ける (`problem.createPlan`)。撤去したのは _整理結果を起点にする方_ だけ。

セッションからの出口は **「困りごとを抽出」1 本**になる。

### Cosmos の `history` コンテナは残す

bicep (`cicd/modules/bootstrap-core.bicep`) の `history` コンテナは**消さない**。
削除は次のデプロイでデータごと消える**不可逆な操作**であり、エージェントが独断で
決めてよい範囲を越える。アプリからの参照だけ切り、器は空のまま残す
(空のコンテナは Cosmos serverless では実質無課金)。**器の削除は PO 判断**。

### 永続化プローブの付け替え

`persistence-probe.sh` (#182) は「AI を呼ばない書き込み経路」として `history.save` を
使っていた。history の撤去でその経路が消えるため、**Problem のタイトルにマーカーを置く**
形へ付け替えた。定常状態 (マーカーが既にある) では `problem.list` の読み取りだけで
判定が終わり、**マーカーが 1 件も無いときだけ** `consultation.extract` を 1 回使って種を置く。
日々のコストは従来どおりゼロのまま。

## Consequences

### 良くなること

- **画面から「押すと壊れるもの」が消える。** ゴールデンパスが 1 本になり、迷子にならない
- `use_cases.md` / `requirements.md` と UI が 1:1 対応する。**doc と画面の突き合わせが目視で終わる**
- 撤去により BFF の tRPC 手続きが 3 本、ai-agent のエンドポイントが 1 本減る。
  `#183` のような横断修正で「取り残される支流」が無くなる
- L3-real に**ゴールデンパス 1 本通し**の spec を足した (`golden-path.spec.ts`)。
  UC 単位では緑でも継ぎ目が切れている状態を止める

### 代償・限界

- **相談セッションの記録が残らなくなる。** 従来も「整理結果 + プラン」の形でしか
  残っていなかった (会話全文ではない) が、その保存先すら無くなる。
  もし「対話そのものを後から読み返したい」なら、**Problem の Mention (日時 + 引用) とは
  別の要求**として改めて UC を起こす必要がある
- **Cosmos の `history` コンテナが宙に浮く。** 器だけ残り、アプリは参照しない。
  削除するかは PO 判断 (上記)
- 既に `history` コンテナに入っているデータ (永続化プローブのマーカー数件) は読めなくなる。
  実ユーザーデータは無い

### 却下した代替案

- **`/organize` にも `messages` を渡して延命する** — 壊れは直るが、UC にも FR にも無い機能を
  維持し続けることになる。「段階廃止」の 3 か月が示したとおり、併存させると腐る側が生まれる
- **UC-06「セッションを整理して履歴に残す」として昇格させる** — 正式な要求なら妥当だが、
  concept_deck も requirements も「**継続的に育つ構造化体験**」を競争軸に置いていて、
  1 回の会話を要約して保存する機能はその軸に乗らない。PO も撤去を選択した
- **UI からだけ隠して API は残す** — 一番小さい変更だが、`#183` で起きた「使われていない方が
  取り残されて腐る」を再生産する。腐るものを残さないのがこの ADR の主眼
