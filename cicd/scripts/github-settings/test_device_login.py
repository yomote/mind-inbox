"""[単体] device flow でワンタイムコードを出して短命トークンを取る (Issue #362)。

無いと何が静かに通るか:
    - **コードが出ないまま待ち続ける** — 2026-08-12 の実測 (run 31619029180) がこれ。
      `gh` が 1 バイトも出力しないまま 12 分ハングし、PO は読むものが無く、
      run は期限切れまで「承認待ち」と区別できない沈黙を続けた
    - **未知のエラーが「まだ承認されていない」に丸められる** — client_id の誤り・
      仕様変更・アプリの無効化が「PO が承認しないだけ」に見え、期限まで待つ。
      直った/壊れたの区別がつかなくなる
    - **壊れた interval で秒間ポーリングする** — `interval: 0` や欠落を素直に信じると
      GitHub に叩き続けて `slow_down` 地獄に入る
    - **トークンがログに残る** — `::add-mask::` を先に出さずに扱うと、以後の
      エコーやエラー出力に平文で載りうる。ファイルの権限が緩ければ後段のステップからも読める
    - **コードが案内文に埋もれる** — PO はモバイルで探す。1 行目に無ければ「見えない」と同じ

性質の出どころ:
    - OAuth 2.0 Device Authorization Grant (RFC 8628) の
      `authorization_pending` / `slow_down` / `expired_token` / `access_denied`
    - Issue #362 の完了条件 (コードが notice に出る / 承認しなければ待たずに落ちる)
    - CLAUDE.md「取れなかったものを異常なしと書かない」「沈黙と正常を区別する」
"""

from __future__ import annotations

import json
import os
import stat

import pytest
from device_login import (
    DEFAULT_INTERVAL_SEC,
    SLOW_DOWN_STEP_SEC,
    DeviceCode,
    DeviceFlowError,
    classify_poll,
    main,
    next_interval,
    notice_lines,
    parse_device_code,
    remaining_seconds,
)

VALID_CODE_PAYLOAD = {
    "device_code": "dev-secret-xyz",
    "user_code": "ABCD-1234",
    "verification_uri": "https://github.com/login/device",
    "expires_in": 900,
    "interval": 5,
}


# ---- コードを取り出せないなら、待たずに落ちる ----


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="空"),
        pytest.param({k: v for k, v in VALID_CODE_PAYLOAD.items() if k != "user_code"}, id="user_code欠落"),
        pytest.param({k: v for k, v in VALID_CODE_PAYLOAD.items() if k != "device_code"}, id="device_code欠落"),
        pytest.param({**VALID_CODE_PAYLOAD, "user_code": ""}, id="user_codeが空文字"),
        pytest.param({"error": "unauthorized_client", "error_description": "bad app"}, id="errorが返る"),
        pytest.param("not json object", id="オブジェクトでない"),
    ],
)
def test_単体_コードを取り出せない応答は例外になる(payload):
    """寛容に受けると「コードが無いのに承認を待つ」状態が作れてしまう (#362 の本体)。"""
    with pytest.raises(DeviceFlowError):
        parse_device_code(payload)


def test_単体_壊れた間隔は既定値に倒れる():
    """`interval: 0` を信じて秒間ポーリングしない。"""
    for broken in (0, -3, None, "", "fast"):
        code = parse_device_code({**VALID_CODE_PAYLOAD, "interval": broken})
        assert code.interval == DEFAULT_INTERVAL_SEC


# ---- 応答の解釈: 未知のエラーを pending に丸めない ----


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"access_token": "gho_xxx"}, "ok"),
        ({"error": "authorization_pending"}, "pending"),
        ({"error": "slow_down"}, "slow_down"),
        ({"error": "expired_token"}, "fail"),
        ({"error": "access_denied"}, "fail"),
        ({"error": "incorrect_client_credentials"}, "fail"),
        ({"error": "device_flow_disabled"}, "fail"),
        ({}, "fail"),
        ("nonsense", "fail"),
    ],
)
def test_単体_応答の解釈は待つ資格のあるものだけを待つ(payload, expected):
    assert classify_poll(payload).kind == expected


def test_単体_失敗には理由が残る():
    """「失敗した」だけでは切り分けられない — GitHub の言い分をそのまま持ち帰る。"""
    result = classify_poll({"error": "device_flow_disabled", "error_description": "not enabled"})
    assert "device_flow_disabled" in result.detail
    assert "not enabled" in result.detail


def test_単体_slow_downのときだけ間隔が伸びる():
    for kind in ("pending", "ok", "fail"):
        assert next_interval(5, classify_poll({"error": "authorization_pending"} if kind == "pending" else {"access_token": "t"} if kind == "ok" else {"error": "expired_token"})) == 5
    assert next_interval(5, classify_poll({"error": "slow_down"})) == 5 + SLOW_DOWN_STEP_SEC


# ---- PO が読める形で出す ----


def test_単体_コードは案内の1行目に単独で出る():
    """モバイルで探させない。notice のタイトル付き 1 行目がコードそのもの。"""
    lines = notice_lines(DeviceCode("dev", "ABCD-1234", "https://github.com/login/device", 900, 5))
    assert lines[0] == "::notice title=ワンタイムコード::ABCD-1234"
    assert any("github.com/login/device" in line for line in lines)


