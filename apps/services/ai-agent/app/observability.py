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


def _redact_exception_arg(value: object) -> object:
    """% 引数に**例外オブジェクトそのもの**が載っていたら型名 + 指紋へ。

    MAF は `logger.error("...: %s", exc)` の形も使う (agent_framework._mcp が実例)。
    例外文はユーザー入力・上流応答を含みうるので、原文の代わりに
    `exception_kind` + `fingerprint` を残す。例外以外の引数には触らない —
    どのログ行か判別する定型文まで潰すと、redaction 自体のデバッグができなくなる。
    """
    if isinstance(value, BaseException):
        return f"{exception_kind(value)} <redacted {fingerprint(str(value))}>"
    return value


def _redact_record(record: logging.LogRecord) -> None:
    """1 つの LogRecord から例外原文の出口を全部落とす (in-place)。

    **行そのものは消さない** — 消すと「ツールが失敗した」という事実まで見えなく
    なる (このリポジトリで最も繰り返している事故 = 取れなかったものを異常なしに
    しない)。level も logger 名も残したまま、本文だけを指紋・フレームに替える。
    """
    # 1) % 引数に載った例外オブジェクト → 型名 + 指紋
    if record.args:
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_exception_arg(a) for a in record.args)
        elif isinstance(record.args, dict):
            # logging は引数 1 個の dict をマッピング引数として許す
            record.args = {
                key: _redact_exception_arg(value) for key, value in record.args.items()
            }

    # 2) 既知の payload 行 (f-string で本文が埋め込み済み) → 本文を指紋へ
    message = record.getMessage()
    for prefix in _FRAMEWORK_PAYLOAD_PREFIXES:
        if message.startswith(prefix):
            payload = message[len(prefix) :].strip()
            message = f"{prefix} <redacted {fingerprint(payload)}>"
            record.msg = message
            record.args = ()
            break

    # 3) exc_info → traceback の最終行 (`Type: 例外文`) を出さず、frames + 指紋だけ残す。
    #    exc_text は Formatter が一度整形した traceback のキャッシュ — 残すと
    #    exc_info を消しても次の handler がキャッシュから原文を書いてしまう。
    if record.exc_info:
        exc = record.exc_info[1]
        note = " exc_info=<dropped>"
        if exc is not None:
            note = f" exc={exception_frames(exc)} <redacted {fingerprint(str(exc))}>"
        record.msg = record.getMessage() + note
        record.args = ()
        record.exc_info = None
        record.exc_text = None


class _FrameworkRedactionHandler(logging.Handler):
    """record を**書き換えるためだけ**の handler (emit しない)。

    logging の Filter は「その record が emit されたロガー自身」にしか掛からず、
    子ロガー (`agent_framework.*`) から propagate してくる record を素通しする
    (#417 の暫定 filter の穴 / Issue #418 の実測)。一方 **handler は propagate の
    経路上で必ず呼ばれる** — `agent_framework.x` の record は root の出力 handler に
    届く前に `agent_framework` に付いた handler を通る。そこで record を in-place に
    書き換えれば、後段の出力 handler (uvicorn / Azure Monitor など、アプリが
    所有しないものを含む) にはすでに redact 済みの record しか渡らない。
    """

    def handle(self, record: logging.LogRecord) -> bool:
        try:
            _redact_record(record)
        except Exception as redaction_error:  # noqa: BLE001
            # fail-closed: redaction が壊れたとき原文を通すのが最悪なので、
            # 本文ごと落として「redaction が失敗した」ことだけを残す。
            # 元の行の内容は失われる — それが見えなくなる代償として、失敗の
            # 事実 (型名) を成功と区別できる形で残す (静かに素通しへ戻さない)。
            record.msg = (
                f"<framework log redaction failed: {type(redaction_error).__name__}>"
            )
            record.args = ()
            record.exc_info = None
            record.exc_text = None
        return True

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover
        """呼ばれない (handle を override しているため)。出力は後段の handler の仕事。"""


def redact_framework_logs() -> None:
    """MAF (`agent_framework` 階層) のログから例外原文・payload を落とす (冪等)。

    **なぜ middleware だけでは足りないか** (#417 P2 の実測): アプリの
    `tool_boundary` middleware が例外を一般化できるのは `FunctionTool.invoke` の
    **外側**だけで、`invoke` の内側にある `logger.error(f"Function failed. Error:
    {exception}")` はそれより先に走る。つまり middleware での一般化と、この
    handler の両方が要る (層が違う)。

    **#417 の暫定 filter から変えたこと** (Issue #418):

    - ロガー直付けの Filter → `agent_framework` に付ける mutating Handler。
      子ロガー (`agent_framework.*`) から propagate してくる record も通るので、
      上流がロガーを分けても素通しに戻らない
    - `exc_info` を frames + 指紋に置換 (traceback の最終行 = 例外文が出口だった)
    - % 引数に載った例外オブジェクトを型名 + 指紋に置換 (`_mcp.py` が使う形)

    **効かない範囲** (残る前提):

    - 子ロガーが `propagate=False` + 自前 handler を持つ場合はこの経路を通らない
      (MAF 1.13 にそのようなロガーは無い — 出力 handler を足すのはアプリ側だけ)
    - 未知の f-string 行に例外文が**文字列として**埋め込まれた場合、既知 prefix
      以外は検出できない (上流へ例外文を丸ごとログに書かない改善を出す価値がある)

    **塞いでいないもの**: MAF は同じ例外を OTel span にも記録する
    (`capture_exception` = `record_exception` + `repr(exception)`。SENSITIVE_DATA
    フラグと**無関係に**原文が載る)。このサービスは exporter を 1 つも構成して
    いないので今は出口が無く、tests/test_observability.py が「span は非記録の
    まま」を pin している — Application Insights 等を配線するとそのテストが赤に
    なり、span 側の redaction (SpanProcessor) を足すことを強制する。
    """
    framework_logger = logging.getLogger(_FRAMEWORK_LOGGER)
    if any(
        isinstance(existing, _FrameworkRedactionHandler)
        for existing in framework_logger.handlers
    ):
        return
    framework_logger.addHandler(_FrameworkRedactionHandler())
