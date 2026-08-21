"""[単体] 通報ステップの判定 (Issue #507)。

無いと何が静かに通るか:
    1. **通報の失敗が run の色に化ける** — 成功したデプロイが通報の失敗だけで赤になり、
       run の色が「dev に届いたか」を答えなくなる (2026-08-17 の 3 run が実例)。
       逆に全部を緑に倒すと、落ちたのに Issue が立たない回が黙って通る。
       step_verdict() はこの 2 方向のどちらに倒れても run は動き続けるので、
       テストが無いと誰も気づけない。
    2. **一覧が引けなかったことが「Issue は無い」に丸まる** — 旧実装はここで無言で
       死んでいた。丸めると、失敗した回に Issue が立たない (= 沈黙が異常なしを意味
       しなくなる / ADR 0035 D2 の前提が崩れる)。
    3. **状況ページの目印がズレる** — コメント文言と product_status.py の
       FAILURE_COMMENT_MARKER が食い違うと、再発時刻の計測が黙って 0 件になる。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import notify_rules as rules

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_単体_失敗側は通報できないと赤にし成功側は赤にしない() -> None:
    """#507 の中心。ここを 1 行壊すとどちらかの事故が復活する。"""
    failures = ["open Issue の一覧を取得できなかった (rc=1)"]

    # デプロイが成功した回: 失うのは「open Issue を閉じる」だけなので step は赤にしない
    ok = rules.step_verdict("success", True, failures)
    assert ok.fail_step is False
    assert "赤にしない" in ok.message
    assert failures[0] in ok.message  # 何に失敗したかを必ず持ち回る

    # job が落ちた回: 通報できない = 障害が Issue に出ない。ここは赤のままにする
    ng = rules.step_verdict("failure", True, failures)
    assert ng.fail_step is True
    assert failures[0] in ng.message


def test_単体_通報に失敗していなければ赤にしない() -> None:
    assert rules.step_verdict("failure", True, []).fail_step is False
    assert rules.step_verdict("success", True, []).fail_step is False


def test_単体_cancelled_の扱いは呼び出し元の意図に従う() -> None:
    """status-page は cancel-in-progress の自己中断が正常運用 (= success 側)、
    deploy / golden-path はタイムアウトが障害 (= failure 側)。"""
    assert rules.effective_status("cancelled", True) == "failure"
    assert rules.effective_status("cancelled", False) == "success"
    assert rules.step_verdict("cancelled", True, ["x"]).fail_step is True
    assert rules.step_verdict("cancelled", False, ["x"]).fail_step is False


def test_単体_未知の_job_status_は異常なしに丸めない() -> None:
    assert rules.effective_status("", True) == "failure"
    assert rules.effective_status("neutral", True) == "failure"
    assert rules.step_verdict("", True, ["x"]).fail_step is True


def test_単体_一覧が引けないときは失敗側だけ新規に立てる() -> None:
    """「open Issue があるか不明」を「無い」に丸めない (旧実装の無言死の跡地)。

    失敗側で黙ると **落ちたのに Issue が立たない**。成功側で立てると
    **直っているのに障害 Issue が湧く**。倒し方は非対称。
    """
    assert (
        rules.plan_action("failure", True, True, lookup_ok=False, has_open_issue=False)
        == rules.OPEN
    )
    assert (
        rules.plan_action("success", True, True, lookup_ok=False, has_open_issue=False)
        == rules.NOOP
    )
    # has_open_issue の値は lookup_ok=False では判断材料にしない (不明なので)
    assert (
        rules.plan_action("success", True, True, lookup_ok=False, has_open_issue=True)
        == rules.NOOP
    )


def test_単体_一覧が引けたときは既存の判定をそのまま使う() -> None:
    assert (
        rules.plan_action("failure", True, True, lookup_ok=True, has_open_issue=False)
        == rules.OPEN
    )
    assert (
        rules.plan_action("failure", True, True, lookup_ok=True, has_open_issue=True)
        == rules.APPEND
    )
    assert (
        rules.plan_action("success", True, True, lookup_ok=True, has_open_issue=True)
        == rules.CLOSE
    )
    # 監視対象が走っていない success は「復旧した」と言えない
    assert (
        rules.plan_action("success", False, True, lookup_ok=True, has_open_issue=True)
        == rules.NOOP
    )


def test_単体_Issue_の突合はタイトル完全一致() -> None:
    """部分一致にすると別 workflow の Issue に相乗りし、他人の障害を勝手に閉じる。"""
    title = rules.issue_title("deploy")
    issues = [
        {"number": 1, "title": "[ci-failure] golden-path-monitor が落ちている"},
        {"number": 2, "title": "[ci-failure] deploy が落ちている (調査中)"},
        {"number": 3, "title": title},
        {"number": 4, "title": title},
    ]
    assert rules.select_issue_number(issues, title) == 3
    assert rules.select_issue_number([], title) is None
    assert rules.select_issue_number([{"number": "9", "title": title}], title) is None


def test_単体_ラベルの既存衝突と本当のエラーを分ける() -> None:
    """両方を同じ扱いにすると、**ラベルの付かない Issue** が立って
    状況ページの ci-failure 集計から静かに漏れる。"""
    assert rules.classify_label_create(0, "") == "created"
    assert (
        rules.classify_label_create(1, "HTTP 422: Validation Failed\nName already exists")
        == "already-exists"
    )
    assert (
        rules.classify_label_create(1, "HTTP 403: Resource not accessible by integration")
        == "error"
    )


def test_単体_再発コメントの目印が状況ページの定数と一致する() -> None:
    """無いと何が静かに通るか: 文言を変えても run は緑のまま通り、状況ページの
    「今も落ちている」判定 (再発コメントの時刻) が黙って 0 件になる (PR #281)。"""
    spec = importlib.util.spec_from_file_location(
        "product_status_for_marker_check",
        REPO_ROOT / "cicd" / "scripts" / "status-page" / "product_status.py",
    )
    assert spec and spec.loader
    product_status = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(product_status)

    assert rules.FAILURE_COMMENT_MARKER == product_status.FAILURE_COMMENT_MARKER
    body = rules.failure_comment("https://example/run/1", "abc1234", "main")
    assert product_status.FAILURE_COMMENT_MARKER in body
    assert "https://example/run/1" in body
    # 復旧コメントに再発の目印が混ざると「復旧した」が「まだ落ちている」に化ける
    assert product_status.FAILURE_COMMENT_MARKER not in rules.recovery_comment(
        "https://example/run/1", "abc1234", "main"
    )


def test_単体_新規_Issue_本文に切り分けの材料が入る() -> None:
    body = rules.issue_body(
        "**dev への自動デプロイが失敗しました。**",
        "deploy",
        "https://example/run/1",
        "abc1234",
        "main",
    )
    for expected in ("dev への自動デプロイ", "`deploy`", "https://example/run/1", "`abc1234`", "`main`"):
        assert expected in body
