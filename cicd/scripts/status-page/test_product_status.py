"""[単体] プロダクトの現在地 (Issue #280) — 分類と警告の判定を固定する。

無いと何が静かに通るか:
    このセクションは「壊れても例外は出ず、表示が静かに間違う」塊。
    deploy が赤なのに警告が出ない / apps/ を触る PR が「工場」列に紛れる /
    取得失敗が「異常なし」に見える — どれも見た目は普通のページのままなので、
    テストでしか捕まえられない。
"""

from product_status import (
    classify_prs,
    dev_state,
    gate_mark,
    next_candidates,
    pick_milestone,
    render,
)

_PEND = {"needs_human": [], "prs": [], "proposed": []}


def _run(c, s, t, e="push", sha="abc1234def5678"):
    return {"c": c, "s": s, "t": t, "e": e, "sha": sha, "u": "https://example.test/r"}


def test_単体_deploy赤のとき警告行が出る():
    """deploy が落ちている間は「dev が古いまま」を目立たせ、Issue 番号を指させる。"""
    runs = [
        _run("failure", "completed", "2099-01-03T00:00:00Z"),
        _run("failure", "completed", "2099-01-02T00:00:00Z"),
        _run("success", "completed", "2099-01-01T00:00:00Z", sha="feed5678abcd"),
    ]
    dev = dev_state(runs, deploy_issue=262)
    assert dev["ok"] is False
    assert dev["behind"] == 2  # 成功後に積まれた push が 2 本、どれも届いていない
    html = render({"dev": dev}, _PEND)
    assert "⚠️" in html
    assert "更新が届いていない" in html
    assert "deploy 赤" in html
    assert "#262" in html  # 指させる先 (ci-failure Issue)
    assert "feed567" in html  # dev に載っているのは最後に成功した commit


def test_単体_deploy緑なら警告を出さず反映済みと書く():
    runs = [_run("success", "completed", "2099-01-01T00:00:00Z")]
    html = render({"dev": dev_state(runs)}, _PEND)
    assert "⚠️" not in html
    assert "abc1234" in html
    assert "最新の main が反映済み" in html


def test_単体_未反映のマージ数はpushのrunで数える():
    """workflow_dispatch (手動 up/down) を「未反映のマージ」に数えないこと。"""
    runs = [
        _run(None, "in_progress", "2099-01-04T00:00:00Z"),  # 実行中の push も未反映
        _run("success", "completed", "2099-01-03T00:00:00Z", e="workflow_dispatch"),
        _run("success", "completed", "2099-01-02T00:00:00Z"),
    ]
    assert dev_state(runs)["behind"] == 1


def test_単体_appsを触るPRはプロダクト列に入る():
    """1 ファイルでも apps/ を触れば「プロダクト」。取得失敗は黙って工場に混ぜない。"""
    prs = [
        {"n": 1, "t": "UI 変更", "files": ["apps/frontend/src/App.tsx", "docs/x.md"]},
        {"n": 2, "t": "CI 変更", "files": [".github/workflows/test.yml"]},
        {"n": 3, "t": "取得失敗", "files": None},
    ]
    product, factory, unknown = classify_prs(prs)
    assert [p["n"] for p in product] == [1]
    assert [p["n"] for p in factory] == [2]
    assert [p["n"] for p in unknown] == [3]
    html = render({"dev": {"fetched": False}, "prs": prs}, _PEND)
    assert "未検証: 変更ファイル不明" in html  # 分類できなかった PR を明示する


def test_単体_次の候補はP1からci系ラベルとPRを除いて作成日順に5件():
    def issue(n, c, labels=("P1",), pr=False):
        return {"n": n, "t": f"issue {n}", "c": c, "labels": list(labels), "pr": pr}

    issues = [
        issue(9, "2099-01-09T00:00:00Z"),
        issue(1, "2099-01-01T00:00:00Z"),
        issue(2, "2099-01-02T00:00:00Z", labels=("P1", "ci-failure")),  # 障害対応は除く
        issue(3, "2099-01-03T00:00:00Z", pr=True),  # PR は Issue ではない
        issue(4, "2099-01-04T00:00:00Z"),
        issue(5, "2099-01-05T00:00:00Z"),
        issue(6, "2099-01-06T00:00:00Z"),
        issue(7, "2099-01-07T00:00:00Z"),
    ]
    picked = next_candidates(issues)
    assert [i["n"] for i in picked] == [1, 4, 5, 6, 7]  # 昇順 5 件。9 は溢れる


def test_単体_milestone未設定でも取得失敗でもページは落ちず区別して出る():
    # 取得失敗 (None) と「milestone が無い」([]) は別の表示
    html_fail = render({"milestones": None, "dev": {"fetched": False}}, _PEND)
    assert "未検証: milestone" in html_fail
    html_empty = render(
        {"milestones": [], "goal": pick_milestone([]), "dev": {"fetched": False}}, _PEND
    )
    assert "未設定" in html_empty


def test_単体_milestoneは期限が直近のものを選ぶ():
    ms = [
        {"n": 1, "t": "期限なし", "due": None},
        {"n": 2, "t": "遠い", "due": "2099-02-01T00:00:00Z"},
        {"n": 3, "t": "直近", "due": "2099-01-05T00:00:00Z"},
    ]
    assert pick_milestone(ms)["n"] == 3


def test_単体_review_gateの取得失敗と未評価を取り違えない():
    assert gate_mark({"s": "success"}) == "🟢"
    assert gate_mark({"s": "failure"}) == "🔴"
    assert gate_mark({"s": "pending"}) == "🟡"
    assert gate_mark({"s": None}) == "❓ (未評価)"  # status がまだ貼られていない
    assert gate_mark(None) == "❓ (未検証)"  # 取得そのものに失敗
