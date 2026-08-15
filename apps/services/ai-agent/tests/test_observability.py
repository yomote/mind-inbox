"""[単体] ログに出してよい形 (`app/observability.py`) の性質を固定する。

このモジュールは「機微データをログに出さないための唯一の出口」なので、壊れても
例外は出ず、**個人の悩みが静かに Log Analytics へ流れ続ける**。入場条件 (strategy.md
§2.2「壊れても例外が出ず、データが静かに間違う」) にそのまま当たる。

性質で書く (§3): 具体例 1 個ではなく「どんな値を入れても漏れない」を、
実際に漏れやすい形 (日本語の悩み / メールアドレス / URL / JSON 断片) の全域で確かめる。
"""

import hashlib
import logging
import secrets

import pytest

from app import observability
from app.observability import (
    exception_frames,
    exception_kind,
    fingerprint,
    redact_framework_logs,
)

# 「実際に漏れると困る値」の代表。どれもこのプロダクトの経路に実在しうる形。
SENSITIVE_VALUES = [
    "妻との関係で限界が来ていて、毎晩眠れない",
    "tanaka.hiroshi@example.co.jp",
    "https://aoai-dev-mindbox.openai.azure.com/ deployment=gpt-4o",
    '{"statement": "転職したいが家族に言えない", "excerpt": "もう無理かも"}',
    "content_filter: 'self_harm' が検出されました (入力: 死にたい)",
    "退職",  # 短く候補も少ない = 辞書攻撃が最も効く側
]


def _raise_deep(message: str) -> None:
    """例外を**関数の中で**投げる (フレームが 1 段以上できるようにする)。"""

    def inner() -> None:
        raise ValueError(message)

    inner()


class TestExceptionFrames:
    """指摘 P1: traceback 経由で例外メッセージが漏れないこと。"""

    @pytest.mark.parametrize("secret", SENSITIVE_VALUES)
    def test_単体_例外メッセージはフレーム表現に一切現れない(self, secret):
        # 無いと: `exc_info=True` に戻す (or 例外文をログ行に足す) 変更が緑のまま通り、
        # コンテンツフィルタが引用したプロンプト断片・pydantic が弾いた値そのものが
        # traceback の最終行として Log Analytics に残り続ける (PR #324 P1 / rubric S3)。
        try:
            _raise_deep(secret)
        except ValueError as exc:
            rendered = exception_frames(exc)

        assert secret not in rendered

    @pytest.mark.parametrize("secret", SENSITIVE_VALUES)
    def test_単体_実際のログ行にも例外メッセージが乗らない(self, secret, caplog):
        # 無いと: helper だけ安全でも、呼び出し側が exc_info=True を付け直した瞬間に
        # 漏れが復活する。**LogRecord を整形した文字列**まで見て初めて出口を塞げる。
        logger = logging.getLogger("test.observability")
        with caplog.at_level(logging.ERROR):
            try:
                _raise_deep(secret)
            except ValueError as exc:
                logger.error(
                    "boom ref=%s kind=%s at=%s",
                    "deadbeef",
                    exception_kind(exc),
                    exception_frames(exc),
                )

        formatted = "\n".join(
            logging.Formatter().format(record) for record in caplog.records
        )
        assert secret not in formatted

    def test_単体_どこで壊れたかは残る(self):
        # 無いと: 「漏らさない」を満たす最も安易な実装 (何も出さない / exc_info を
        # 消すだけ) が通り、#183 の「静かに壊れている」— どのコードで落ちたのか
        # ログから辿れない状態 — に戻る。デバッグ可能性は落とさないのが条件。
        try:
            _raise_deep("値そのもの")
        except ValueError as exc:
            rendered = exception_frames(exc)

        assert "ValueError" in rendered
        assert "inner" in rendered  # 実際に raise した関数名
        assert "test_observability.py" in rendered  # ファイル
        assert ":" in rendered  # 行番号

    def test_単体_連鎖した例外も上流のフレームまで辿れるがメッセージは出ない(self):
        # 無いと: `raise X from Y` で包む経路 (extractor / _fail はどちらもこの形) で
        # 「本当に落ちた場所」= 上流のフレームが失われ、包んだ側の 1 行しか残らない。
        upstream_secret = "上流の例外文 tanaka@example.com"
        downstream_secret = "包んだ側の例外文 死にたい"
        try:
            try:
                _raise_deep(upstream_secret)
            except ValueError as cause:
                raise RuntimeError(downstream_secret) from cause
        except RuntimeError as exc:
            rendered = exception_frames(exc)

        assert "RuntimeError" in rendered
        assert "ValueError" in rendered  # 連鎖元まで残る
        assert "inner" in rendered  # 連鎖元のフレーム
        assert upstream_secret not in rendered
        assert downstream_secret not in rendered

    def test_単体_traceback_を持たない例外でも落ちない(self):
        # 無いと: まだ raise されていない例外オブジェクトを渡した瞬間に
        # ログ出力自体が例外を投げ、**元の障害がログに残らない**という最悪の壊れ方をする。
        assert "ValueError" in exception_frames(ValueError("未 raise"))


