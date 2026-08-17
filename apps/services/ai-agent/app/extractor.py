"""
セッション全文 (Dump) を Mention[] に抽出し、既存 Problem へのグルーピングまで行う。

Problem 中心 2層モデル (ADR 0007) の核であり、セッションからの唯一の抽出経路。
/extract の本体。workflow.py の FSM を経由せず、単発の structured LLM
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

from agent_framework import BaseChatClient

from .agents import complete
from .history import ChatHistory
from .observability import client_detail, fingerprint, new_ref
from .repositories import SessionRepository
from .schemas import (
    THEMES,
    Affect,
    ConversationMessage,
    ExistingProblemRef,
    ExtractedItem,
    ExtractionResult,
    GroupingOutcome,
    Mention,
    Theme,
    ThinkingMap,
    ThinkingNode,
)

logger = logging.getLogger(__name__)


class ExtractionUnavailable(Exception):
    """抽出の材料 (会話) が手に入らなかった。呼び出し側は 404 で返す。"""


class ExtractionParseError(Exception):
    """LLM の応答を JSON として解釈できなかった (#183)。

    **「困りごとが 0 件だった」と混同しないための独立した失敗**。以前はここで warning を
    出して空の結果を返しており、画面には「追跡する困りごとは見つかりませんでした」と
    表示されていた — 壊れているのに正常終了に見える、最も気づけない壊れ方だった。
    """


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

あわせて、**あなたがこの会話を今どう整理しているか**を箇条書きツリーの形で出してください
(thinkingMap)。これは利用者の画面に「AI の整理」として出ます。
- kind: topic (会話に出ている話題) / hypothesis (あなたの見立て・まだ本人が認めていない) /
  unknown (まだ聞けていない・確かめたい穴)
- status: confirmed (本人の発話で確かめられた) / tentative (あなたがそう思っているだけ) /
  unexplored (まだ触れていない)
- parentId: その節が何の下にぶら下がるか (根なら null)
- label は 20 字以内。節は多くても 12 個までにしてください。

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
  ],
  "thinkingMap": {{
    "nodes": [
      {{
        "id": "n1",
        "kind": "topic|hypothesis|unknown",
        "label": "短い見出し",
        "status": "confirmed|tentative|unexplored",
        "parentId": "親の id または null"
      }}
    ]
  }}
}}

困りごとが無ければ mentions は空配列にしてください。
"""

# 整理マップの上限。画面 (箇条書きツリー) と払うトークンを抑えるための機械的な締め。
# ここで切ると「LLM が出したのに画面に出ない節」が生まれるが、切らないと右ペインが
# 際限なく伸びる。切ったことはログに出す (静かに捨てない)。
_MAX_MAP_NODES = 24
_MAX_MAP_LABEL = 40


def _format_messages(messages: list[ConversationMessage]) -> str:
    """呼び出し側から渡された会話をテキストに整形する (#183)。"""
    lines = []
    for m in messages:
        label = "ユーザー" if m.role == "user" else "AI"
        lines.append(f"{label}: {m.text}")
    return "\n".join(lines)


def _format_history(history: ChatHistory) -> str:
    """ChatHistory をテキストに整形する。system メッセージは除外する。"""
    lines = []
    for msg in history.messages:
        if msg.role == "system":
            continue
        label = "ユーザー" if msg.role == "user" else "AI"
        lines.append(f"{label}: {msg.text}")
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


def _coerce_theme(value: object) -> Theme:
    """LLM が返したテーマを固定分類に丸める。未知なら「未分類」。

    戻り値は `str` ではなく `Theme` (#488)。ここが str のままだと、THEMES と
    Theme がずれたとき (= 丸めた先が pydantic に弾かれる) を型検査が見逃す。
    """
    for theme in THEMES:
        if value == theme:
            return theme
    return "未分類"


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


_NODE_KINDS = ("topic", "hypothesis", "unknown")
_NODE_STATUSES = ("confirmed", "tentative", "unexplored")


def _coerce_thinking_map(raw: object, session_id: str) -> ThinkingMap | None:
    """LLM が返した thinkingMap を安全な ThinkingMap へ (#433)。

    LLM の生成物なので、**画面が壊れうる形は全部ここで潰す**:
      - label が空の節は捨てる (画面に空行が出るだけで、何も伝えない)
      - 未知の kind は topic に、未知の status は tentative に丸める
        (unexplored / confirmed へ丸めると「未探索の枝の数」「確定の数」= 画面に出る
         実数が、LLM の書き損じで動く。断定の弱い tentative に寄せる)
      - 親が存在しない / 自分自身 / 循環している parentId は null (根扱い) に落とす
        — 節を捨てると画面から消えて数と合わなくなるので、**節は残して線だけ切る**
      - problem_id は必ず null (#321 まで — LLM が推測した Problem id を画面に出さない)

    節が 1 つも残らなければ None を返す (「マップ無し」と「空マップ」を画面で区別しない)。
    """
    data = raw if isinstance(raw, dict) else None
    if data is None:
        return None
    raw_nodes = data.get("nodes")
    if not isinstance(raw_nodes, list):
        return None

    nodes: list[ThinkingNode] = []
    parents: dict[str, str | None] = {}
    for index, item in enumerate(raw_nodes):
        if not isinstance(item, dict):
            continue
        # **文字列でない label は空ラベルと同じく捨てる** (Codex P2)。`str()` に通すと
        # `None` → "None" / `{}` → "{}" のような**非空文字列**に化け、直後の空判定を
        # すり抜けて「検証できない値が正常な話題として件数に加算される」。
        raw_label = item.get("label")
        label = raw_label.strip() if isinstance(raw_label, str) else ""
        if not label:
            continue
        raw_id = item.get("id")
        node_id = raw_id if isinstance(raw_id, str) and raw_id else f"node-{index}"
        if (
            node_id in parents
        ):  # id の重複は後勝ちにせず先勝ちで捨てる (親の指す先が割れる)
            continue
        kind = item.get("kind")
        status = item.get("status")
        parent_raw = item.get("parentId")
        parents[node_id] = str(parent_raw) if isinstance(parent_raw, str) else None
        nodes.append(
            ThinkingNode(
                id=node_id,
                kind=kind if kind in _NODE_KINDS else "topic",
                label=label[:_MAX_MAP_LABEL],
                status=status if status in _NODE_STATUSES else "tentative",
                parent_id=parents[node_id],
                problem_id=None,
            )
        )

    if len(nodes) > _MAX_MAP_NODES:
        # 上限で切ったことは残す (静かに捨てると「LLM が出したのに出ない」が説明できない)。
        logger.info(
            "Thinking map truncated session=%s nodes=%d limit=%d",
            session_id,
            len(nodes),
            _MAX_MAP_NODES,
        )
        nodes = nodes[:_MAX_MAP_NODES]

    known = {n.id for n in nodes}
    for node in nodes:
        node.parent_id = _resolve_parent(node, parents, known)

    if not nodes:
        return None
    return ThinkingMap(nodes=nodes)


def _resolve_parent(
    node: ThinkingNode, parents: dict[str, str | None], known: set[str]
) -> str | None:
    """親が辿れる (存在する / 自分に戻らない) ときだけ親として認める。

    無いと: 循環した parentId (a→b→a) でツリー描画が無限再帰する / 上限で切られた
    親を指す節が画面から消える。
    """
    parent = node.parent_id
    if parent is None or parent not in known or parent == node.id:
        return None
    seen = {node.id}
    cursor: str | None = parent
    while cursor is not None:
        if cursor in seen:
            return None  # 循環 → 根に落とす
        seen.add(cursor)
        cursor = parents.get(cursor)
        if cursor is not None and cursor not in known:
            break
    return parent


def _clamp_confidence(value: object) -> float | None:
    if value is None:
        return None
    # 入力は JSON 由来のスカラー (数値 / 文字列 / bool は int のサブクラス) だけ。
    # それ以外 (dict / list / 任意のオブジェクト) は従来も float() が TypeError を
    # 出して None に落ちていたので、振る舞いは変えずに「float に渡せる型だけ渡す」を
    # 型の上でも示す (#488)。
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return None


async def extract(
    session_id: str,
    existing_problems: list[ExistingProblemRef],
    session_repo: SessionRepository,
    client: BaseChatClient,
    messages: list[ConversationMessage] | None = None,
) -> ExtractionResult:
    # 会話が渡されていればそれを使う (#183)。session_repo はプロセスメモリなので、
    # scale-to-zero・スケールアウト・リビジョン差し替えのいずれでも消える。
    # 渡されない場合は従来どおり履歴を見る (旧クライアントとの互換)。
    if messages:
        conversation = _format_messages(messages)
    else:
        history = await session_repo.get(session_id)
        if history is None:
            raise ExtractionUnavailable(
                f"Session not found and no messages were provided: {session_id!r}"
            )
        conversation = _format_history(history)
    prompt = _EXTRACT_PROMPT.format(
        conversation=conversation,
        existing_problems=_format_existing(existing_problems),
        theme_list=_THEME_LIST,
    )

    raw = (await complete(client, prompt)).strip()
    parts = raw.split("```")
    if len(parts) >= 3:
        raw = parts[1].removeprefix("json").strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        # 空の結果を返すと「困りごとは見つかりませんでした」と表示され、壊れたことが
        # ユーザーにもログにも伝わらない (#183)。失敗として上げる。
        #
        # ただし `raw` は**抽出された困りごとの要約そのもの** = このプロダクトで最も
        # 機微なテキスト。ログに出すのは指紋 (長さ + ハッシュ先頭) までに留め、
        # ref でクライアント側のエラーと突き合わせられるようにする (Issue #313)。
        ref = new_ref()
        logger.error(
            "Extract JSON parse failed ref=%s session=%s response=%s",
            ref,
            session_id,
            fingerprint(raw),
        )
        raise ExtractionParseError(
            client_detail(ref, "LLM の応答を解釈できませんでした")
        ) from exc

    known = {p.id: p for p in existing_problems}
    now = datetime.now(timezone.utc).isoformat()
    items: list[ExtractedItem] = []
    # problem_id -> このバッチ反映後の mention_count。同一 Dump 内で同じ既存 Problem に
    # 複数 Mention が寄る場合に累積させる (existing は共有オブジェクトで更新されないため)。
    running_count: dict[str, int] = {}
    updated_ids: set[str] = set()
    new_count = 0

    for m in data.get("mentions", []):
        if not isinstance(m, dict):
            continue
        theme = _coerce_theme(m.get("theme"))
        grouping_value = m.get("grouping")
        grouping_raw = grouping_value if isinstance(grouping_value, dict) else {}
        confidence = _clamp_confidence(grouping_raw.get("confidence"))

        existing_id = grouping_raw.get("existingProblemId")
        # 変数名は `existing` (この関数の上流にある `ref` = ログ突き合わせ用の
        # エラー参照 ID と同名だった / #488 の型検査導入で表面化)
        existing = known.get(existing_id) if existing_id else None

        if existing is not None:
            # 既存 Problem への再出現。同一バッチ内の重複ヒットを累積する。
            problem_id = existing.id
            running_count[problem_id] = (
                running_count.get(problem_id, existing.mention_count) + 1
            )
            # 既存への寄せは「既存との類似度スコア」を持つ
            grouping_conf = confidence
            outcome = GroupingOutcome(
                kind="existing",
                problem_id=problem_id,
                problem_title=existing.title,
                problem_theme=existing.theme,
                is_recurrence=True,
                mention_count=running_count[problem_id],
                reignited=existing.status != "open",
                grouping_confidence=grouping_conf,
            )
            updated_ids.add(problem_id)
        else:
            # 新規 Problem を起こす
            problem_id = f"prob-{uuid.uuid4()}"
            running_count[problem_id] = 1
            title = grouping_raw.get("newProblemTitle") or m.get("statement", "")
            # 新規は既存 Problem との類似度が無いため null (domain.ts: new なら null)
            grouping_conf = None
            outcome = GroupingOutcome(
                kind="new",
                problem_id=problem_id,
                problem_title=str(title),
                problem_theme=theme,
                is_recurrence=False,
                mention_count=1,
                reignited=False,
                grouping_confidence=grouping_conf,
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
            grouping_confidence=grouping_conf,
        )
        items.append(ExtractedItem(mention=mention, grouping=outcome))

    return ExtractionResult(
        session_id=session_id,
        items=items,
        new_problem_count=new_count,
        updated_problem_count=len(updated_ids),
        # 整理マップ (#433)。同じ 1 回の LLM 出力に相乗りしているので追加呼び出しは 0。
        # マップの解釈に失敗しても抽出は落とさない (右ペインのタブが空になるだけ)。
        thinking_map=_coerce_thinking_map(data.get("thinkingMap"), session_id),
    )
