import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── API schemas ───────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Session identifier")
    message: str = Field(..., description="User message")


class ChatResponse(BaseModel):
    """1 ターン分の応答。/chat と /chat/stream の done イベントが同じ形を運ぶ。

    `choices` は `offer_choices` ツール (#432-b) が提示した選択肢。**空 = 選択肢なし**
    で、これが既定 (`LLM_EXPOSED_TOOLS` が空ならツール自体が LLM に見えない)。

    **`requires_approval` に相乗りさせていないのが要点**: 承認カードの文言は
    「承認するまで、この操作は行われません」で、選択肢に付くと嘘になる。選択肢は
    「会話の分岐」であって「実行の可否」ではないので、承認とは別のフィールド・
    別の UI にする (#432 design-gate)。承認要求のターンでは choices は空のまま
    (workflow の `_record_approval_request` は choices を積まない)。
    """

    reply: str
    requires_approval: bool = False
    approval_request_id: Optional[str] = None
    citations: list[str] = []
    choices: list[str] = []


class ChatStreamDelta(BaseModel):
    """/chat/stream の SSE イベント: 逐次トークン (#120)。"""

    type: Literal["delta"] = "delta"
    text: str


class ChatStreamDone(BaseModel):
    """/chat/stream の SSE イベント: 完了。従来 /chat と同一の ChatResponse を運ぶ。"""

    type: Literal["done"] = "done"
    response: ChatResponse


class ChatStreamError(BaseModel):
    """/chat/stream の SSE イベント: 途中失敗。クライアントは非ストリーミングへフォールバックする。"""

    type: Literal["error"] = "error"
    message: str


class ApproveRequest(BaseModel):
    approval_request_id: str
    approved: bool


class ApproveResponse(BaseModel):
    reply: str


class ApprovalConflictResponse(BaseModel):
    """`/approve` の **409**: その承認 ID はもう解決済み (#82 / PO 裁定 2026-08-15 B 案)。

    404 (レコードが無い) と分ける理由: 混ぜていた頃は「レコードが消えた」のか
    「もう解決済み」なのかが応答から読めず、**確実に未実行と言える却下済みまで
    「実行されたか不明」**に落ちていた。409 + 現在状態にすると、クライアントは
    「すでに却下済み (= 実行されていない)」を言い切れ、承認済みは実行の有無を
    断定しない案内に分けられる。

    **`status` は「どちらの決定を受け付けたか」であって実行の完了ではない**
    (PR #430 Codex P1)。遷移は checkpoint 再開の前に書くので、`approved` の記録後・
    副作用の実行前に落ちれば「approved なのに未実行」のレコードが残る。
    `approved` から「実行された」を導いてはいけない。

    `detail` は FastAPI の慣行に合わせた人間可読の 1 行で、**機械判別は `status` で行う**
    (BFF は zod でこの body を検証してから専用エラーに写す)。プロキシや別レイヤが返す
    汎用 409 はこの形にならないので、承認と無関係な 409 を「処理済み」に化けさせない。
    """

    detail: str
    status: Literal["approved", "rejected"]
    # **決定を受け付けた**時刻 (ISO 8601 / UTC)。実行の完了時刻ではない (上記)。
    # **null がありうる**: この項目より前に書かれた Cosmos の `ApprovalRecord` には
    # 時刻が無い。「時刻が取れなかった」を「未処理」と混同させないため、status とは
    # 独立の nullable にしてある。
    processed_at: Optional[str] = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


# ── Internal workflow schemas ─────────────────────────────────────────────────


class Plan(BaseModel):
    """承認 UI に見せる「これから実行しようとしているツール呼び出し」。

    中身は LLM が function calling で生成した呼び出しをそのまま写したもの
    (#320 以降、自前の分類プロンプトは無い)。
    """

    tool_name: Optional[str] = None
    tool_args: dict = {}
    is_side_effecting: bool = False