def test_単体_残り時間は負にならない():
    assert remaining_seconds(deadline=100.0, now=250.0) == 0
    assert remaining_seconds(deadline=100.0, now=40.0) == 60


# ---- 通しの振る舞い (HTTP だけ差し替える) ----


class FakeHttp:
    """`request_json` の差し替え。呼ばれた順に応答を返す。"""

    def __init__(self, code_payload, poll_payloads):
        self.code_payload = code_payload
        self.poll_payloads = list(poll_payloads)
        self.poll_calls = 0

    def __call__(self, url, fields, timeout=20):
        if url.endswith("/device/code"):
            return self.code_payload
        self.poll_calls += 1
        if not self.poll_payloads:
            return {"error": "authorization_pending"}
        return self.poll_payloads.pop(0)


@pytest.fixture
def fake_clock(monkeypatch):
    """時計を差し替える。`sleep` は実際に待たず、代わりに時刻を進める。

    単に `sleep` を潰すだけだと、**判定が壊れて「待ち続ける」側に倒れたときに
    テストが実時間で回り続ける** (期限は監視ループの実時間で測るため)。
    それでは変異を入れたときに「落ちる」ではなく「固まる」になり、
    テストが効いているのか確かめられない。時刻を進めることで、
    壊れた実装は必ず期限に到達して有限時間で赤くなる。
    """
    state = {"now": 0.0}
    monkeypatch.setattr("device_login.time.monotonic", lambda: state["now"])
    # 0 秒 sleep でも必ず前へ進める (進まないと無限ループを作れてしまう)
    monkeypatch.setattr(
        "device_login.time.sleep",
        lambda seconds: state.__setitem__("now", state["now"] + max(float(seconds), 0.5)),
    )
    return state


def test_単体_承認されたらトークンを他人に読めない権限で書く(tmp_path, monkeypatch, capsys, fake_clock):
    token_path = tmp_path / "token"
    monkeypatch.setattr(
        "device_login.request_json",
        FakeHttp(VALID_CODE_PAYLOAD, [{"error": "authorization_pending"}, {"access_token": "gho_secret"}]),
    )

    assert main(["--token-out", str(token_path)]) == 0
    assert token_path.read_text() == "gho_secret"
    # 0600 — 後段のステップや別ユーザーから拾えない
    assert stat.S_IMODE(os.stat(token_path).st_mode) == 0o600

    out = capsys.readouterr().out
    assert "::add-mask::gho_secret" in out, "トークンはマスク登録より先に出してはいけない"
    assert out.index("::add-mask::gho_secret") < out.index("認証できました")
    assert "::add-mask::dev-secret-xyz" in out, "device_code も伏せる (承認を横取りされうる)"


def test_単体_承認されなければ待たずに落ちてトークンを作らない(tmp_path, monkeypatch, capsys, fake_clock):
    token_path = tmp_path / "token"
    monkeypatch.setattr(
        "device_login.request_json",
        FakeHttp(VALID_CODE_PAYLOAD, [{"error": "access_denied", "error_description": "user said no"}]),
    )

    assert main(["--token-out", str(token_path)]) == 1
    assert not token_path.exists()
    out = capsys.readouterr().out
    assert "::error::" in out
    assert "access_denied" in out


def test_単体_期限が来たら承認待ちのまま終わる(tmp_path, monkeypatch, capsys, fake_clock):
    """`authorization_pending` が返り続けても、上限を超えたら赤で終える (沈黙で居座らない)。"""
    token_path = tmp_path / "token"
    fake = FakeHttp({**VALID_CODE_PAYLOAD, "expires_in": 900}, [])
    monkeypatch.setattr("device_login.request_json", fake)

    assert main(["--token-out", str(token_path), "--max-wait", "0"]) == 1
    assert not token_path.exists()
    assert "::error::" in capsys.readouterr().out


def test_単体_コードが出せなければポーリングを1回もしない(tmp_path, monkeypatch, capsys, fake_clock):
    """#362 の再発防止 — 出せないと分かった時点で終わる。待つ対象が無い。"""
    token_path = tmp_path / "token"
    fake = FakeHttp({"error": "unauthorized_client"}, [{"access_token": "never"}])
    monkeypatch.setattr("device_login.request_json", fake)

    assert main(["--token-out", str(token_path)]) == 1
    assert fake.poll_calls == 0
    assert not token_path.exists()
    assert "ワンタイムコードを出せませんでした" in capsys.readouterr().out


def test_単体_応答がJSONでなくても握り潰さない(tmp_path, monkeypatch, capsys, fake_clock):
    """`request_json` が上げる例外は DeviceFlowError として赤に届く。"""
    token_path = tmp_path / "token"

    def broken(url, fields, timeout=20):
        raise DeviceFlowError(f"{url} の応答が JSON ではない: {json.dumps({'x': 1})}")

    monkeypatch.setattr("device_login.request_json", broken)
    assert main(["--token-out", str(token_path)]) == 1
    assert "::error::" in capsys.readouterr().out
