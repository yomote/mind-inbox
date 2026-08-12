"""機微データを外に出さずに追跡可能性だけを残すためのヘルパー (Issue #313 / rubric S3)。

このサービスが扱うのは「ユーザーのモヤモヤ」= メンタルヘルスに近い個人の悩みで、
`.github/claude/security-rubric.md` は **PII 以上の慎重さで扱う。ログ・LLM プロンプト・
外部サービスへの流出は最重要リスク**と定めている。一方で何も出さないと、パース失敗も
上流エラーも追えなくなる — #183 の教訓どおり「静かに壊れている」が最悪の壊れ方なので、
デバッグ可能性を殺すのは対策ではない。

そこで**出口ごとに出してよいものを固定する**:

- **サーバのログ**: 本文 (相談の文面 / 抽出結果 / LLM の生出力) は出さない。代わりに
  `fingerprint()` = 長さ + SHA-256 の先頭だけを出す。同じ壊れ方が繰り返しているか、
  入力が変わったのかは fingerprint の一致/不一致で判定できる。例外の詳細 (traceback)
  はサーバのログ側にだけ残してよい。
- **クライアント (BFF → ブラウザ)**: 一般化した文言 + `ref` (相関 ID) だけ。ref を
  伝えてもらえば、同じ ref を持つサーバのログ行に必ず辿り着ける。

`ref` は 1 回の失敗ごとに新しく作る。ユーザーや会話に紐づく値ではない (逆引きで個人を
特定できるものを ref にしない) ため、ブラウザまで出しても機微情報の出口にはならない。
"""

from __future__ import annotations

import hashlib
import uuid

_FINGERPRINT_CHARS = 12

# クライアントに返す一般化メッセージ。詳細は同じ ref のサーバログにだけ存在する。
_GENERIC_DETAIL = "処理に失敗しました"


def new_ref() -> str:
    """1 回の失敗を指す相関 ID。ログとクライアント応答の両方に載せる。"""
    return uuid.uuid4().hex[:_FINGERPRINT_CHARS]


def fingerprint(value: object) -> str:
    """本文を出さずに「同じ内容か」だけを比較できる指紋。

    ログに出してよいのはこの形だけ (長さ + ハッシュの先頭)。元の文字列は復元できない。
    """
    text = value if isinstance(value, str) else repr(value)
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    return f"len={len(text)} sha256={digest[:_FINGERPRINT_CHARS]}"


def exception_kind(exc: BaseException) -> str:
    """ログ・クライアント双方に出してよい例外の識別。**メッセージ本文は含めない**。

    上流 (Azure OpenAI SDK) の例外文にはエンドポイント URL / デプロイ名 / api-version、
    コンテンツフィルタが引用したプロンプト断片が入りうるため、型名だけを使う。
    """
    return type(exc).__name__


def client_detail(ref: str, message: str = _GENERIC_DETAIL) -> str:
    """クライアントへ返してよいエラー本文 (一般化した文言 + ref)。"""
    return f"{message} (ref: {ref})"