class ApprovalRecord(BaseModel):
    """承認レコード (Cosmos の永続モデル / `CosmosApprovalRepository`)。

    **`extra="forbid"` にしてはいけない。** Cosmos の read_item は文書に
    システムプロパティ (`_rid` / `_self` / `_etag` / `_ts` / `_attachments`) を
    載せて返すので、forbid にすると `model_validate(doc)` が**既存文書の読み込みを
    すべて弾く** (実測 5 件の extra エラー)。廃止フィールドが残った古い文書も
    同じ理由で読めなくなる。余剰フィールドを黙って捨てる代わりに払っている代償が
    これで、**廃止フィールドを渡すテストの残骸は型では止まらない** (judge #417)。
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    plan: Plan
    status: Literal["pending", "approved", "rejected"] = "pending"
    # status を pending から動かした時刻 (ISO 8601 / UTC)。`/approve` の 409 応答が
    # 「いつ受け付けたか」を運ぶために持つ (#82)。**実行の完了時刻ではない** —
    # 遷移は checkpoint 再開の前に書かれる (PR #430 Codex P1)。**None がありうる** —
    # この項目より前に書かれた Cosmos 文書には無い (`extra="forbid"` にしていないのと
    # 同じ理由で、既存文書を読めなくしない)。**status の代わりに使ってはいけない**:
    # 時刻が無いことは「未処理」を意味しない。
    processed_at: Optional[str] = None
    # approvalRequestId (= id) → MAF checkpoint への写像 (ADR 0016 M1-3)。
    # /approve はこの checkpoint から workflow を再開する。
    checkpoint_id: Optional[str] = None


# ── Plan schemas ──────────────────────────────────────────────────────────────


class PlanRequest(BaseModel):
    summary: str
    emotions: list[str] = []
    priorities: list[str] = []


class PlanResponse(BaseModel):
    title: str
    steps: list[str] = []


# ── Extract schemas (Problem 中心 2層モデル / ADR 0007) ────────────────────────
#
# apps/bff/src/trpc/domain.ts の zod 型を鏡写しにする。
# JSON 表現は camelCase (alias) で domain.ts と完全一致させ、Python 側は snake_case
# で扱う (populate_by_name=True で両表記から構築可)。FastAPI は response_model を
# 既定で by_alias=True 直列化するため、/extract の出力は domain.ts の型と一致する。

# domain.ts THEMES と同一 (固定7分類 + 未分類)
THEMES = (
    "仕事・キャリア",
    "お金",
    "心と体",
    "家族・パートナー",
    "人間関係",
    "自己理解・生き方",
    "日常・生活",
    "未分類",
)

Theme = Literal[
    "仕事・キャリア",
    "お金",
    "心と体",
    "家族・パートナー",
    "人間関係",
    "自己理解・生き方",
    "日常・生活",
    "未分類",
]

AffectValence = Literal["negative", "neutral", "positive"]
ProblemStatus = Literal["open", "resolved", "shelved"]


class Affect(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    label: str
    valence: AffectValence
    intensity: float = Field(ge=0, le=1)


class Mention(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    session_id: str = Field(alias="sessionId")
    dump_id: Optional[str] = Field(alias="dumpId")
    created_at: str = Field(alias="createdAt")
    statement: str
    excerpt: str
    affect: Affect
    proposed_theme: Theme = Field(alias="proposedTheme")
    proposed_tags: list[str] = Field(alias="proposedTags")
    problem_id: Optional[str] = Field(alias="problemId")
    grouping_confidence: Optional[float] = Field(alias="groupingConfidence", ge=0, le=1)


class GroupingOutcome(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["new", "existing"]
    problem_id: str = Field(alias="problemId")
    problem_title: str = Field(alias="problemTitle")
    problem_theme: Theme = Field(alias="problemTheme")
    is_recurrence: bool = Field(alias="isRecurrence")
    mention_count: int = Field(alias="mentionCount", ge=1)
    reignited: bool
    grouping_confidence: Optional[float] = Field(alias="groupingConfidence", ge=0, le=1)


class ExtractedItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mention: Mention
    grouping: GroupingOutcome


# ── 思考整理マップ (Issue #433 段階 1 / 2026-08-15 PO 裁定) ────────────────────
#
# 「AI が今この会話をどう整理しているか」を右ペインに出すためのデータ。
# **preview (/extract) の出力に相乗りする** — 追加の LLM 呼び出しは 0 (裁定 2)。
#
# ここに **% や進捗率のフィールドを足さないこと**。整理度合いは「ノードを数えた実数」
# だけで表す (裁定 3)。分母 (= 悩みが出きった状態) を誰も測っていないので、率を出すと
# 測っていない数字を画面に出すことになる。契約に無ければ画面にも作れない。

# それが何か: 話題 / AI の仮説 / まだ聞けていない穴
ThinkingNodeKind = Literal["topic", "hypothesis", "unknown"]
# どれだけ確かか: ユーザーが認めた / AI がそう思っているだけ / まだ触れていない
ThinkingNodeStatus = Literal["confirmed", "tentative", "unexplored"]


class ThinkingNode(BaseModel):
    """整理マップの 1 ノード。kind (何か) × status (どれだけ確か) の 2 軸 (裁定 4)。

    2 軸に分けているのは「AI の仮説をユーザーが認めた」(hypothesis × confirmed) を
    表せるようにするため — #432 の仮説提示が当たったかどうかが地図の上で見える。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    kind: ThinkingNodeKind
    label: str
    status: ThinkingNodeStatus
    # 箇条書きツリーの親 (段階 1 の構造)。null = 根。
    # グラフの辺 (causes / relates など) は段階 2 で足す — 段階 1 で描かないものを
    # 契約に置くと、誰も検証していないフィールドが増えるだけになる。
    parent_id: Optional[str] = Field(default=None, alias="parentId")
    # #321 (題材再定義) との接続穴。**段階 1 では常に null で返す** — LLM が推測した
    # Problem id を載せると「AI が既存の悩みと紐づけた」と誤読されるため、extractor が
    # 必ず落とす。フィールドだけ空けておけば #321 の後に契約を壊さず繋がる (裁定 4)。
    problem_id: Optional[str] = Field(default=None, alias="problemId")