class TestFingerprint:
    """指摘 P2: 鍵無しの決定的ハッシュを辞書攻撃で照合できないこと。"""

    @pytest.mark.parametrize("value", SENSITIVE_VALUES)
    def test_単体_鍵無し_sha256_の先頭とは一致しない(self, value):
        # 無いと: 鍵なし SHA-256 に戻す変更が緑のまま通る。低エントロピーな値
        # (メールアドレス / 短い宛先 / 定型文) はログを持つ者が候補を総当たりすれば
        # 照合でき、「元の文字列は復元できない」という前提が崩れる (PR #324 P2)。
        unkeyed = hashlib.sha256(value.encode("utf-8")).hexdigest()
        digest = fingerprint(value).split("hmac=")[1]

        assert digest not in unkeyed
        assert not unkeyed.startswith(digest)

    @pytest.mark.parametrize("value", SENSITIVE_VALUES)
    def test_単体_プロセスが変われば同じ値でも指紋が変わる(self, value, monkeypatch):
        # 無いと: 鍵がプロセス跨ぎで固定 (定数 / 環境変数) の実装に差し替わっても
        # 緑のまま通り、ログを横串にして「同じ人の同じ悩み」を全期間追跡できてしまう。
        before = fingerprint(value)
        monkeypatch.setattr(observability, "_FINGERPRINT_KEY", secrets.token_bytes(32))
        after = fingerprint(value)

        assert before != after

    @pytest.mark.parametrize("value", SENSITIVE_VALUES)
    def test_単体_同じプロセス内では同じ値が同じ指紋になる(self, value):
        # 無いと: 毎回ランダム (ref と同じもの) に化けても通り、fingerprint 本来の用途
        # =「同じ入力で壊れ続けているのか、入力が変わったのか」の判別ができなくなる。
        assert fingerprint(value) == fingerprint(value)
        assert fingerprint(value) != fingerprint(value + "x")

    @pytest.mark.parametrize("value", SENSITIVE_VALUES)
    def test_単体_本文そのものは指紋に含まれない(self, value):
        # 無いと: デバッグしやすさを理由に本文の先頭数文字を足す、といった変更が通る。
        # 出してよいのは長さと digest だけ。
        rendered = fingerprint(value)

        assert value not in rendered
        assert rendered.startswith(f"len={len(value)} hmac=")


def _formatted(caplog) -> str:
    """root 側の出力 handler が実際に書く形 (= 出口に届く文字列) まで整形して見る。"""
    return "\n".join(logging.Formatter().format(record) for record in caplog.records)


