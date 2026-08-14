"""機微データを外に出さずに追跡可能性だけを残すためのヘルパー (Issue #313 / rubric S3)。

このサービスが扱うのは「ユーザーのモヤモヤ」= メンタルヘルスに近い個人の悩みで、
`.github/claude/security-rubric.md` は **PII 以上の慎重さで扱う。ログ・LLM プロンプト・
外部サービスへの流出は最重要リスク**と定めている。一方で何も出さないと、パース失敗も
上流エラーも追えなくなる — #183 の教訓どおり「静かに壊れている」が最悪の壊れ方なので、
デバッグ可能性を殺すのは対策ではない。

そこで**出口ごとに出してよいものを固定する**:

- **サーバのログ**: 本文 (相談の文面 / 抽出結果 / LLM の生出力) は出さない。代わりに
  `fingerprint()` = 長さ + **プロセス鍵つき HMAC** の先頭だけを出す。同じ壊れ方が
  繰り返しているか、入力が変わったのかは fingerprint の一致/不一致で判定できる。
  例外は `exception_kind()` (型名) と `exception_frames()` (どのファイル/行で壊れたか)
  だけを出し、**例外メッセージと traceback の本文行は出さない**。
- **クライアント (BFF → ブラウザ)**: 一般化した文言 + `ref` (相関 ID) だけ。ref を
  伝えてもらえば、同じ ref を持つサーバのログ行に必ず辿り着ける。

`ref` は 1 回の失敗ごとに新しく作る。ユーザーや会話に紐づく値ではない (逆引きで個人を
特定できるものを ref にしない) ため、ブラウザまで出しても機微情報の出口にはならない。

## ログに `exc_info=True` を使わない (PR #324 Codex 指摘 P1)

`logger.error(..., exc_info=True)` は traceback を整形するが、その**最終行は
`ExceptionType: 例外メッセージ`** であり、Azure OpenAI のコンテンツフィルタ例外
(引用されたプロンプト断片) や pydantic の ValidationError (不正だった値そのもの) が
そこに入る。クライアント応答を一般化しても、この出口が開いていれば機微データは
Log Analytics へ流れ続ける。

かといってスタックを丸ごと捨てると「どこで壊れたか」が分からなくなる (#183 の
「静かに壊れている」に戻る)。そこで**フレーム (どこで) とメッセージ (どの値で) を
分離**し、フレームだけを `exception_frames()` で出す。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import traceback
import uuid

_FINGERPRINT_CHARS = 12

# 1 つの例外について残すフレーム数 (末尾 = 実際に投げた場所から遡る)。
_MAX_FRAMES = 20
# `raise ... from ...` / 暗黙の連鎖をたどる深さ。循環は id() で止める。
_MAX_CHAINED = 5

# **プロセス起動ごとのランダム鍵** (PR #324 Codex 指摘 P2)。
#
# 鍵なしの SHA-256 は、値の候補集合が小さいとき (メールアドレス / 短い宛先 / 定型文 /
# 既知の ID) 総当たりで元の値に照合できるので、「復元できない」という前提が成立しない。
# また同じ値がいつも同じ digest になるため、ログを横串にすれば「同じ人の同じ悩み」を
# 追跡できてしまう。
#
# 鍵をプロセス内メモリだけに持つことで、外部の秘密管理 (Key Vault / 環境変数) を
# 増やさずに辞書攻撃と横串追跡の両方を潰す。**代償**: fingerprint の一致で相関が
# 取れるのは「同じプロセスが生きている間」だけになる — 再起動・再デプロイ・
# Container Apps の別レプリカを跨ぐと同じ入力でも digest が変わる。fingerprint の
# 用途は「今この障害が同じ入力で繰り返しているか」なので、この範囲で足りる。
_FINGERPRINT_KEY = secrets.token_bytes(32)

# クライアントに返す一般化メッセージ。詳細は同じ ref のサーバログにだけ存在する。
_GENERIC_DETAIL = "処理に失敗しました"


def new_ref() -> str:
    """1 回の失敗を指す相関 ID。ログとクライアント応答の両方に載せる。"""
    return uuid.uuid4().hex[:_FINGERPRINT_CHARS]


def fingerprint(value: object) -> str:
    """本文を出さずに「同じ内容か」だけを比較できる指紋。

    ログに出してよいのはこの形だけ (長さ + プロセス鍵つき HMAC の先頭)。鍵は
    プロセス内メモリにしか無いので、ログを手に入れた者が候補を総当たりしても
    元の値に照合できない (`_FINGERPRINT_KEY` の説明を参照)。
    """
    text = value if isinstance(value, str) else repr(value)
    digest = hmac.new(
        _FINGERPRINT_KEY, text.encode("utf-8", errors="replace"), hashlib.sha256
    ).hexdigest()
    return f"len={len(text)} hmac={digest[:_FINGERPRINT_CHARS]}"


def exception_kind(exc: BaseException) -> str:
    """ログ・クライアント双方に出してよい例外の識別。**メッセージ本文は含めない**。

    上流 (Azure OpenAI SDK) の例外文にはエンドポイント URL / デプロイ名 / api-version、
    コンテンツフィルタが引用したプロンプト断片が入りうるため、型名だけを使う。
    """
    return type(exc).__name__


def exception_frames(exc: BaseException) -> str:
    """例外が**どこで**壊れたかだけを返す (ファイル:行:関数の連なり)。

    `exc_info=True` の代わりに使う。返すのは **filename / lineno / 関数名だけ**で、

    - 例外メッセージ (traceback の最終行 = `ValueError: <値>`)
    - ソース行のテキスト (`FrameSummary.line`)
    - ローカル変数

    は一切含めない。つまり「どの行で壊れたか」は残り「どの値で壊れたか」は残らない。
    `lookup_lines=False` にしているのはソース行を**そもそも読み込まない**ためで、
    「読んだが出さない」ではなく「持っていない」状態にしておく。

    連鎖 (`raise ... from ...` / 例外処理中の再送出) は `<-` で繋いで残す — 上流の
    どこで始まったかが分からないと追跡が切れるため。各段の型名は `exception_kind`
    と同じで、型名自体は機微ではない。
    """
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and len(parts) < _MAX_CHAINED:
        if id(current) in seen:
            break
        seen.add(id(current))
        frames = traceback.StackSummary.extract(
            traceback.walk_tb(current.__traceback__), lookup_lines=False
        )[-_MAX_FRAMES:]
        where = " < ".join(
            f"{frame.filename}:{frame.lineno}:{frame.name}"
            for frame in reversed(frames)
        )
        parts.append(f"{exception_kind(current)}[{where}]")
        current = current.__cause__ or current.__context__
    return " <- ".join(parts)


def client_detail(ref: str, message: str = _GENERIC_DETAIL) -> str:
    """クライアントへ返してよいエラー本文 (一般化した文言 + ref)。"""
    return f"{message} (ref: {ref})"


# ── フレームワーク側のログ出口を塞ぐ (#417 P2 / Issue #418) ───────────────────

_FRAMEWORK_LOGGER = "agent_framework"

# MAF がツールの payload を**そのまま**載せるログ行の接頭辞 (実測 / agent_framework)。
# いずれも f-string で組まれているので、record に届いた時点で本文が埋まっている。
_FRAMEWORK_PAYLOAD_PREFIXES = (
    # FunctionTool.invoke の中 = **アプリの middleware より内側**。ツール本体が
    # 上流 URL・外部応答・相談内容を含む例外を投げると原文がそのまま出る
    "Function failed. Error:",
    # LLM が生成したツール引数 (= ユーザー発話由来)。DEBUG だが本番で DEBUG に
    # 落とした瞬間に会話の中身が Log Analytics へ流れる
    "Function arguments:",
    # ツールの戻り値
    "Function result:",
)


class _FrameworkPayloadRedactor(logging.Filter):
    """`agent_framework` ロガーの payload 行を指紋に差し替える。

    **行そのものは消さない** — 消すと「ツールが失敗した」という事実まで見えなく
    なる (このリポジトリで最も繰り返している事故 = 取れなかったものを異常なしに
    しない)。level も logger 名も残したまま、本文だけを `fingerprint` に替える。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for prefix in _FRAMEWORK_PAYLOAD_PREFIXES:
            if message.startswith(prefix):
                payload = message[len(prefix) :].strip()
                record.msg = f"{prefix} <redacted {fingerprint(payload)}>"
                record.args = ()
                break
        return True


