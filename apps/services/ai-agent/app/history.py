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
from collections.abc import Iterable

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
