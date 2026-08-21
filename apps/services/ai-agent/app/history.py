"""会話履歴の薄い自前コンテナ (ADR 0016 M1-5 / SK ChatHistory の置き換え)。

MAF には bare chat client と組で使う「履歴コンテナ」の公開型が無い
(HistoryProvider / SessionStore は ChatAgent のスレッド管理に結合しており、
M1 の等価移行で持ち込むと変化が 2 種類になる)。そこで SK ChatHistory が
担っていた最小面 — 追記ヘルパ / messages 列 / Cosmos 直列化 — だけを
MAF の `Message` 型の上に薄く実装する。メッセージの実体は MAF `Message`
そのもの (role は素の str、テキストは `.text`)。

直列化は MAF Message の SerializationMixin (to_dict / from_dict) に委譲する。
`deserialize` は PR #261 が書いた SK ChatHistory.serialize 形式の既存 Cosmos
文書 (sessions コンテナ / TTL 7 日) も読める — 移行デプロイ直後に進行中の
会話履歴が黙って空になるのを防ぐ後方互換 (この互換分岐は SK 文書の自然消滅
後に消してよい)。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

from agent_framework import Message


class ChatHistory:
    """時系列の Message 列。SK ChatHistory の追記ヘルパ互換を最小限持つ。"""

    def __init__(self, messages: Iterable[Message] | None = None) -> None:
        self.messages: list[Message] = list(messages or [])

    def add_system_message(self, text: str) -> None:
        self.messages.append(Message(role="system", contents=[text]))

    def add_user_message(self, text: str) -> None:
        self.messages.append(Message(role="user", contents=[text]))

    def add_assistant_message(self, text: str) -> None:
        self.messages.append(Message(role="assistant", contents=[text]))

    def prune(self, *, max_messages: int, max_chars: int) -> None:
        """窓の外に出たメッセージを捨てる (`select_window` と同じ判定 / 破壊的)。

        LLM へ渡す直前だけで刈ると Cosmos の 1 文書 (2MB 上限) は育ち続けるので、
        保存側でも同じ窓まで落とす。**UI の履歴表示には影響しない** — 画面が読む
        会話ログは BFF 側の別リポジトリが持っており、この文書は「次のターンで LLM に
        渡す文脈」専用。
        """
        self.messages = select_window(
            self.messages, max_messages=max_messages, max_chars=max_chars
        )

    def serialize(self) -> str:
        """Cosmos 文書へ入れる JSON 文字列 (MAF Message.to_dict ベース)。"""
        return json.dumps(
            {"messages": [m.to_dict() for m in self.messages]}, ensure_ascii=False
        )

    @classmethod
    def deserialize(cls, raw: str) -> "ChatHistory":
        """serialize の逆。SK ChatHistory.serialize 形式 (items/content_type) も読む。"""
        data = json.loads(raw)
        messages: list[Message] = []
        for m in data.get("messages", []):
            if "items" in m:
                # SK 形式 (PR #261 時点の既存文書): role は素の str、テキストは
                # items[].content_type == "text" の text を連結して写す
                text = "".join(
                    item.get("text", "")
                    for item in m["items"]
                    if item.get("content_type") == "text"
                )
                messages.append(Message(role=m["role"], contents=[text]))
            else:
                messages.append(Message.from_dict(m))
        return cls(messages)


# ── 会話履歴の窓 (#486 / design-gate 承認 2026-08-17) ──────────────────────────
#
# ChatHistory は毎ターン全量が LLM へ再送されるので、上限が無いと TTL 7 日の間に
# セッションが無限に育ち、次の順で壊れる:
#   1. トークン費が会話長に線形で増える
#   2. context 超過でそのセッションの**全ターンが恒久 500** (もう会話を再開できない)
#   3. 1 文書全載せの Cosmos save が 2MB 上限で失敗する
#
# 承認された方式 (要約コンパクションは #501 で窓の上に載せる):
#   - 窓は**ターン境界**で切る = user 発言から次の user 発言の直前までを 1 かたまりに
#     し、古いターンから丸ごと落とす。ツール結果 (system) と assistant 応答を
#     その user 発言から切り離さないため
#   - **先頭の system プロンプトは窓の外で常時保持** — これが落ちるとエージェントが
#     人格ごと入れ替わる
#   - 上限は**件数 + 文字数予算の二本立て** (tokenizer 依存を足さない)


def _message_chars(message: Message) -> int:
    """1 メッセージの文字数。テキストが無い content は直列化長で代用する。

    履歴に積まれるのは今のところ全部テキスト (`add_*_message`) だが、`.text` が
    空なら 0 と数える実装にすると、将来 function_call content を履歴へ積んだ瞬間に
    **予算を消費しないメッセージ**が生まれ、「上限内」を名乗ったまま無限に育つ。
    測れないものを 0 と report しないために、直列化長を下限として使う。
    """
    text = message.text
    if text:
        return len(text)
    return len(json.dumps(message.to_dict(), ensure_ascii=False))


def select_window(
    messages: Sequence[Message], *, max_messages: int, max_chars: int
) -> list[Message]:
    """LLM へ渡す分だけを新しい側から選ぶ (純粋関数 / 入力は変更しない)。

    返す列 = `先頭の system プロンプト` + `予算に収まる範囲の新しいターン群`。

    予算は**保持する system プロンプトも数えたうえで**両方を満たす:
        len(返り値) <= max_messages かつ sum(文字数) <= max_chars

    ただし**最新ターンだけは予算を超えても落とさない**。落とすと「いま答えるべき
    user 発言」が消えて、LLM は前置きだけを渡されることになる (窓が会話そのものを
    壊す)。1 ターンの大きさは BFF の MAX_MESSAGE_LENGTH=8,000 とツール実行の上限
    (`llm_max_function_calls`) で押さえられているので、この抜けは有界。
    """
    if not messages:
        return []

    # 先頭の system プロンプト = 最初の非 system メッセージより前 (常時保持)。
    # ツール結果も role="system" なので「system を全部残す」ではこの窓は効かない
    pinned_end = 0
    while pinned_end < len(messages) and messages[pinned_end].role == "system":
        pinned_end += 1
    pinned = list(messages[:pinned_end])
    rest = list(messages[pinned_end:])

    # ターン境界で分割 (user 発言が各ターンの先頭)。最初の user より前に残った
    # 非 system メッセージは、直後のターンではなく単独のかたまりとして扱う
    turns: list[list[Message]] = []
    for message in rest:
        if message.role == "user" or not turns:
            turns.append([message])
        else:
            turns[-1].append(message)

    budget_messages = max_messages - len(pinned)
    budget_chars = max_chars - sum(_message_chars(m) for m in pinned)

    kept: list[list[Message]] = []
    for turn in reversed(turns):
        turn_messages = len(turn)
        turn_chars = sum(_message_chars(m) for m in turn)
        if kept and (turn_messages > budget_messages or turn_chars > budget_chars):
            break  # ここより古いターンは丸ごと落とす
        budget_messages -= turn_messages
        budget_chars -= turn_chars
        kept.append(turn)

    return pinned + [m for turn in reversed(kept) for m in turn]
