"""OAuth device flow で PO 本人の短命トークンを取る (#362)。

なぜ `gh auth login --web` をやめたか (2026-08-12 の実測 / run 31619029180):
  疑似端末 (`script`) 越しに動かした `gh` が、**ワンタイムコードを 1 バイトも
  出力しないまま 12 分間ハングした**。コードもプロンプトもエラーも出ず、
  PO は run ページを開いても読むものが無かった。出力が出るかどうかが
  `gh` の対話実装に依存する形をやめ、**自分で書いた 1 行を出す**。

この実装が守ること:
  - コードは `::notice::` で出す。run サマリ最上部のカードとして出るので
    モバイルでも確実に見え、**annotation は走行中でも API から読める**ため
    エージェント (窓口 PM) が PO に転記できる (生ログは完了後しか取れない)
  - **黙って待たない**。コードが取れなければ即座に非ゼロで落ちる。
    ポーリング中も残り時間を定期的に出す
    (cicd/CLAUDE.md「異常時だけ喋る設計にすると、沈黙と正常が区別できなくなる」)
  - **トークンを絶対にログへ出さない**。受け取った瞬間に `::add-mask::` へ渡し、
    ファイルには 0600 で書く。**応答本文をログに載せるときは必ず伏せてから**
    (下記 redact — 独立レビューが実測で見つけた漏出経路への対処 / #366)
  - **承認したのが本人かを確かめる**。`user_code` は public リポジトリの run ログに
    出るので、第三者が先に自分のアカウントで承認できてしまう。トークンの持ち主が
    期待した login でなければ、設定を 1 つも読まずに落とす

秘密情報は要らない: device flow の client_id は公開値で、`gh auth login --web`
が内部で使っているものと同じ。OAuth アプリの登録も不要。
**client_id は環境変数やリポジトリ変数で差し替えられない** — 特権 workflow の
承認先を PR に残らない経路 (Settings の変更) で変えられるようにしないため (#366)。

判定 (レスポンスの解釈) はすべて純粋関数として切り出してある — I/O の下の
関数群がテスト対象 (`test_device_login.py`)。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"

# `gh auth login --web` が使う公開 client_id (秘密情報ではない)。
# **上書き経路を作らない** — 差し替えるなら PR を出すこと。上書きできると、
# 特権 workflow の承認先を diff にもレビューにも残さずに攻撃者の OAuth App へ
# 向けられる (PO が承認した瞬間、アカウント全体の repo 権限が渡る / #366 レビュー)。
GH_CLI_CLIENT_ID = "178c6fc778ccc68e1d6a"

# ポーリング間隔の下限。GitHub が interval を返さない / 壊れた値を返したときの既定。
DEFAULT_INTERVAL_SEC = 5
# slow_down を受けたときに足す秒数 (OAuth device flow の仕様)。
SLOW_DOWN_STEP_SEC = 5
# 進捗を出す間隔。これを超えて黙らない。
HEARTBEAT_SEC = 30


class DeviceFlowError(Exception):
    """device flow が続行不能になった (握り潰さずここまで上げる)。"""


@dataclass(frozen=True)
class DeviceCode:
    """`POST /login/device/code` の応答のうち、この先で使うものだけ。"""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class PollResult:
    """`POST /login/oauth/access_token` 1 回分の解釈。

    kind:
      - "ok"       … token を取得した
      - "pending"  … PO がまだ承認していない (待つ)
      - "slow_down"… 速すぎる (interval を伸ばして待つ)
      - "fail"     … 続行不能 (期限切れ / 拒否 / 未知のエラー)
    """

    kind: str
    token: str = ""
    detail: str = ""


# ---- ここから純粋関数 (テスト対象) ----


def redact(text: str) -> str:
    """ログ・例外に載せる文字列からトークンらしきものを落とす。

    なぜ必要か (2026-08-12 / #366 の独立レビューが実測で再現):
        token endpoint が JSON 以外 (OAuth 既定の form-urlencoded) を返すと、
        「応答が JSON ではない」例外に **`access_token=gho_...` が丸ごと載り**、
        `::add-mask::` に登録する前に `::error::` として出ていた。
        public リポジトリの run ログは誰でも読めるので、アカウント全体に効く
        トークンがそのまま公開される経路だった。

    マスク (`::add-mask::`) は「登録した後」しか効かない。**登録前に本文を出す
    可能性がある場所は、すべてここを通す**。
    """
    text = re.sub(
        r"((?:access|refresh)_token[=\"':\s]+)[^&\s\"',}]+", r"\1[REDACTED]", text
    )
    return re.sub(r"\bgh[pousr]_[A-Za-z0-9]{8,}", "[REDACTED]", text)


def sanitize_for_annotation(value: str) -> str:
    """`::notice::` に埋める値から改行と `::` を落とす。

    値の出どころは github.com 固定なので現状は到達不能だが、埋め込み先が
    workflow command なので素通しにする理由が無い (#366 レビュー info)。
    """
    return re.sub(r"[\r\n]+", " ", value).replace("::", ":")


def parse_device_code(payload: object) -> DeviceCode:
    """device code の応答を読む。**欠けていたら例外**にする。

    ここを寛容にすると「コードが無いのに待ち続ける」状態が作れてしまう
    (それが #362 で 12 分溶けた形そのもの)。必須項目が 1 つでも無ければ落とす。
    """
    if not isinstance(payload, dict):
        raise DeviceFlowError(redact(f"応答が JSON オブジェクトではない: {payload!r}"))
    if payload.get("error"):
        raise DeviceFlowError(
            f"device code を取得できない: {payload.get('error')} "
            f"({payload.get('error_description', '説明なし')})"
        )
    missing = [
        key
        for key in ("device_code", "user_code", "verification_uri")
        if not payload.get(key)
    ]
    if missing:
        raise DeviceFlowError(
            f"応答に必須項目が無い: {', '.join(missing)} / 実際: {sorted(payload)}"
        )
    return DeviceCode(
        device_code=str(payload["device_code"]),
        user_code=str(payload["user_code"]),
        verification_uri=str(payload["verification_uri"]),
        expires_in=_positive_int(payload.get("expires_in"), 900),
        interval=_positive_int(payload.get("interval"), DEFAULT_INTERVAL_SEC),
    )


def _positive_int(value: object, fallback: int) -> int:
    """正の整数として読めなければ fallback。壊れた値で 0 秒間隔にしない。"""
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def classify_poll(payload: object) -> PollResult:
    """access_token の応答 1 回分を解釈する。

    **未知の error を「まだ承認されていない」に丸めない** — 丸めると、
    設定ミスや仕様変更が「PO が承認しないだけ」に見えて期限まで待ち続ける。
    """
    if not isinstance(payload, dict):
        return PollResult(
            kind="fail", detail=redact(f"応答が JSON オブジェクトではない: {payload!r}")
        )
    token = payload.get("access_token")
    if token:
        return PollResult(kind="ok", token=str(token))
    error = str(payload.get("error", "")).strip()
    description = str(payload.get("error_description", "")).strip()
    if error == "authorization_pending":
        return PollResult(kind="pending")
    if error == "slow_down":
        return PollResult(kind="slow_down")
    if not error:
        return PollResult(kind="fail", detail=f"token も error も無い応答: {sorted(payload)}")
    return PollResult(kind="fail", detail=f"{error} ({description or '説明なし'})")


def next_interval(current: int, result: PollResult) -> int:
    """slow_down を受けたら間隔を伸ばす。それ以外は据え置き。"""
    if result.kind == "slow_down":
        return current + SLOW_DOWN_STEP_SEC
    return current


def owner_matches(actual_login: object, expected_login: str) -> bool:
    """承認したのが期待した本人か (#366 レビュー minor)。

    `user_code` は public リポジトリの run ログ / annotation に出るので、
    **第三者が先に自分のアカウントで承認できる**。その場合 run は他人のトークンで
    続行し、権限不足の 403 が「設定が無効」と読まれて誤ったドリフトが
    データブランチに記録されうる。
    **期待値が空なら「確認しない」ではなく不一致**として扱う (素通しにしない)。
    """
    if not expected_login or not isinstance(actual_login, str) or not actual_login.strip():
        return False
    return actual_login.strip().lower() == expected_login.strip().lower()


def notice_lines(code: DeviceCode) -> list[str]:
    """PO に出す案内。**コードそのものを 1 行目に置く** (探させない)。

    `::notice::` は run サマリにカードとして出るので、走行中でもモバイルで見え、
    check-run の annotation として API からも読める。
    """
    return [
        f"::notice title=ワンタイムコード::{sanitize_for_annotation(code.user_code)}",
        (
            f"::notice title=入力先::{sanitize_for_annotation(code.verification_uri)} "
            "を開いて上のコードを入力し、承認してください"
        ),
        f"::notice title=期限::{code.expires_in // 60} 分以内 (過ぎたら何も変更せずに赤で終わります)",
    ]


def remaining_seconds(deadline: float, now: float) -> int:
    """締め切りまでの残り秒 (負にはしない)。"""
    return max(0, int(deadline - now))


# ---- ここから下は I/O ----


def request_json(url: str, fields: dict[str, str], timeout: int = 20) -> object:
    """フォーム POST して JSON を読む。失敗は握り潰さず例外にする。

    **応答本文を例外に載せる前に必ず redact する** — トークンは JSON 以外の
    形式でも本文に現れる (#366 の実測)。
    """
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Accept": "application/json", "User-Agent": "mind-inbox-github-settings"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read().decode()
    except urllib.error.HTTPError as exc:
        raise DeviceFlowError(
            f"{url} が HTTP {exc.code} を返した: {redact(exc.read().decode())[:200]}"
        ) from exc
    except OSError as exc:
        raise DeviceFlowError(f"{url} に到達できない: {exc}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise DeviceFlowError(f"{url} の応答が JSON ではない: {redact(body)[:200]}") from exc


def request_json_get(url: str, token: str, timeout: int = 20) -> object:
    """トークンで GET して JSON を読む。**本文はログに出さない** (login だけ使う)。"""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "mind-inbox-github-settings",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read().decode())
    except (OSError, json.JSONDecodeError) as exc:
        raise DeviceFlowError(
            f"{url} でトークンの持ち主を確認できない: {type(exc).__name__}"
        ) from exc


def emit(line: str) -> None:
    """Actions のログへ即座に流す (バッファに溜めない — 沈黙を作らない)。"""
    print(line, flush=True)


def acquire_code(args: argparse.Namespace) -> DeviceCode | None:
    """コードを取りに行く。取れなければ None (呼び元がそこで終わる)。"""
    try:
        return parse_device_code(
            request_json(DEVICE_CODE_URL, {"client_id": args.client_id, "scope": args.scopes})
        )
    except DeviceFlowError as exc:
        emit(f"::error::{exc}")
        emit("::error::ワンタイムコードを出せませんでした。**何も変更していません**。")
        return None


def run(args: argparse.Namespace) -> int:
    started = time.monotonic()
    # どの OAuth App に承認を求めたのかを痕跡に残す (client_id は公開値)。
    emit(f"承認先の client_id: {args.client_id}")
    code = acquire_code(args)
    if code is None:
        return 1

    # device_code は承認を横取りされうるので伏せる (user_code は見せるためのもの)。
    emit(f"::add-mask::{code.device_code}")
    emit("----------------------------------------------------------------")
    for line in notice_lines(code):
        emit(line)
    emit(f"  コード: {code.user_code}   入力先: {code.verification_uri}")
    emit("----------------------------------------------------------------")

    deadline = started + min(code.expires_in, args.max_wait)
    interval = code.interval
    last_heartbeat = started

    while True:
        now = time.monotonic()
        if now >= deadline:
            emit("::error::承認されないまま期限が来ました。**何も変更していません**。")
            emit("::error::もう一度 dispatch すれば新しいコードが出ます。")
            return 1
        time.sleep(min(interval, remaining_seconds(deadline, now)))
        try:
            payload = request_json(
                ACCESS_TOKEN_URL,
                {
                    "client_id": args.client_id,
                    "device_code": code.device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
        except DeviceFlowError as exc:
            # ここに来る時点でコードは既に出ている。「コードを出せなかった」と
            # 書かない (切り分けを誤らせる / #366 レビュー)。
            emit(f"::error::{exc}")
            emit("::error::承認の確認に失敗しました。**何も変更していません**。")
            return 1
        result = classify_poll(payload)
        if result.kind == "ok":
            emit(f"::add-mask::{result.token}")
            return _finish(args, result.token)
        if result.kind == "fail":
            emit(f"::error::device flow が失敗しました: {result.detail}")
            emit("::error::**何も変更していません**。")
            return 1
        interval = next_interval(interval, result)
        now = time.monotonic()
        if now - last_heartbeat >= HEARTBEAT_SEC:
            emit(
                f"承認待ち… コード {code.user_code} / 残り "
                f"{remaining_seconds(deadline, now)} 秒 (次の確認は {interval} 秒後)"
            )
            last_heartbeat = now


def _finish(args: argparse.Namespace, token: str) -> int:
    """トークンの持ち主を確かめてから書き出す。"""
    try:
        who = request_json_get(USER_URL, token)
    except DeviceFlowError as exc:
        emit(f"::error::{exc}")
        emit("::error::**何も変更していません**。")
        return 1
    login = who.get("login") if isinstance(who, dict) else None
    if not owner_matches(login, args.expect_login):
        emit(
            f"::error::承認したのが期待した本人ではありません "
            f"(承認: {login!r} / 期待: {args.expect_login!r})"
        )
        emit(
            "::error::公開ログに出たコードを第三者が先に承認した可能性があります。"
            "**何も変更していません**。"
        )
        return 1
    _write_token(args.token_out, token)
    emit(f"認証できました ({login} / トークンはこの run の中だけで使い、終了時に削除します)")
    return 0


def _write_token(path: str, token: str) -> None:
    """トークンを 0600 で**新規作成**する。環境変数や GITHUB_OUTPUT には出さない。

    出すと以降の全ステップに漏れる。読むのは点検ステップだけでよい。
    `O_EXCL | O_NOFOLLOW` — 既存ファイルやシンボリックリンクには書かない
    (`O_CREAT` だけだと既存ファイルの緩い権限がそのまま使われ、リンク先にも
     書けてしまう。GitHub-hosted では到達しにくいが self-hosted で実害 / #366)。
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    handle = os.open(path, flags, 0o600)
    with os.fdopen(handle, "w") as file:
        file.write(token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # 既定値のみ。**環境変数からは読まない** (上書き経路を作らない / 冒頭のコメント)。
    parser.add_argument("--client-id", default=GH_CLI_CLIENT_ID)
    # `read:org` は sync.py が一度も使わないので要求しない (repos/ しか叩かない)。
    parser.add_argument("--scopes", default="repo")
    parser.add_argument(
        "--expect-login",
        required=True,
        help="このアカウントが承認したときだけトークンを使う (公開されたコードの横取り対策)",
    )
    parser.add_argument("--token-out", required=True)
    parser.add_argument(
        "--max-wait",
        type=int,
        default=600,
        help="承認を待つ上限 (秒)。expires_in と短い方を採る",
    )
    args = parser.parse_args(argv)
    try:
        return run(args)
    except DeviceFlowError as exc:
        emit(f"::error::{redact(str(exc))}")
        emit("::error::device flow を完了できませんでした。**何も変更していません**。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
