"""
OrganizedResult を ActionPlan（タイトル・ステップ）に変換する。

workflow.py の FSM を経由せず、単発の structured LLM 呼び出しで完結する。
"""
from __future__ import annotations

import json
import logging

from semantic_kernel import Kernel
from semantic_kernel.contents import ChatHistory

from .kernel import get_execution_settings
from .schemas import PlanRequest, PlanResponse

logger = logging.getLogger(__name__)

_PLAN_PROMPT = """\
以下の状況分析をもとに、具体的な行動プランを JSON 形式のみで作成してください。
マークダウン記法は使わないでください。

状況要約: {summary}
主な感情: {emotions}
優先事項: {priorities}

回答形式:
{{
  "title": "プランのタイトル（例: 48時間アクションプラン）",
  "steps": ["ステップ1", "ステップ2", "ステップ3"]
}}

ステップは 3〜5 件、具体的かつ実行可能な内容にしてください。
"""


async def generate_plan(
    req: PlanRequest,
    kernel: Kernel,
) -> PlanResponse:
    prompt = _PLAN_PROMPT.format(
        summary=req.summary,
        emotions="、".join(req.emotions),
        priorities="、".join(req.priorities),
    )

    call_history = ChatHistory()
    call_history.add_user_message(prompt)

    svc = kernel.get_service("chat")
    result = await svc.get_chat_message_content(
        chat_history=call_history, settings=get_execution_settings()
    )

    raw = str(result).strip()
    parts = raw.split("```")
    if len(parts) >= 3:
        raw = parts[1].removeprefix("json").strip()

    try:
        data = json.loads(raw)
        return PlanResponse(
            title=data.get("title", "アクションプラン"),
            steps=data.get("steps", []),
        )
    except json.JSONDecodeError:
        logger.warning("Plan JSON parse failed: %r", raw)
        return PlanResponse(
            title="アクションプラン",
            steps=["具体的なステップを考えてみましょう"],
        )
