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
from app.observability import exception_frames, exception_kind, fingerprint

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
