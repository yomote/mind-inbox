"""プロンプト置き場 (ADR 0027 D2)。

自律改善ループ (ADR 0022) が書き換えてよい唯一の場所。文字列定数だけを置き、
ロジックは置かない。CI (`auto-improve-guard.yml`) がこのディレクトリを許可パスとして
扱うため、ここにロジックを置くとコードの自動改変を許すことになる。
"""

from __future__ import annotations

from .chat import CHAT_SYSTEM_PROMPT

__all__ = ["CHAT_SYSTEM_PROMPT"]