class ThinkingMap(BaseModel):
    """1 セッション分の整理マップ。揮発する (ADR 0039 D1 — preview は何も保存しない)。"""

    model_config = ConfigDict(populate_by_name=True)

    nodes: list[ThinkingNode] = []


class ExtractionResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    items: list[ExtractedItem] = []
    new_problem_count: int = Field(alias="newProblemCount", ge=0)
    updated_problem_count: int = Field(alias="updatedProblemCount", ge=0)
    # 整理マップ (#433)。null = LLM が返さなかった / 使える節が 1 つも無かった。
    # 「マップが無い」と「空のマップ」を画面で区別しないので同じ null に潰す。
    thinking_map: Optional[ThinkingMap] = Field(default=None, alias="thinkingMap")


class ExistingProblemRef(BaseModel):
    """グルーピングの突き合わせ候補。BFF (Problem リポジトリ) が /extract に渡す。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    theme: Theme
    summary: str = ""
    mention_count: int = Field(default=1, alias="mentionCount", ge=0)
    status: ProblemStatus = "open"


class ConversationMessage(BaseModel):
    """抽出対象の会話 1 発話 (#183)。BFF 経由でフロントの会話全文が渡ってくる。"""

    model_config = ConfigDict(populate_by_name=True)

    role: Literal["user", "assistant"]
    text: str


class ExtractRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId", description="Session identifier")
    existing_problems: list[ExistingProblemRef] = Field(
        default_factory=list, alias="existingProblems"
    )
    # 呼び出し側が会話を持っているなら、それを使う (#183)。
    # このサービスのセッション履歴はプロセスメモリなので、scale-to-zero やスケールアウトで
    # 消える / 別レプリカに当たると 404 になっていた。渡されていれば履歴に依存しない。
    messages: list[ConversationMessage] = Field(default_factory=list)
