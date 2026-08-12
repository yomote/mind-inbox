"""[単体] プロダクトの現在地 (Issue #280) — 分類と警告の判定を固定する。

無いと何が静かに通るか:
    このセクションは「壊れても例外は出ず、表示が静かに間違う」塊。
    deploy が赤なのに警告が出ない / apps/ を触る PR が「工場」列に紛れる /
    取得失敗が「異常なし」に見える — どれも見た目は普通のページのままなので、
    テストでしか捕まえられない。
"""

from product_status import (
    DEPLOY_STEP,
    FAILURE_COMMENT_MARKER,
    MARKER_STEP,
    TEARDOWN_STEP,
    classify_prs,
    collect,
    dev_state,
    gate_mark,
    next_candidates,
    parse_steps,
    pick_milestone,
    render,
    unrecovered_since,
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


def test_単体_guard_skipの緑で直前のdeploy失敗を隠さない():
    """デプロイ失敗の**次の** push が guard skip で緑になっても赤警告を消さない。

    無いと何が静かに通るか:
        ok を「最新 push の conclusion」で決めると、失敗の翌 push が skip された
        瞬間に ⚠️ と ci-failure Issue へのリンクが消え、古い commit と未反映本数
        だけが並ぶ。**壊れているのに壊れて見えない**表示になる (PR #281 Codex P2)。
    """
    skipped = _run("success", "completed", "2099-01-03T00:00:00Z", rid=70)
    failed = _run("failure", "completed", "2099-01-02T00:00:00Z", rid=71)
    deployed = _run(
        "success", "completed", "2099-01-01T00:00:00Z", sha="feed5678abcd", rid=72
    )
    steps = {
        70: {"deployed": False, "attempted": False, "torn_down": False},  # guard skip
        71: {"deployed": False, "attempted": True, "torn_down": False},  # 試みて失敗
        72: {"deployed": True, "attempted": True, "torn_down": False},
    }
    dev = dev_state(
        [skipped, failed, deployed],
        deploy_issue=262,
        run_steps=lambda r: steps[r["id"]],
    )
    assert dev["ok"] is False, "guard skip の緑を「直近のデプロイ結果」として読んでいる"
    html = render({"dev": dev}, _PEND)
    assert "⚠️" in html
    assert "更新が届いていない" in html
    assert "#262" in html
    assert "feed567" in html  # 載っているのは最後に完走した commit


def test_単体_guard_skipが5本続いても直前の失敗を保持する():
    """skip の緑が予算 (MAX_OK_CHECKS) より多く続いても赤を消さない。

    無いと何が静かに通るか:
        「直近にデプロイを試みた push」を探す走査は API 予算で打ち切るため、
        失敗のあと guard skip の緑が 3 本以上積まれると失敗が窓から外れ、
        ⚠️・deploy 赤・Issue リンクが**もう一度**消える (PR #281 Codex P2-a)。
    """
    skips = [
        _run("success", "completed", f"2099-01-1{i}T00:00:00Z", rid=100 + i)
        for i in (5, 4, 3, 2, 1)  # 新しい順に 5 本
    ]
    failed = _run("failure", "completed", "2099-01-05T00:00:00Z", rid=110)
    deployed = _run(
        "success", "completed", "2099-01-01T00:00:00Z", sha="feed5678abcd", rid=111
    )
    steps = {
        **{100 + i: {"deployed": False, "attempted": False} for i in (5, 4, 3, 2, 1)},
        110: {"deployed": False, "attempted": True},
        111: {"deployed": True, "attempted": True},
    }
    dev = dev_state(
        [*skips, failed, deployed], deploy_issue=262, run_steps=lambda r: steps[r["id"]]
    )
    assert dev["ok"] is False, "skip が続いた分だけ失敗が窓から外れて赤が消えている"
    html = render({"dev": dev}, _PEND)
    assert "⚠️" in html
    assert "#262" in html


def test_単体_失敗runが取得窓から消えてもopen_Issueで赤を保持する():
    """run 一覧 (per_page=30) から失敗が押し出されても ⚠️ を消さない。

    無いと何が静かに通るか:
        guard skip の成功 push が 30 本続くと失敗 run が **runs 一覧そのもの**から
        消え、窓の中だけを見る規則では ok=None に戻る (PR #281 Codex P2-a 再提起)。
        deploy の ci-failure Issue は緑になれば report-failure が自動で閉じる
        (ADR 0035 D2) ので、open のままなら「まだ復旧していない」と言える。
    """
    skips = [
        _run("success", "completed", f"2099-02-{i:02}T00:00:00Z", rid=200 + i)
        for i in range(30, 0, -1)  # 新しい順に 30 本すべて guard skip
    ]
    dev = dev_state(
        skips,
        deploy_issue=262,
        run_steps=lambda r: {"deployed": False, "attempted": False},
        deploy_issue_at="2099-01-15T00:00:00Z",  # 窓の外で起きた失敗の Issue
    )
    assert dev["ok"] is False, "失敗が取得窓から消えると赤が消えている"
    html = render({"dev": dev}, _PEND)
    assert "⚠️" in html
    assert "#262" in html


def test_単体_Issueが古くその後に成功デプロイがあれば赤にしない():
    """閉じ損ねた古い Issue で永久に赤くしない (誤検知を持ち込まない)。"""
    skip = _run("success", "completed", "2099-03-09T00:00:00Z", rid=210)
    deployed = _run("success", "completed", "2099-03-05T00:00:00Z", rid=211)
    steps = {
        210: {"deployed": False, "attempted": False},
        211: {"deployed": True, "attempted": True},
    }
    dev = dev_state(
        [skip, deployed],
        deploy_issue=262,
        run_steps=lambda r: steps[r["id"]],
        deploy_issue_at="2099-03-01T00:00:00Z",  # 成功デプロイより古い
    )
    assert dev["ok"] is not False, "復旧済みなのに古い Issue で赤を出している"
    assert "⚠️" not in render({"dev": dev}, _PEND)


def _collect_issue_at(bot_times, human_times, issue_at="2099-04-01T00:00:00Z"):
    """collect を「open な deploy Issue あり」で走らせ、未復旧の証拠時刻を返す。

    gh_lines の fake は **jq が bot と固定文言で絞っているときだけ** bot の時刻を
    返す (絞っていなければ人間のコメントも混ぜて返す)。こうしておくと、
    フィルタを外す退行がそのままテストの失敗になる。
    """
    seen = []

    def fake_gh(*args):
        url = args[1] if len(args) > 1 else ""
        if "labels=ci-failure" in url:
            return [{"n": 262, "t": "[ci-failure] deploy が落ちている", "at": issue_at}]
        if "/status" in url:
            return {"s": "success"}
        return []

    def fake_gh_lines(*args):
        if "/comments" not in " ".join(args):
            return []
        seen.append(args)
        jq = args[args.index("--jq") + 1]
        assert "--paginate" in args, "コメントを 1 ページ目しか見ていない"
        filtered = '"Bot"' in jq and FAILURE_COMMENT_MARKER in jq
        return list(bot_times) if filtered else [*bot_times, *human_times]

    data = collect(fake_gh, fake_gh_lines, lambda *a: [])
    assert len(seen) == 1, "コメント取得は open な deploy Issue のときだけ 1 回"
    return data


def test_単体_閉じ損ねたIssueでも再発の追記があれば赤を保持する():
    """close の失敗 (`|| true`) で Issue が残ったまま再障害になった場合。

    無いと何が静かに通るか:
        created_at は**初回の障害**で固定される。閉じ損ね → 成功デプロイ →
        再障害の追記、という並びだと「Issue は成功デプロイより古い」と読めてしまい、
        **現に落ちているのに ⚠️ が消える** (PR #281 Codex P2)。
    """
    data = _collect_issue_at(
        bot_times=["2099-04-20T00:00:00Z"],  # 再障害の追記 (成功デプロイより新しい)
        human_times=[],
        issue_at="2099-04-01T00:00:00Z",  # 初回の障害 (成功デプロイより古い)
    )
    deployed = _run("success", "completed", "2099-04-10T00:00:00Z", rid=300)
    dev = dev_state(
        [deployed],
        deploy_issue=262,
        run_steps=lambda r: {"deployed": True, "attempted": True},
        deploy_issue_at=data["dev_issue_at"],
    )
    assert dev["ok"] is False, "再発の追記を見ておらず、赤が消えている"
    assert "⚠️" in render({"dev": dev}, _PEND)


def test_単体_人間のコメントでは未復旧時刻が進まない():
    """人間が古い Issue に一言書いただけで赤が復活しないこと。"""
    data = _collect_issue_at(
        bot_times=[],  # bot の失敗追記は無い (= 初回の障害のまま)
        human_times=["2099-04-20T00:00:00Z"],  # 人間のコメントだけが新しい
        issue_at="2099-04-01T00:00:00Z",
    )
    assert data["dev_issue_at"] == "2099-04-01T00:00:00Z", (
        "人間のコメントで未復旧時刻が進んでいる"
    )
    deployed = _run("success", "completed", "2099-04-10T00:00:00Z", rid=301)
    dev = dev_state(
        [deployed],
        deploy_issue=262,
        run_steps=lambda r: {"deployed": True, "attempted": True},
        deploy_issue_at=data["dev_issue_at"],
    )
    assert dev["ok"] is not False
    assert "⚠️" not in render({"dev": dev}, _PEND)


def test_単体_追記の無いIssueは作成時刻にフォールバックする():
    assert unrecovered_since("2099-01-01T00:00:00Z", []) == "2099-01-01T00:00:00Z"
    assert unrecovered_since("2099-01-01T00:00:00Z", None) == "2099-01-01T00:00:00Z"
    # 取得できた追記のうち **最新** を採る (順不同で来ても)
    assert (
        unrecovered_since(
            "2099-01-01T00:00:00Z",
            ["2099-01-05T00:00:00Z", "2099-01-09T00:00:00Z", "2099-01-07T00:00:00Z"],
        )
        == "2099-01-09T00:00:00Z"
    )


def test_単体_成功デプロイより古い失敗は赤にしない():
    """赤の保持は「その後デプロイできていない」ときだけ (誤検知を持ち込まない)。"""
    skip = _run("success", "completed", "2099-01-09T00:00:00Z", rid=120)
    deployed = _run("success", "completed", "2099-01-05T00:00:00Z", rid=121)
    old_fail = _run("failure", "completed", "2099-01-01T00:00:00Z", rid=122)
    steps = {
        120: {"deployed": False, "attempted": False},
        121: {"deployed": True, "attempted": True},
        122: {"deployed": False, "attempted": True},
    }
    dev = dev_state([skip, deployed, old_fail], run_steps=lambda r: steps[r["id"]])
    assert dev["ok"] is not False, "復旧済みの古い失敗で赤を出している"
    assert "⚠️" not in render({"dev": dev}, _PEND)


def test_単体_Provision前に落ちた失敗も試行済みとして扱う():
    """Azure login / IMAGE_TAG 解決で落ちると Provision は skipped になる。

    無いと何が静かに通るか:
        DEPLOY_STEP だけで「試みたか」を見ると、この失敗は「そもそも走らなかった」
        と同じ扱いになり、さらに古い成功 run が選ばれて ok=True。**deploy は赤で
        Issue も立っているのに、状況ページだけ緑**になる (PR #281 Codex P2-b)。
        marker (guard 通過直後の no-op) が痕跡を残すので区別できる。
    """
    info = parse_steps(
        [
            {"n": MARKER_STEP, "c": "success"},  # 経路には入った
            {"n": "Azure login (OIDC, no stored secret)", "c": "failure"},
            {"n": DEPLOY_STEP, "c": "skipped"},  # ここまで来ていない
            {"n": TEARDOWN_STEP, "c": "skipped"},
        ]
    )
    assert info == {"deployed": False, "attempted": True, "torn_down": False}

    broken = _run("failure", "completed", "2099-01-09T00:00:00Z", rid=130)
    old_ok = _run("success", "completed", "2099-01-01T00:00:00Z", rid=131)
    dev = dev_state(
        [broken, old_ok],
        deploy_issue=262,
        run_steps=lambda r: (
            info if r["id"] == 130 else {"deployed": True, "attempted": True}
        ),
    )
    assert dev["ok"] is False
    html = render({"dev": dev}, _PEND)
    assert "⚠️" in html
    assert "#262" in html


def test_単体_marker無しの過去runはDEPLOY_STEPで試行を判定する():
    """marker を足す前の run が「試みていない」に化けないこと (後方互換)。"""
    assert parse_steps([{"n": DEPLOY_STEP, "c": "failure"}])["attempted"] is True
    assert parse_steps([{"n": DEPLOY_STEP, "c": "success"}])["deployed"] is True
    # guard skip: marker も DEPLOY_STEP も走っていない
    assert parse_steps([{"n": DEPLOY_STEP, "c": "skipped"}])["attempted"] is False
    assert parse_steps("not a list") is None  # 取得失敗は None (未検証)


def test_単体_デプロイを試みたpushが見つからないときは緑とも赤とも書かない():
    """guard skip だけが続く区間では ok を断定しない (未検証側に倒す)。"""
    runs = [
        _run("success", "completed", f"2099-01-0{i}T00:00:00Z", rid=80 + i)
        for i in (3, 2, 1)
    ]
    dev = dev_state(runs, run_steps=lambda r: {"deployed": False, "attempted": False})
    assert dev["ok"] is None
    html = render({"dev": dev}, _PEND)
    assert "実デプロイの痕跡がありません" in html
    assert "⚠️" not in html
    assert "反映済み" not in html


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
        if "/status" in url:
            return {"s": "success"}
        return None

    calls = []

    def fake_gh_lines(*args):
        calls.append(args)
        assert "--paginate" in args, "--paginate 無しでは 1 ページ目しか取れない"
        return files_2pages

    def fake_gh_objects(*args):
        assert "--paginate" in args
        return [{"n": 1, "t": "大きい PR", "sha": "abc", "draft": False}]

    data = collect(fake_gh, fake_gh_lines, fake_gh_objects)
    assert any("/pulls/1/files" in a for call in calls for a in call)
    product, factory, _ = classify_prs(data["prs"])
    assert [p["n"] for p in product] == [1], (
        "2 ページ目の apps/ が工場に誤分類されている"
    )
    assert factory == []


def test_単体_open_PR一覧も全ページ取得し51件目が進行中に出る():
    """一覧そのものが 1 ページで切れると、51 件目以降は分類以前に「進行中」から
    静かに消える (PR #281 Codex P2)。paginate 版で取ることを呼び方ごと固定する。"""
    many = [
        {"n": i, "t": f"PR {i}", "sha": f"sha{i}", "draft": False}
        for i in range(1, 52)  # 51 件
    ]
    seen = []

    def fake_gh(*args):
        url = args[1] if len(args) > 1 else ""
        if "/status" in url:
            return {"s": "success"}
        return []

    def fake_gh_objects(*args):
        seen.append(args)
        assert "--paginate" in args, "open PR の一覧を --paginate 無しで取っている"
        return many

    data = collect(fake_gh, lambda *a: [], fake_gh_objects)
    assert any("base=main" in a for call in seen for a in call)
    assert len(data["prs"]) == 51
    html = render(data, _PEND)
    assert "/pull/51" in html, "51 件目の PR が「進行中」から消えている"


def test_単体_全PR一覧の取得失敗はmain向け以外不明と明示する():
    """base=main の取得だけ成功し全 PR 一覧が失敗すると、脚注が黙って消えて
    「main 向け以外の PR は無い」ように見える (PR #281 Codex P2)。"""
    html = render(
        {"dev": {"fetched": False}, "prs": []},
        {"needs_human": [], "prs": None, "proposed": []},  # 全 PR 一覧のみ失敗
    )
    assert "main 向け以外の PR の有無は不明" in html
    assert "未検証: 全 open PR" in html
