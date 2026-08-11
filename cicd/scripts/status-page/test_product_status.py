"""[単体] プロダクトの現在地 (Issue #280) — 分類と警告の判定を固定する。

無いと何が静かに通るか:
    このセクションは「壊れても例外は出ず、表示が静かに間違う」塊。
    deploy が赤なのに警告が出ない / apps/ を触る PR が「工場」列に紛れる /
    取得失敗が「異常なし」に見える — どれも見た目は普通のページのままなので、
    テストでしか捕まえられない。
"""

from product_status import (
    classify_prs,
    collect,
    dev_state,
    gate_mark,
    next_candidates,
    pick_milestone,
    render,
)

_PEND = {"needs_human": [], "prs": [], "proposed": []}

_SEQ = iter(range(1, 10_000))


def _run(c, s, t, e="push", sha="abc1234def5678", rid=None):
    return {
        "id": rid if rid is not None else next(_SEQ),
        "c": c,
        "s": s,
        "t": t,
        "e": e,
        "sha": sha,
        "u": "https://example.test/r",
    }


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


def test_単体_手動downのrunが最新でも反映済みにしない():
    """workflow_dispatch の down は run success でも「デプロイ」ではなく「撤収」。

    conclusion だけ見ると、dev を消した直後に「最新の main が反映済み」と出る
    (PR #281 Codex P2)。steps の痕跡で撤収を識別し、撤収済みと明示する。
    """
    down = _run(
        "success", "completed", "2099-01-05T00:00:00Z", e="workflow_dispatch", rid=50
    )
    old = _run("success", "completed", "2099-01-01T00:00:00Z", rid=51)
    steps = {
        50: {"deployed": False, "torn_down": True},
        51: {"deployed": True, "torn_down": False},
    }
    dev = dev_state([down, old], run_steps=lambda r: steps[r["id"]])
    assert dev["down"] is not None
    html = render({"dev": dev}, _PEND)
    assert "撤収されています" in html
    assert "反映済み" not in html


def test_単体_dispatchのrunはrun_steps無しでもデプロイに数えない():
    """runs API では dispatch の up/down が区別できない — steps を確認できない
    ときは push の成功 run だけをデプロイとみなす (最小の近似)。"""
    dispatch = _run(
        "success", "completed", "2099-01-05T00:00:00Z", e="workflow_dispatch"
    )
    pushed = _run("success", "completed", "2099-01-01T00:00:00Z", sha="feed5678abcd")
    dev = dev_state([dispatch, pushed])
    assert dev["last_success"]["sha"] == "feed5678abcd"  # dispatch を anchor にしない


def test_単体_guard_skipの成功runを反映済みと数えない():
    """push の成功 run でも guard skip (自動デプロイ未解禁等) なら dev は進んでいない。"""
    skip = _run("success", "completed", "2099-01-05T00:00:00Z", rid=60)
    real = _run(
        "success", "completed", "2099-01-01T00:00:00Z", sha="feed5678abcd", rid=61
    )
    steps = {
        60: {"deployed": False, "torn_down": False},
        61: {"deployed": True, "torn_down": False},
    }
    dev = dev_state([skip, real], run_steps=lambda r: steps[r["id"]])
    assert dev["last_success"]["sha"] == "feed5678abcd"
    assert dev["behind"] == 1  # skip された push はまだ dev に届いていない
    html = render({"dev": dev}, _PEND)
    assert "最新の main が反映済み" not in html
    assert "以降 1 本のマージが未反映" in html


def test_単体_実デプロイの痕跡確認に失敗したら未検証と書く():
    runs = [_run("success", "completed", "2099-01-01T00:00:00Z")]
    dev = dev_state(runs, run_steps=lambda r: None)  # jobs API 取得失敗
    assert dev["verify_failed"]
    html = render({"dev": dev}, _PEND)
    assert "未検証" in html
    assert "反映済み" not in html


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


def test_単体_PRのfilesは全ページ取得しappsが2ページ目でもプロダクト分類():
    """1 ページ目 (100 件) だけ見ると apps/ が 101 件目以降のとき「工場」に
    静かに誤分類される (PR #281 Codex P2)。--paginate の 1 行 1 値取得で
    全ページを結合していることを collect の呼び方ごと固定する。"""
    # 2 ページ相当: apps/ は 121 件目 (1 ページ目に載らない位置)
    files_2pages = [f"docs/f{i:03}.md" for i in range(120)] + ["apps/bff/src/x.ts"]

    def fake_gh(*args):
        url = args[1] if len(args) > 1 else ""
        if "/milestones?" in url:
            return []
        if "/runs" in url:
            return []
        if "labels=" in url:
            return []
        if "base=main" in url:
            return [{"n": 1, "t": "大きい PR", "sha": "abc", "draft": False}]
        if "/status" in url:
            return {"s": "success"}
        return None

    calls = []

    def fake_gh_lines(*args):
        calls.append(args)
        assert "--paginate" in args, "--paginate 無しでは 1 ページ目しか取れない"
        return files_2pages

    data = collect(fake_gh, fake_gh_lines)
    assert any("/pulls/1/files" in a for call in calls for a in call)
    product, factory, _ = classify_prs(data["prs"])
    assert [p["n"] for p in product] == [1], (
        "2 ページ目の apps/ が工場に誤分類されている"
    )
    assert factory == []


def test_単体_全PR一覧の取得失敗はmain向け以外不明と明示する():
    """base=main の取得だけ成功し全 PR 一覧が失敗すると、脚注が黙って消えて
    「main 向け以外の PR は無い」ように見える (PR #281 Codex P2)。"""
    html = render(
        {"dev": {"fetched": False}, "prs": []},
        {"needs_human": [], "prs": None, "proposed": []},  # 全 PR 一覧のみ失敗
    )
    assert "main 向け以外の PR の有無は不明" in html
    assert "未検証: 全 open PR" in html
