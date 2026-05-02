import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field


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