class TestFrameworkLogRedaction:
    """Issue #418: MAF (`agent_framework` 階層) のどのロガーから出ても例外原文が
    出口 (root の出力 handler) に届かないこと。

    #417 の暫定対策はロガー直付けの Filter で、`agent_framework` 直の record しか
    見ていなかった。子ロガー経由 / % 引数の例外 / `exc_info=True` の 3 経路が
    素通しだったのは修正前の実測で確認済み (PR に記載)。

    **`canary` は実秘密ではない**: SENSITIVE_VALUES 由来の合成カナリア値を
    **わざと生のまま** MAF と同じ形でログへ流し、出口で消えている
    (`canary not in formatted`) ことを検査する。ここでログに出ているのは
    検出器であって秘密そのものではない — 変数名を secret にすると CodeQL が
    「本物の秘密の平文ログ」として警告する (PR #463 alert #5) ため、
    役割 (漏えい検出カナリア) を名前で表している。
    """

    @pytest.mark.parametrize("canary", SENSITIVE_VALUES)
    def test_単体_子ロガーからの_payload_行も指紋になる(self, canary, caplog):
        # 無いと: Filter 方式 (record が emit されたロガーにしか掛からない) に
        # 戻す変更が緑のまま通り、上流がロガーを分けた瞬間
        # (`agent_framework.x`) に例外原文の素通しへ戻る。
        redact_framework_logs()
        with caplog.at_level(logging.ERROR):
            logging.getLogger("agent_framework.future_module").error(
                f"Function failed. Error: {canary}"
            )

        formatted = _formatted(caplog)
        assert canary not in formatted
        assert "Function failed." in formatted  # 失敗の事実は残る (行ごと消さない)

    @pytest.mark.parametrize("canary", SENSITIVE_VALUES)
    def test_単体_引数に載った例外オブジェクトは型名と指紋になる(self, canary, caplog):
        # 無いと: MAF が実際に使う `logger.error("...: %s", exc)` の形
        # (agent_framework._mcp:2131 等) で、% 展開の瞬間に例外原文が出る。
        redact_framework_logs()
        with caplog.at_level(logging.ERROR):
            logging.getLogger("agent_framework._mcp").error(
                "MCP connection closed unexpectedly after reconnection: %s",
                RuntimeError(canary),
            )

        formatted = _formatted(caplog)
        assert canary not in formatted
        assert "RuntimeError" in formatted  # 何が起きたか (型名) は残る
        assert "MCP connection closed" in formatted  # どの行かも残る

    @pytest.mark.parametrize("canary", SENSITIVE_VALUES)
    def test_単体_exc_info_の_traceback_からも例外文が消えフレームは残る(
        self, canary, caplog
    ):
        # 無いと: MAF の `logger.warning(..., exc_info=True)`
        # (agent_framework._mcp:1670 等) で、Formatter が整形する traceback の
        # 最終行 = `Type: 例外文` として原文が出る。
        redact_framework_logs()
        with caplog.at_level(logging.WARNING):
            try:
                _raise_deep(canary)
            except ValueError:
                logging.getLogger("agent_framework._mcp").warning(
                    "Background MCP reload failed", exc_info=True
                )

        formatted = _formatted(caplog)
        assert canary not in formatted
        assert "Background MCP reload failed" in formatted
        assert "ValueError" in formatted  # 型名は残る
        assert "inner" in formatted  # どこで壊れたか (フレーム) も残る

    @pytest.mark.parametrize("canary", SENSITIVE_VALUES)
    def test_単体_redaction_自体が壊れたら原文ごと落ちて失敗が残る(
        self, canary, caplog, monkeypatch
    ):
        # 無いと: redaction 内の例外で (a) 原文がそのまま通る (最悪) か
        # (b) logging 機構へ例外が漏れて元の障害が消える。fail-closed —
        # 本文は失うが「redaction が失敗した」ことは成功と区別できる形で残す。
        redact_framework_logs()

        def _boom(_value: object) -> str:
            raise RuntimeError("redaction is broken")

        monkeypatch.setattr(observability, "fingerprint", _boom)
        with caplog.at_level(logging.ERROR):
            logging.getLogger("agent_framework").error(
                f"Function failed. Error: {canary}"
            )

        formatted = _formatted(caplog)
        assert canary not in formatted
        assert "redaction failed" in formatted
        assert "RuntimeError" in formatted  # 何で失敗したか (型名) は残る

    def test_単体_二重に張っても_handler_は_1_枚(self):
        # 無いと: import のたびに handler が増え、record 書き換えが多重に走る
        # (指紋の指紋化で「同じ壊れ方か」の判別が壊れる)。
        redact_framework_logs()
        redact_framework_logs()

        framework_logger = logging.getLogger("agent_framework")
        redactors = [
            h
            for h in framework_logger.handlers
            if isinstance(h, observability._FrameworkRedactionHandler)
        ]
        assert len(redactors) == 1


class TestOtelExitStaysClosed:
    """Issue #418 の「塞いでいないもの」を、開いた瞬間に赤になる形で pin する。"""

    def test_単体_otel_の_span_は非記録のまま(self):
        # 無いと: Application Insights 等の exporter を配線した瞬間、MAF の
        # capture_exception (= record_exception + repr(exception) /
        # SENSITIVE_DATA フラグと無関係) が例外原文を span に載せて送り出す。
        # ログ側の redaction はこの経路に掛からない。exporter を足すときは
        # このテストを更新し、span 側の redaction (SpanProcessor) を同時に
        # 入れること。
        import app.workflow  # noqa: F401 — MAF を import し終えた状態で見る

        from opentelemetry import trace

        span = trace.get_tracer("mind-inbox-otel-pin").start_span("probe")
        assert not span.is_recording()