def redact_framework_tool_logs() -> None:
    """MAF が自前でツール payload を出すログ行を指紋化する (冪等)。

    **なぜ middleware だけでは足りないか** (#417 P2 の実測): アプリの
    `tool_boundary` middleware が例外を一般化できるのは `FunctionTool.invoke` の
    **外側**だけで、`invoke` の内側にある `logger.error(f"Function failed. Error:
    {exception}")` はそれより先に走る。つまり middleware での一般化と、この
    フィルタの両方が要る (層が違う)。

    **効かない範囲**: logging のフィルタは「そのロガーに直接出た record」にしか
    掛からず、子ロガー (`agent_framework.*`) から propagate してくる record には
    掛からない。今 payload を載せているのは `agent_framework` ロガー自身なので
    足りているが、**上流がロガーを分けたら静かに漏れ始める** — そのため
    tests/test_workflow_tools.py が MAF の実ループを通して pin している。

    **塞いでいないもの**: MAF は同じ例外を OTel span にも記録する
    (`capture_exception`)。このサービスは exporter を 1 つも構成していないので
    今は出口が無いが、**Application Insights 等を有効化したらそこが新しい出口に
    なる** (ログとは別の経路なのでこのフィルタでは掛からない)。Issue #418。
    """
    framework_logger = logging.getLogger(_FRAMEWORK_LOGGER)
    if any(
        isinstance(existing, _FrameworkPayloadRedactor)
        for existing in framework_logger.filters
    ):
        return
    framework_logger.addFilter(_FrameworkPayloadRedactor())
