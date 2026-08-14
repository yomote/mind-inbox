import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── API schemas ───────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Session identifier")
    message: str = Field(..., description="User message")


class ChatResponse(BaseModel):
    reply: str
    requires_approval: bool = False
    approval_request_id: Optional[str] = None
    citations: list[str] = []


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


class ExtractionResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    items: list[ExtractedItem] = []
    new_problem_count: int = Field(alias="newProblemCount", ge=0)
    updated_problem_count: int = Field(alias="updatedProblemCount", ge=0)


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
