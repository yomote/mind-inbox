"""
セッション全文 (Dump) を Mention[] に抽出し、既存 Problem へのグルーピングまで行う。

Problem 中心 2層モデル (ADR 0007) の核。organizer.py の /organize を置換する新エンドポイント
/extract の本体。organizer と同じく workflow.py の FSM を経由せず、単発の structured LLM
呼び出しで完結する。

処理:
  A1 抽出   — Dump から独立した困りごと (Mention) を切り出す (statement/excerpt/affect)
  A3 テーマ — 各 Mention に固定7分類 + 未分類の主テーマ + 自由タグを付ける
  A2 グルーピング — 既存 Problem 候補と突き合わせ、寄せる (existing) か新規 (new) かを LLM が判定

v1 は LLM 単発判定 (embedding は Phase 2)。既存 Problem 候補は BFF (Problem リポジトリ) が
ExtractRequest.existing_problems で渡す。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from semantic_kernel import Kernel
from semantic_kernel.contents import ChatHistory

from .kernel import get_execution_settings
from .repositories import SessionRepository
from .schemas import (
    THEMES,
    Affect,
    ExistingProblemRef,
    ExtractedItem,
    ExtractionResult,
    GroupingOutcome,
    Mention,
)

logger = logging.getLogger(__name__)

_THEME_LIST = "、".join(THEMES)

_EXTRACT_PROMPT = """\
以下の会話 (1回の吐き出し) を分析し、独立した「困りごと」を抽出してください。
困りごとの粒度は「独立して再発しうるか / 独立して解決しうるか」で判断します
(例: 「転職の不安」と「睡眠不足」は別の困りごと)。JSON 形式のみで回答し、
マークダウン記法は使わないでください。

会話:
{conversation}

既存の困りごと一覧 (この中に該当すれば existingProblemId にその id を入れる。
無ければ null で新規とする):
{existing_problems}

テーマは次のいずれか1つを選ぶ: {theme_list}

回答形式:
{{
  "mentions": [
    {{
      "statement": "困りごとを一文で言い換えた要約",
      "excerpt": "根拠となるユーザー発話の短い引用",
      "affect": {{"label": "感情ラベル", "valence": "negative|neutral|positive", "intensity": 0.0〜1.0}},
      "theme": "上のテーマから1つ",
      "tags": ["自由タグ1", "自由タグ2"],
      "grouping": {{
        "existingProblemId": "既存 id または null",
        "newProblemTitle": "新規時の短いタイトル (既存に寄せる時は空でよい)",
        "confidence": 0.0〜1.0
      }}
    }}
  ]
}}

困りごとが無ければ mentions は空配列にしてください。
"""


def _format_history(history: ChatHistory) -> str:
    """ChatHistory をテキストに整形する。system メッセージは除外する。"""
    lines = []
    for msg in history.messages:
        role_name = getattr(msg.role, "value", str(msg.role)).lower()
        if "system" in role_name:
            continue
        label = "ユーザー" if "user" in role_name else "AI"
        lines.append(f"{label}: {msg.content}")
    return "\n".join(lines)


def _format_existing(problems: list[ExistingProblemRef]) -> str:
    if not problems:
        return "(なし)"
    lines = []
    for p in problems:
        lines.append(
            f"- id={p.id} / {p.title} [テーマ: {p.theme}] {p.summary}".rstrip()
        )
    return "\n".join(lines)


def _coerce_theme(value: object) -> str:
    """LLM が返したテーマを固定分類に丸める。未知なら「未分類」。"""
    return value if value in THEMES else "未分類"


def _coerce_affect(raw: object) -> Affect:
    """LLM の affect を安全に Affect へ。壊れていても neutral で埋める。"""
    data = raw if isinstance(raw, dict) else {}
    valence = data.get("valence")
    if valence not in ("negative", "neutral", "positive"):
        valence = "neutral"
    try:
        intensity = float(data.get("intensity", 0.0))
    except (TypeError, ValueError):
        intensity = 0.0
    intensity = min(1.0, max(0.0, intensity))
    return Affect(
        label=str(data.get("label", "")), valence=valence, intensity=intensity
    )


def _clamp_confidence(value: object) -> float | None:
    if value is None:
        return None
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return None


async def extract(
    session_id: str,
    existing_problems: list[ExistingProblemRef],
    session_repo: SessionRepository,
    kernel: Kernel,
) -> ExtractionResult:
    history = await session_repo.get(session_id)
    if history is None:
        raise ValueError(f"Session not found: {session_id!r}")

    conversation = _format_history(history)
    prompt = _EXTRACT_PROMPT.format(
        conversation=conversation,
        existing_problems=_format_existing(existing_problems),
        theme_list=_THEME_LIST,
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
    except json.JSONDecodeError:
        logger.warning("Extract JSON parse failed: %r", raw)
        return ExtractionResult(
            session_id=session_id,
            items=[],
            new_problem_count=0,
            updated_problem_count=0,
        )

    known = {p.id: p for p in existing_problems}
    now = datetime.now(timezone.utc).isoformat()
    items: list[ExtractedItem] = []
    # problem_id -> このバッチ反映後の mention_count。同一 Dump 内で同じ既存 Problem に
    # 複数 Mention が寄る場合に累積させる (ref は共有オブジェクトで更新されないため)。
    running_count: dict[str, int] = {}
    updated_ids: set[str] = set()
    new_count = 0

    for m in data.get("mentions", []):
        if not isinstance(m, dict):
            continue
        theme = _coerce_theme(m.get("theme"))
        grouping_raw = m.get("grouping") if isinstance(m.get("grouping"), dict) else {}
        confidence = _clamp_confidence(grouping_raw.get("confidence"))

        existing_id = grouping_raw.get("existingProblemId")
        ref = known.get(existing_id) if existing_id else None

        if ref is not None:
            # 既存 Problem への再出現。同一バッチ内の重複ヒットを累積する。
            problem_id = ref.id
            running_count[problem_id] = (
                running_count.get(problem_id, ref.mention_count) + 1
            )
            outcome = GroupingOutcome(
                kind="existing",
                problem_id=problem_id,
                problem_title=ref.title,
                problem_theme=ref.theme,
                is_recurrence=True,
                mention_count=running_count[problem_id],
                reignited=ref.status != "open",
                grouping_confidence=confidence,
            )
            updated_ids.add(problem_id)
        else:
            # 新規 Problem を起こす
            problem_id = f"prob-{uuid.uuid4()}"
            running_count[problem_id] = 1
            title = grouping_raw.get("newProblemTitle") or m.get("statement", "")
            outcome = GroupingOutcome(
                kind="new",
                problem_id=problem_id,
                problem_title=str(title),
                problem_theme=theme,
                is_recurrence=False,
                mention_count=1,
                reignited=False,
                grouping_confidence=confidence,
            )
            new_count += 1

        mention = Mention(
            id=f"men-{uuid.uuid4()}",
            session_id=session_id,
            dump_id=session_id,  # v1: 1 セッション = 1 Dump
            created_at=now,
            statement=str(m.get("statement", "")),
            excerpt=str(m.get("excerpt", "")),
            affect=_coerce_affect(m.get("affect")),
            proposed_theme=theme,
            proposed_tags=[str(t) for t in m.get("tags", []) if t],
            problem_id=problem_id,
            grouping_confidence=confidence,
        )
        items.append(ExtractedItem(mention=mention, grouping=outcome))

    return ExtractionResult(
        session_id=session_id,
        items=items,
        new_problem_count=new_count,
        updated_problem_count=len(updated_ids),
    )
