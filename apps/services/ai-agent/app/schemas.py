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


class ApproveRequest(BaseModel):
    approval_request_id: str
    approved: bool


class ApproveResponse(BaseModel):
    reply: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


# ── Internal workflow schemas ─────────────────────────────────────────────────


class Plan(BaseModel):
    needs_retrieval: bool = False
    tool_name: Optional[str] = None
    tool_args: dict = {}
    is_side_effecting: bool = False


class ApprovalRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    plan: Plan
    rag_context: str = ""
    status: Literal["pending", "approved", "rejected"] = "pending"


# ── Organize / Plan schemas ───────────────────────────────────────────────────


class OrganizeRequest(BaseModel):
    session_id: str = Field(..., description="Session identifier")


class OrganizeResponse(BaseModel):
    summary: str
    emotions: list[str] = []
    priorities: list[str] = []


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


class ExtractRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId", description="Session identifier")
    existing_problems: list[ExistingProblemRef] = Field(
        default_factory=list, alias="existingProblems"
    )
