"""review-gate の判定ロジックのテスト (ADR 0036)。

無いと何が静かに通るか: SHA 照合や Codex 対象判定のバグは「門が開きっぱなし」を
意味するが、門は開いていても誰も気づかない (マージは普通に成功する) ため、
判定ロジックの退行はテストでしか捕まえられない。
"""

import subprocess
from datetime import datetime as real_datetime
from datetime import timezone

import check
from check import (
    CODEX_RETRIGGER_MARKER,
    HUMAN_STALL_MARKER,
    MERGE_STALL_HOURS,
    MERGE_STALL_MARKER,
    all_check_runs_green,
    codex_present_in,
    deploy_gate_anchor,
    earliest_iso,
    decide,
    has_pm_accept,
    human_queue_issues,
    is_bot_login,
    is_code_pr,
    is_codex_review_result,
    is_proposed_adr,
    is_stale,
    latest_iso,
    merge_failure_reason,
    minutes_between,
    needs_image_build,
    page_exhausts_lookback,
    parse_gh_json,
    recently_merged,
    review_gate_success_at,
    runs_since,
    sensitive_paths,
    should_execute_merge,
    should_notify_again,
    should_notify_human_stall,
    should_request_security_review,
    should_retrigger_codex,
    still_unposted,
    sweep_targets,
)

HEAD = "abc1234def5678"
# (本文, author_association) の組で渡す。association は GitHub が付ける値で、
# 本文からは詐称できない。
ACCEPT_OK = ("[pm-accept] abc1234 受け入れ確認: 意図どおり", "OWNER")


def test_l1_受け入れコメントが無いと赤() -> None:
    v = decide(HEAD, ["docs/x.md"], [], 0, False, False)
    assert not v.ok
    assert any("pm-accept" in m for m in v.missing)


def test_l1_古いshaの受け入れは押し流される() -> None:
    # push 前の SHA で受け入れたコメントは、新しい head では無効 (リセットの本体)
    stale = ("[pm-accept] 9999999 これは前のコミットへの受け入れ", "OWNER")
    assert not has_pm_accept([stale], HEAD)
    assert not decide(HEAD, [], [stale], 0, False, False).ok


def test_l1_マーカーとshaが揃えば緑() -> None:
    v = decide(HEAD, ["docs/x.md"], [ACCEPT_OK], 0, False, False)
    assert v.ok
    assert v.missing == []


def test_l1_マーカーだけshaだけでは受け入れにならない() -> None:
    assert not has_pm_accept([("[pm-accept] だけで SHA なし", "OWNER")], HEAD)
    assert not has_pm_accept([(f"SHA {HEAD[:7]} はあるがマーカーなし", "OWNER")], HEAD)


def test_l1_未解決スレッドがあると赤() -> None:
    v = decide(HEAD, [], [ACCEPT_OK], 2, False, False)
    assert not v.ok
    assert any("2 件" in m for m in v.missing)


def test_l1_コード判定はappsとcicdのみ() -> None:
    assert is_code_pr(["apps/bff/src/x.ts"])
    assert is_code_pr(["cicd/scripts/deploy/x.sh"])
    assert not is_code_pr(["docs/adr/0036.md", ".github/workflows/x.yml"])


def test_l1_コードprはcodex必須_docsは不要() -> None:
    code, docs = ["apps/bff/x.ts"], ["docs/x.md"]
    assert not decide(HEAD, code, [ACCEPT_OK], 0, False, True).ok
    assert decide(HEAD, code, [ACCEPT_OK], 0, True, True).ok
    assert decide(HEAD, docs, [ACCEPT_OK], 0, False, True).ok


def test_l1_codexフラグが切のときは要求しない() -> None:
    # REVIEW_GATE_REQUIRE_CODEX 未設定 (= #205 の有効化前) は advisory に留める
    assert decide(HEAD, ["apps/bff/x.ts"], [ACCEPT_OK], 0, False, False).ok


def test_l1_説明文は140字に収まる() -> None:
    v = decide(HEAD, ["apps/bff/x.ts"] * 3, [], 5, False, True)
    assert len(v.description) <= 140


def test_l1_第三者のコメントは受け入れにならない() -> None:
    """public リポジトリなので誰でも PR にコメントできる。

    無いと何が静かに通るか:
        通りすがりのアカウントが `[pm-accept] <sha>` と書くだけで門が緑になり、
        エージェントの常設承認と組み合わさると **レビューされていない PR が
        マージされる**。門が開いていること自体は誰の目にも留まらない。
    """
    body = "[pm-accept] abc1234 いいと思います"
    assert not has_pm_accept([(body, "NONE")], HEAD)
    assert not has_pm_accept([(body, "CONTRIBUTOR")], HEAD)
    assert not has_pm_accept([(body, "FIRST_TIME_CONTRIBUTOR")], HEAD)
    assert not decide(HEAD, ["apps/x.ts"], [(body, "NONE")], 0, False, False).ok


def test_l1_権限保持者の受け入れは通る() -> None:
    body = "[pm-accept] abc1234 受け入れます"
    for association in ("OWNER", "MEMBER", "COLLABORATOR", "owner"):
        assert has_pm_accept([(body, association)], HEAD), association


# ---- Codex 自動再トリガー / 敏感パス指名 (ADR 0038) ----


def _retrigger(**overrides) -> bool:
    """全条件が揃った既定値からの差分で書く (どの条件が効いたかをテスト名で示す)。"""
    kwargs = dict(
        code_pr=True,
        draft=False,
        codex_present=False,
        marker_posted=False,
        minutes_since_last_push=30.0,
        threshold_minutes=10.0,
    )
    kwargs.update(overrides)
    return should_retrigger_codex(**kwargs)


def test_l1_再トリガーは全条件が揃ったときだけ() -> None:
    """無いと何が静かに通るか: 条件判定の退行は「毎 run 投稿するノイズ」か
    「一度も投稿しない沈黙」のどちらかに倒れ、どちらも PR 上では気づけない。"""
    assert _retrigger()
    assert not _retrigger(code_pr=False), "docs PR に投稿するのは枠の無駄"
    assert not _retrigger(draft=True), "draft はレビュー対象前"
    assert not _retrigger(codex_present=True), "既着なら再トリガー不要"
    assert not _retrigger(marker_posted=True), "2 重投稿防止 (冪等性の本体)"
    assert not _retrigger(minutes_since_last_push=9.9), "猶予内は Codex 自身を待つ"
    assert _retrigger(minutes_since_last_push=10.0), "既定 10 分ちょうどで発火"


def test_l1_再トリガーのマーカーは一度きりでpushではリセットされない() -> None:
    # marker_posted はコメント全量から探す前提 — push で吠え直す設計にすると
    # 「沈黙の原因が機構の外」のとき毎 push ノイズになる
    assert not _retrigger(marker_posted=True, minutes_since_last_push=9999.0)


def test_l1_敏感パスの判定() -> None:
    """無いと何が静かに通るか: IaC / CI 定義 / 認証まわりの変更が security review の
    自動指名を素通りし、「敏感な変更ほど誰も見ていない」が続く (ADR 0038 の動機)。"""
    assert sensitive_paths(["cicd/iac/main-bootstrap.bicep"]) == [
        "cicd/iac/main-bootstrap.bicep"
    ]
    assert sensitive_paths([".github/workflows/deploy.yml"])
    assert sensitive_paths([".github/actions/report-failure/action.yml"])
    assert sensitive_paths(["apps/bff/local.settings.json"])
    assert sensitive_paths(["apps/bff/local.settings.json.example"])
    # apps/bff/src/** は認証・トークン・CORS の匂いがあるパスだけ
    assert sensitive_paths(["apps/bff/src/auth/entra.ts"])
    assert sensitive_paths(["apps/bff/src/functions/speechToken.ts"])
    assert sensitive_paths(["apps/bff/src/cors.ts"])
    assert not sensitive_paths(["apps/bff/src/trpc/domain.ts"])
    # 対象外のもの
    assert not sensitive_paths(["docs/adr/0038-x.md", "apps/frontend/src/App.tsx"])
    # 混在なら当たったものだけ返す
    assert sensitive_paths(["docs/x.md", "cicd/iac/x.bicep"]) == ["cicd/iac/x.bicep"]


def test_l1_security指名は敏感パスありかつ未投稿のときだけ() -> None:
    assert should_request_security_review(
        draft=False, sensitive=["cicd/iac/x.bicep"], marker_posted=False
    )
    assert not should_request_security_review(
        draft=False, sensitive=[], marker_posted=False
    )
    assert not should_request_security_review(
        draft=False, sensitive=["cicd/iac/x.bicep"], marker_posted=True
    ), "2 重投稿防止"
    assert not should_request_security_review(
        draft=True, sensitive=["cicd/iac/x.bicep"], marker_posted=False
    )


def test_l1_敏感パスはadvisoryで合否には入らない() -> None:
    """ADR 0038 の要: 門を重くしない。敏感パス PR でも decide() の合否は変わらない。"""
    v = decide(HEAD, ["cicd/iac/x.bicep"], [ACCEPT_OK], 0, True, True)
    assert v.ok


def test_l1_経過分の計算はISO8601のZ表記を読める() -> None:
    assert minutes_between("2026-08-11T03:00:00Z", "2026-08-11T03:30:00Z") == 30.0
    assert minutes_between("2026-08-11T03:00:00Z", "2026-08-11T03:00:00Z") == 0.0


def test_l1_sweepの対象はopenかつbase_mainかつ非draftのみ() -> None:
    """PR #238 P1 対応 (schedule sweep) の対象選定。

    無いと何が静かに通るか: 選定の退行は「閉じた/対象外 PR へ 30 分毎に API を
    叩き続ける」か「open PR が sweep から漏れて advisory が永遠に発火しない」
    (= P1 が直っていない) のどちらかに倒れ、どちらも run は緑のまま。
    """

    def pr(**overrides) -> dict:
        base = {"number": 1, "state": "open", "draft": False, "base": {"ref": "main"}}
        base.update(overrides)
        return base

    target = pr()
    assert sweep_targets([target]) == [target]
    assert sweep_targets([pr(state="closed")]) == []
    assert sweep_targets([pr(draft=True)]) == []
    assert sweep_targets([pr(base={"ref": "release"})]) == []
    assert sweep_targets([pr(base=None)]) == []  # base 欠落を落とさず対象外に
    # 混在なら対象だけ残る
    assert sweep_targets([pr(state="closed"), target, pr(draft=True)]) == [target]


def test_l1_投稿直前の再フェッチ確認はマーカーを見つけたら投稿させない() -> None:
    """PR #238 P2 対応 (2 重投稿レース) の判定部。

    無いと何が静かに通るか: 近接した 2 run (または schedule sweep と event run) が
    両方「未投稿」と観測して同じ advisory を 2 本投稿する。マーカーは部分文字列
    一致なので、引用・返信に埋まっていても検出できることも固定する。
    """
    assert still_unposted(CODEX_RETRIGGER_MARKER, [])
    assert still_unposted(CODEX_RETRIGGER_MARKER, ["普通のコメント", "[pm-accept] abc"])
    assert not still_unposted(
        CODEX_RETRIGGER_MARKER, ["x", f"{CODEX_RETRIGGER_MARKER}\n⏳ 未着です"]
    )
    # 本文の途中 (引用等) にあっても「投稿済み」と数える — 吠え直すよりノイズ回避を優先
    assert not still_unposted(
        CODEX_RETRIGGER_MARKER, [f"引用: {CODEX_RETRIGGER_MARKER} を見た"]
    )


# Codex bot が issue コメントとして投稿する実在 3 パターン (PR #238 / #239 で実測)
CODEX_LOGIN = "chatgpt-codex-connector[bot]"
CODEX_CLEAN_REVIEW = "**Codex Review**: Didn't find any major issues. Nice work!"
CODEX_ACCOUNT_GUIDE = (
    "To use Codex here, create a Codex account and connect it to your GitHub account."
)
CODEX_ERROR_REPLY = "Codex couldn't complete this request. Try again later."


def test_l1_指摘ゼロのclean_reviewはissueコメントの本文で既着と数える() -> None:
    """PR #239 実測: Codex は指摘ゼロのとき review オブジェクトを作らず、
    issue コメント (Codex Review ヘッダ) だけを残す。

    無いと何が静かに通るか:
        clean な PR ほど「Codex レビューが無い」と判定され**門が永遠に赤のまま**
        (PR #239 / #246 で実発生)。逆側の退行 (定型応答を既着と数える) は
        下のテストが固定する。
    """
    assert is_codex_review_result(CODEX_CLEAN_REVIEW)
    assert codex_present_in([], [(CODEX_LOGIN, CODEX_CLEAN_REVIEW)], "codex")


def test_l1_同botの非レビュー定型応答は既着と数えない() -> None:
    """アカウント案内・エラー応答を既着と数えると、Codex が実際には
    レビューしていないのに再トリガー依頼 (advisory) が発火しなくなる。"""
    assert not is_codex_review_result(CODEX_ACCOUNT_GUIDE)
    assert not is_codex_review_result(CODEX_ERROR_REPLY)
    assert not codex_present_in(
        [],
        [(CODEX_LOGIN, CODEX_ACCOUNT_GUIDE), (CODEX_LOGIN, CODEX_ERROR_REPLY)],
        "codex",
    )


def test_l1_codex既着の判定はloginでも絞る() -> None:
    # review オブジェクト / レビューコメントは存在自体が証拠 (従来どおり)
    assert codex_present_in([CODEX_LOGIN], [], "codex")
    assert not codex_present_in(["yomote"], [], "codex")
    # issue コメントは本文が review 結果でも、投稿者が Codex でなければ数えない
    # (advisory の依頼文や人間の引用が「既着」に化けるのを防ぐ)
    assert not codex_present_in(
        [], [("github-actions[bot]", "`@codex review` を投稿してください")], "codex"
    )
    assert not codex_present_in([], [("yomote", CODEX_CLEAN_REVIEW)], "codex")


# ---- マージ執行 / ストール検知 (ADR 0040 D1 / Issue #253) ----


def _mergeable_pr(**overrides) -> dict:
    """マージ執行の全条件が揃った既定値からの差分で書く (advisory のテストと同じ流儀)。"""
    pr = {
        "number": 1,
        "state": "open",
        "draft": False,
        "merged": False,
        "merged_at": None,
        "base": {"ref": "main"},
        "auto_merge": {"merge_method": "squash"},
        "labels": [],
    }
    pr.update(overrides)
    return pr


def test_l1_マージ執行はauto_merge武装済みのprだけ() -> None:
    """無いと何が静かに通るか: 対象判定の退行は「PM が受け入れていない PR を
    機械が勝手にマージする」方向に倒れうる。マージ自体は成功してしまうので、
    誰も気づかないまま未受け入れコードが main に入る (ADR 0040 D1 の設計の要)。"""
    ok, _ = should_execute_merge(_mergeable_pr())
    assert ok
    assert not should_execute_merge(_mergeable_pr(auto_merge=None))[0], (
        "auto-merge 未武装 = PM の受け入れ意思表示が無い — 対象を広げない"
    )


def test_l1_リリースprとneeds_human_prはマージ執行しない() -> None:
    """無いと何が静かに通るか: リリース PR (main → release) は「judge 🟢 でも
    merge は人間」(ADR 0019)、needs-human は「必ず人間が押す」(CLAUDE.md の
    常設承認の例外)。ここが緩むと人間の門が機械に破られる。"""
    ok, reason = should_execute_merge(_mergeable_pr(base={"ref": "release"}))
    assert not ok and "main" in reason
    ok, reason = should_execute_merge(_mergeable_pr(labels=[{"name": "needs-human"}]))
    assert not ok and "needs-human" in reason
    # 無関係なラベルは止めない
    assert should_execute_merge(_mergeable_pr(labels=[{"name": "bug"}]))[0]


def test_l1_closed_merged_draftはマージ執行しない() -> None:
    assert not should_execute_merge(_mergeable_pr(state="closed"))[0]
    assert not should_execute_merge(_mergeable_pr(merged=True))[0]
    # sweep は list API (merged ブール無し / merged_at のみ) の PR を渡す
    assert not should_execute_merge(_mergeable_pr(merged_at="2026-08-11T00:00:00Z"))[0]
    assert not should_execute_merge(_mergeable_pr(draft=True))[0]


def test_l1_マージ直前の再取得で保留条件を再評価する(monkeypatch) -> None:
    """TOCTOU (Codex P1 / PR #258) の再確認を I/O 関数の構造で固定する。

    無いと何が静かに通るか: run 冒頭のスナップショット判定と merge API 呼び出しの
    間 (コメント・レビュー取得を挟む) に PM が auto-merge を解除 / needs-human を
    付けても、古い判定のままマージされる。merge API が強制するのは sha= (head
    変更) だけで、本 workflow 固有の保留条件は強制しない — try_merge 自身が
    PR を取り直して should_execute_merge を再評価しなければ塞がらない。
    """
    put_calls: list[tuple] = []

    def gh_returning(fresh_pr: dict):
        def fake_gh(*args):
            if "-X" in args:  # PUT merge
                put_calls.append(args)
                return {}
            return fresh_pr  # 直前再確認の GET pulls/N

        return fake_gh

    head = {"sha": "abc1234def5678"}
    # 再取得で auto-merge が外れていた → マージしない
    monkeypatch.setattr(
        check, "gh", gh_returning(_mergeable_pr(auto_merge=None, head=head))
    )
    merged, reason = check.try_merge("o/r", 1, head["sha"])
    assert not merged and not put_calls and "auto-merge" in reason
    # 再取得で needs-human が付いていた → マージしない
    monkeypatch.setattr(
        check,
        "gh",
        gh_returning(_mergeable_pr(labels=[{"name": "needs-human"}], head=head)),
    )
    merged, reason = check.try_merge("o/r", 1, head["sha"])
    assert not merged and not put_calls and "needs-human" in reason
    # 再取得で head が動いていた → マージしない (見ていないコミットをマージしない)
    monkeypatch.setattr(
        check, "gh", gh_returning(_mergeable_pr(head={"sha": "zzz99990000"}))
    )
    merged, reason = check.try_merge("o/r", 1, head["sha"])
    assert not merged and not put_calls and "head" in reason
    # 保留条件が保たれていれば PUT が 1 回だけ飛ぶ
    monkeypatch.setattr(check, "gh", gh_returning(_mergeable_pr(head=head)))
    merged, _ = check.try_merge("o/r", 1, head["sha"])
    assert merged and len(put_calls) == 1


def test_l1_マージ直前の再取得に失敗したらマージしない(monkeypatch) -> None:
    """無いと何が静かに通るか: 再取得の失敗を「保留条件は変わっていない」と
    読み替えると、確認できていない状態でマージが飛ぶ (「取れなかったものを
    合格と書かない」規律の破れ)。"""

    def failing_gh(*args):
        raise subprocess.CalledProcessError(1, list(args), stderr="HTTP 500")

    monkeypatch.setattr(check, "gh", failing_gh)
    merged, reason = check.try_merge("o/r", 1, "abc1234")
    assert not merged and "再取得に失敗" in reason


def test_l1_sweepはpm_acceptが消えたprをマージしない(monkeypatch) -> None:
    """Codex P1 (PR #258): gate が success を貼った後に [pm-accept] コメントが
    削除されても issue_comment.deleted は購読外で status は緑のまま残る。

    無いと何が静かに通るか: sweep が保存済み status だけを信じて try_merge を
    呼ぶと、**PM 受け入れの無い PR が次の schedule でマージされる** — 門の
    3 条件のうちコメント由来のものが、撤回後も機械には見え続ける。
    """
    posted_status: list[str] = []

    def gate_closed(repo, number, head_sha):
        return check.GateEval(
            verdict=check.Verdict(ok=False, missing=["PM 受け入れが無い"]),
            changed_paths=["docs/x.md"],
            comment_pairs=[],
            code_pr=False,
            codex_present=False,
        )

    def must_not_merge(*args):
        raise AssertionError("門が閉じているのにマージ API が呼ばれた")

    monkeypatch.setattr(check, "evaluate_gate", gate_closed)
    monkeypatch.setattr(check, "try_merge", must_not_merge)
    monkeypatch.setattr(
        check, "post_status", lambda repo, sha, state, desc: posted_status.append(state)
    )
    merged = check.reverify_and_merge("o/r", 1, "abc1234")
    assert not merged
    assert posted_status == ["failure"], "保存済み success を failure に訂正する"
    # 再評価が通れば try_merge → followup に進む
    followups: list[list[str]] = []
    monkeypatch.setattr(
        check,
        "evaluate_gate",
        lambda repo, number, head_sha: check.GateEval(
            verdict=check.Verdict(ok=True),
            changed_paths=["docs/x.md"],
            comment_pairs=[],
            code_pr=False,
            codex_present=False,
        ),
    )
    monkeypatch.setattr(check, "try_merge", lambda *a: (True, ""))
    monkeypatch.setattr(
        check,
        "ensure_merge_followup",
        lambda repo, merged_at, paths: followups.append(paths),
    )
    merged = check.reverify_and_merge("o/r", 1, "abc1234")
    assert merged and followups == [["docs/x.md"]]


def test_l1_補償の基準時刻はマージ成功の後に取る(monkeypatch) -> None:
    """Codex P1 (PR #258): sweep が門の再評価より前に取った時刻を merged_at に
    使うと、再評価の API 数往復の間に**別 PR** の build run が開始された場合、
    runs_since がその run (この PR の変更を含まない) を「補償済み」と誤認する。

    無いと何が静かに通るか: この PR の image build が永久に dispatch されず、
    deploy は古い image を正常に載せ続ける (smoke も通る静かな劣化 — 本 PR の
    P1 初回指摘と同じ失敗モードが、時刻の取り方だけで再発する)。
    """
    calls: list[str] = []

    class FakeDateTime:
        @staticmethod
        def now(tz=None):
            calls.append("now")
            return real_datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(check, "datetime", FakeDateTime)
    monkeypatch.setattr(
        check,
        "evaluate_gate",
        lambda repo, number, head_sha: check.GateEval(
            verdict=check.Verdict(ok=True),
            changed_paths=["apps/services/ai-agent/x.py"],
            comment_pairs=[],
            code_pr=True,
            codex_present=True,
        ),
    )
    monkeypatch.setattr(
        check, "try_merge", lambda *a: calls.append("merge") or (True, "")
    )
    captured: list[str] = []
    monkeypatch.setattr(
        check,
        "ensure_merge_followup",
        lambda repo, merged_at, paths: captured.append(merged_at),
    )
    assert check.reverify_and_merge("o/r", 1, "abc1234")
    assert captured, "マージ成功後に followup が呼ばれる"
    assert "now" in calls and "merge" in calls
    assert calls.index("now") > calls.index("merge"), (
        "基準時刻 (now) の取得はマージ成功より後 — 再評価前に取った時刻を使わない"
    )


def test_l1_sweepは1件のpr失敗で全体を中断しない(monkeypatch) -> None:
    """Codex P2 (PR #258): PR ループに例外の隔離が無いと、1 つの PR で API が
    失敗し続けるだけで sweep がそこで毎回終了する。

    無いと何が静かに通るか: 後続の sweep_merged_followups / sweep_human_queue が
    毎回スキップされ、その間に補償対象が 24h lookback を外れて build/deploy の
    欠落が**永久に残る** (マージ済み PR 側には何の異常も見えない)。逆に失敗を
    握りつぶして exit 0 にすると、壊れた sweep が緑のまま回り続ける。
    """
    processed: list[int] = []
    phases: list[str] = []

    def fake_gh(*args):
        joined = " ".join(args)
        if "pulls?state=open" in joined:
            return [
                {"number": 1, "state": "open", "draft": False, "base": {"ref": "main"}},
                {"number": 2, "state": "open", "draft": False, "base": {"ref": "main"}},
            ]
        raise AssertionError(f"想定外の gh 呼び出し: {joined}")

    def fake_sweep_one_pr(repo, pr, now_iso):
        if pr["number"] == 1:
            raise subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 500")
        processed.append(pr["number"])

    monkeypatch.setattr(check, "gh", fake_gh)
    monkeypatch.setattr(check, "sweep_one_pr", fake_sweep_one_pr)
    monkeypatch.setattr(
        check,
        "sweep_merged_followups",
        lambda repo, now_iso: phases.append("followups") or True,
    )
    monkeypatch.setattr(
        check, "sweep_human_queue", lambda repo, now_iso: phases.append("human")
    )
    exit_code = check.run_advisory_sweep("o/r")
    assert processed == [2], "1 件目の失敗後も 2 件目は処理される"
    assert phases == ["followups", "human"], "後続フェーズは必ず実行される"
    assert exit_code != 0, "失敗は握りつぶさず run を赤にする"
    # 全部成功なら exit 0 (失敗フラグの誤爆で常時赤にならないことも固定)
    monkeypatch.setattr(check, "sweep_one_pr", lambda repo, pr, now_iso: None)
    assert check.run_advisory_sweep("o/r") == 0


def test_l1_sweepは一覧取得の失敗でも後続フェーズを実行する(monkeypatch) -> None:
    """Codex P2 (PR #258): open PR 一覧の取得 (入口 1 行) が raise すると、
    PR 単位の隔離にも failures の集計にも到達せず関数ごと終了していた。

    無いと何が静かに通るか: GitHub API の一過性障害が一覧取得に当たるたび、
    followups / human queue が丸ごとスキップされる — 障害が続くと補償対象が
    24h lookback を外れ、build/deploy の欠落が永久に残る (隔離したはずの
    中断経路が入口だけ残っている)。
    """
    phases: list[str] = []

    def failing_gh(*args):
        raise subprocess.TimeoutExpired(["gh"], 60)

    monkeypatch.setattr(check, "gh", failing_gh)
    monkeypatch.setattr(
        check,
        "sweep_one_pr",
        lambda repo, pr, now_iso: (_ for _ in ()).throw(
            AssertionError("一覧が取れていないのに PR 処理が呼ばれた")
        ),
    )
    monkeypatch.setattr(
        check,
        "sweep_merged_followups",
        lambda repo, now_iso: phases.append("followups") or True,
    )
    monkeypatch.setattr(
        check, "sweep_human_queue", lambda repo, now_iso: phases.append("human")
    )
    exit_code = check.run_advisory_sweep("o/r")
    assert phases == ["followups", "human"], "一覧取得が落ちても後続フェーズは走る"
    assert exit_code != 0, "一覧取得の失敗も握りつぶさず run を赤にする"


def test_l1_補償対象ページの打ち切りはlookbackで判定する() -> None:
    """Codex P2 (PR #258): closed PR の取得が 1 ページ固定だと、24h に 30 件超の
    close や古い closed PR のコメント更新で lookback 内のマージがページ外へ
    押し出され、失敗した補償が永久に残る。

    無いと何が静かに通るか: 打ち切り判定の退行は「lookback 内のマージを残した
    まま打ち切る (= P2 が直っていない)」か「closed PR 全履歴を 30 分毎に全ページ
    走査し続ける」に倒れ、どちらも run は緑のまま。merged_at <= updated_at に
    よる安全な早期終了だけを許す。
    """
    now = "2026-08-11T12:00:00Z"
    old = {"number": 1, "updated_at": "2026-08-09T00:00:00Z"}  # lookback (24h) 外
    fresh = {"number": 2, "updated_at": "2026-08-11T11:00:00Z"}  # lookback 内
    no_time = {"number": 3}
    assert page_exhausts_lookback([old, old], now), "全部古い → 打ち切ってよい"
    assert not page_exhausts_lookback([old, fresh], now), "新しい PR が居る → 続ける"
    assert not page_exhausts_lookback([no_time], now), "時刻欠落は古いと断定しない"
    assert not page_exhausts_lookback([], now), "空ページは打ち切り判定の対象外"


def test_l1_マージ失敗の翻訳は405と409を正常系として区別する() -> None:
    """無いと何が静かに通るか: 405/409 (他 check 未完・base 遅れ) を想定外扱いに
    すると sweep が 30 分毎にノイズを吐く。逆に全部を正常系に丸めると
    権限退行 (404) がログから読めず、マージ執行が沈黙したまま誰も気づかない。"""
    assert "405" in merge_failure_reason("gh: Pull Request is not mergeable (HTTP 405)")
    assert "409" in merge_failure_reason("gh: Head branch was modified (HTTP 409)")
    assert "権限" in merge_failure_reason("gh: Not Found (HTTP 404)")
    assert "想定外" in merge_failure_reason("connection reset by peer")


def test_l1_review_gate_statusの抽出はsuccessのときだけ時刻を返す() -> None:
    """無いと何が静かに通るか: pending/failure でも時刻が返ると、sweep が
    門の赤い PR にマージを試み続ける (405 連発のログノイズ + 門の意味の希薄化)。"""
    ok = {
        "context": "review-gate",
        "state": "success",
        "updated_at": "2026-08-11T00:00:00Z",
    }
    assert review_gate_success_at([ok]) == "2026-08-11T00:00:00Z"
    assert review_gate_success_at([{**ok, "state": "failure"}]) is None
    assert review_gate_success_at([{**ok, "state": "pending"}]) is None
    assert review_gate_success_at([{**ok, "context": "other-check"}]) is None
    assert review_gate_success_at([]) is None


def test_l1_check_runの全緑判定は走行中と失敗を緑に数えない() -> None:
    """無いと何が静かに通るか: 走行中や失敗を緑に倒すと「全 required check 🟢 の
    まま未マージ」でない PR にストール通知が付き、通知が狼少年化する。"""
    green = {"status": "completed", "conclusion": "success"}
    assert all_check_runs_green([green, {**green, "conclusion": "skipped"}])
    assert all_check_runs_green([{**green, "conclusion": "neutral"}])
    assert not all_check_runs_green(
        [green, {"status": "in_progress", "conclusion": None}]
    )
    assert not all_check_runs_green([{**green, "conclusion": "failure"}])
    # check run ゼロは緑 (required の本体 review-gate は commit status 側で見る)
    assert all_check_runs_green([])


def test_l1_ストール判定は閾値ちょうどで発火する() -> None:
    assert not is_stale(
        "2026-08-11T00:00:00Z", "2026-08-11T01:59:00Z", MERGE_STALL_HOURS
    )
    assert is_stale("2026-08-11T00:00:00Z", "2026-08-11T02:00:00Z", MERGE_STALL_HOURS)
    assert is_stale("2026-08-09T00:00:00Z", "2026-08-11T00:00:00Z", 48.0)
    assert not is_stale("2026-08-10T00:00:01Z", "2026-08-12T00:00:00Z", 48.0)


def test_l1_latest_isoは最新時刻を返しnoneと空を無視する() -> None:
    assert latest_iso(["2026-08-11T00:00:00Z", "2026-08-11T02:00:00Z", None, ""]) == (
        "2026-08-11T02:00:00Z"
    )
    assert latest_iso([None, ""]) is None


def test_l1_時限系の再通知は24hクールダウンで抑止される() -> None:
    """無いと何が静かに通るか: ストール通知は状態が続く限り毎 sweep (30 分毎) 条件を
    満たすため、抑止が壊れると 1 日 48 連投になる。逆に「一度きり」(still_unposted
    方式) に倒すと、直らないストールが 2 日目から沈黙する (Issue #253 のマーカー方式)。"""
    now = "2026-08-11T12:00:00Z"
    recent = [(f"{MERGE_STALL_MARKER}\n🕰 stalled", "2026-08-11T00:00:00Z")]
    old = [(f"{MERGE_STALL_MARKER}\n🕰 stalled", "2026-08-10T11:00:00Z")]
    assert should_notify_again(MERGE_STALL_MARKER, [], now), "初回は通知する"
    assert not should_notify_again(MERGE_STALL_MARKER, recent, now), "12h 前 → 黙る"
    assert should_notify_again(MERGE_STALL_MARKER, old, now), "25h 前 → 再通知"
    # 複数あれば最新でみる (古い通知が残っていても連投しない)
    assert not should_notify_again(MERGE_STALL_MARKER, old + recent, now)
    # マーカー無しコメントはクールダウンに数えない
    assert should_notify_again(MERGE_STALL_MARKER, [("普通のコメント", now)], now)


def test_l1_needs_human停滞は初回通知後も48h条件で沈黙しない() -> None:
    """Codex P2 (PR #258): 初回通知の投稿自体が Issue の updated_at を現在へ進める。

    無いと何が静かに通るか: updated_at の 48h 判定だけだと、初回通知の直後から
    「停滞していない」扱いに戻り、本文で約束している 24h 後の再通知が永久に
    起きない — 通知は 1 回きりになり、人間が見落としたらそのまま沈む。
    """
    now = "2026-08-11T12:00:00Z"
    bot = "github-actions[bot]"
    notice = (f"{HUMAN_STALL_MARKER}\n⏰ 停滞", "2026-08-10T10:00:00Z", bot)  # 26h 前
    # 通知が updated_at を進めた状態 (26h 前 = 48h 未満) でも、マーカー優先で再通知
    notify, reason = should_notify_human_stall("2026-08-10T10:00:00Z", [notice], now)
    assert notify and "再通知" in reason
    # 24h 未満は再通知しない (クールダウン)
    recent_notice = (f"{HUMAN_STALL_MARKER}\n⏰ 停滞", "2026-08-11T00:00:00Z", bot)
    notify, reason = should_notify_human_stall(
        "2026-08-11T00:00:00Z", [recent_notice], now
    )
    assert not notify and "クールダウン" in reason
    # マーカー無し (未通知) は従来どおり updated_at の 48h 判定
    assert should_notify_human_stall("2026-08-09T00:00:00Z", [], now)[0]
    assert not should_notify_human_stall("2026-08-10T00:00:00Z", [], now)[0]


def test_l1_needs_human停滞は人間が反応したら再通知しない() -> None:
    """無いと何が静かに通るか: 反応を見ないと「PO が答えたのに 24h ごとに
    メンションが飛び続ける」狼少年化。逆に bot の投稿 (自分の通知・Codex) を
    反応と数えると、再通知がやはり永久に止まる (P2 と同じ沈黙が別経路で再発)。"""
    now = "2026-08-11T12:00:00Z"
    bot = "github-actions[bot]"
    notice = (f"{HUMAN_STALL_MARKER}\n⏰ 停滞", "2026-08-10T10:00:00Z", bot)  # 26h 前
    human_reply = ("対応中です", "2026-08-10T12:00:00Z", "yomote")  # 通知の後
    notify, reason = should_notify_human_stall(
        "2026-08-10T12:00:00Z", [notice, human_reply], now
    )
    assert not notify and "反応あり" in reason
    # bot のコメントは人間の反応に数えない → 再通知は止まらない
    bot_reply = (
        "Codex Review: ...",
        "2026-08-10T12:00:00Z",
        "chatgpt-codex-connector[bot]",
    )
    assert should_notify_human_stall("2026-08-10T12:00:00Z", [notice, bot_reply], now)[
        0
    ]
    # 通知より前の人間コメントは「反応」ではない (それ自体が停滞の一部)
    old_comment = ("あとで見る", "2026-08-01T00:00:00Z", "yomote")
    assert should_notify_human_stall(
        "2026-08-10T10:00:00Z", [old_comment, notice], now
    )[0]
    # 人間の反応からさらに 48h 停滞したら再通知する (反応 1 回で永久に沈黙しない)
    stale_reply = ("対応中です", "2026-08-09T00:00:00Z", "yomote")
    stale_notice = (f"{HUMAN_STALL_MARKER}\n⏰ 停滞", "2026-08-08T00:00:00Z", bot)
    notify, reason = should_notify_human_stall(
        "2026-08-09T00:00:00Z", [stale_notice, stale_reply], now
    )
    assert notify and "48h 停滞" in reason


def test_l1_needs_human停滞はコメント以外の人間更新も時計に入れる() -> None:
    """Codex P2 追指摘 (PR #258): 本文・タイトルの編集はコメントに現れないが
    updated_at を進める。

    無いと何が静かに通るか: 通知後に PO が本文を編集して対応中と示しても
    「人間の反応なし」として 24h 後にメンションが飛び続ける (狼少年化)。
    逆に通知自身が進めた updated_at まで人間活動と数えると、再通知が永久に
    止まる (前回 P2 の沈黙が別経路で再発)。区別は固定マージン無しの厳密比較 —
    実測 (2026-08-11 #262 #253) でコメント投稿の updated_at はコメント
    created_at と**秒まで一致** (汚染幅 0 秒) のため、「既知の活動より
    1 秒でも後 = 人間の更新」で切り分けられる (固定マージンを置くと
    その幅の内側の編集を汚染側に捨てる — Codex 再々指摘で撤廃)。
    """
    now = "2026-08-11T12:00:00Z"
    bot = "github-actions[bot]"
    notice = (f"{HUMAN_STALL_MARKER}\n⏰ 停滞", "2026-08-10T00:00:00Z", bot)  # 36h 前
    # 通知の 10h 後に本文編集 (updated_at だけが進む) → 反応ありとして黙る
    notify, reason = should_notify_human_stall("2026-08-10T10:00:00Z", [notice], now)
    assert not notify and "反応あり" in reason
    # 通知を見てすぐ直すケース: 30 秒後の本文編集も人間活動と数える
    # (1 分マージン時代はここが汚染側に落ちていた — Codex 再々指摘で反転)
    notify, reason = should_notify_human_stall("2026-08-10T00:00:30Z", [notice], now)
    assert not notify and "反応あり" in reason
    notify, reason = should_notify_human_stall("2026-08-10T00:04:00Z", [notice], now)
    assert not notify and "反応あり" in reason
    # 編集から 48h 停滞したら再通知する (測り直しの起点は編集時刻)
    old_notice = (f"{HUMAN_STALL_MARKER}\n⏰ 停滞", "2026-08-08T00:00:00Z", bot)
    notify, reason = should_notify_human_stall(
        "2026-08-09T00:00:00Z", [old_notice], now
    )  # 編集は 60h 前
    assert notify and "48h 停滞" in reason
    # 汚染は「秒まで一致」のみ (実測どおり) → 人間活動と数えず 24h 経過で再通知
    notify, _ = should_notify_human_stall("2026-08-10T00:00:00Z", [notice], now)
    assert notify
    # bot コメント (Codex 等) が updated_at を進めた場合は既知の活動 → 汚染扱いで
    # 再通知は止まらない (bot を人間に化けさせない)
    codex = (
        "Codex Review: ...",
        "2026-08-10T06:00:00Z",
        "chatgpt-codex-connector[bot]",
    )
    notify, _ = should_notify_human_stall("2026-08-10T06:00:00Z", [notice, codex], now)
    assert notify


def test_l1_paginateの複数ページ連続jsonをパースできる() -> None:
    """Codex P2-a (PR #258): `gh api --paginate` は複数ページのときトップレベルの
    JSON 配列を連続出力する (`[...][...]`)。

    無いと何が静かに通るか: 素朴な json.loads は 2 ページ目の先頭で Extra data に
    なり、コメント 100 件超の Issue / PR が 1 つあるだけで schedule sweep 全体が
    毎回中断する — マージ再試行もストール通知も止まり、run の赤だけが残る。
    """
    two_pages = '[{"n": 1}, {"n": 2}]\n[{"n": 3}]'
    assert parse_gh_json(two_pages) == [{"n": 1}, {"n": 2}, {"n": 3}]
    # 1 ページ (従来ケース) は形を変えない
    assert parse_gh_json('[{"n": 1}]') == [{"n": 1}]
    assert parse_gh_json('{"total_count": 1}') == {"total_count": 1}
    assert parse_gh_json("") == {}
    assert parse_gh_json("  \n") == {}
    # 配列以外の複数値は黙って壊れた形で返さない (--paginate は配列 endpoint 専用)
    import pytest

    with pytest.raises(ValueError):
        parse_gh_json('{"a": 1}\n{"b": 2}')


def test_l1_needs_human停滞のsweepはbotコメントの更新で沈黙しない(
    monkeypatch,
) -> None:
    """Codex P2-b (PR #258): 旧実装は updated_at の 24h 事前フィルタが投稿者を
    見ずスキップしていた。

    無いと何が静かに通るか: cooldown 明け直前に bot (Codex 等) がコメントすると
    再通知がさらに 24h 遅れ、bot の定期投稿が続くと永久に発火しない — 人間には
    「通知が来ないだけ」で、needs-human キューが静かに沈む。
    """
    now = "2026-08-11T12:00:00Z"
    issue = {  # bot コメントが 1h 前に updated_at を進めている (旧フィルタなら skip)
        "number": 5,
        "updated_at": "2026-08-11T11:00:00Z",
    }
    comments = [
        {  # 前回通知 (25h 前 — クールダウン明け)
            "body": f"{HUMAN_STALL_MARKER}\n⏰ 停滞",
            "created_at": "2026-08-10T11:00:00Z",
            "user": {"login": "github-actions[bot]"},
        },
        {  # bot の定期投稿 (1h 前)。人間の反応ではない
            "body": "Codex Review: ...",
            "created_at": "2026-08-11T11:00:00Z",
            "user": {"login": "chatgpt-codex-connector[bot]"},
        },
    ]
    posted: list[tuple] = []

    def fake_gh(*args):
        joined = " ".join(args)
        if "-f" in args:  # post_comment
            posted.append(args)
            return {}
        if "state=open" in joined:
            return [issue]
        if "/comments" in joined:
            return comments
        raise AssertionError(f"想定外の gh 呼び出し: {joined}")

    monkeypatch.setattr(check, "gh", fake_gh)
    monkeypatch.setattr(check, "local_proposed_adrs", lambda: [])
    check.sweep_human_queue("o/r", now)
    assert len(posted) == 1, "通知から 24h 経過 + 人間反応なし → 再通知される"


def test_l1_bot判定はloginの接尾辞で見る() -> None:
    assert is_bot_login("github-actions[bot]")
    assert is_bot_login("chatgpt-codex-connector[bot]")
    assert not is_bot_login("yomote")
    assert not is_bot_login("")


def test_l1_proposed_adrの判定はstatus行だけを見る() -> None:
    """無いと何が静かに通るか: 本文中の引用 (「Status: Proposed で入れる」等の運用
    説明) まで拾うと、Accepted 済み ADR が永遠に停滞通知され続ける。逆に行頭
    形式を取りこぼすと、承認待ちキューが静かに沈む (ADR 0040 D1 の検知対象)。"""
    assert is_proposed_adr("# ADR\n\n- Status: Proposed\n- Date: 2026-08-11\n")
    assert is_proposed_adr("- Status: Proposed (debrief 待ち)\n")
    assert not is_proposed_adr("- Status: Accepted (design-gate 承認)\n")
    assert not is_proposed_adr("エージェント起案の ADR は Status: Proposed で入れる\n")
    assert not is_proposed_adr("- Status: Superseded by 0041\n")


def test_l1_needs_human停滞の対象はissueだけでprを除く() -> None:
    """無いと何が静かに通るか: issues API は PR も混ぜて返す。除かないと
    needs-human ラベルの PR (人間が押す担当) にまで 48h 停滞コメントが付き、
    「PR は必ず人間が押す」運用の通知が二重化する。"""
    issue = {"number": 1, "updated_at": "2026-08-01T00:00:00Z"}
    pr = {
        "number": 2,
        "updated_at": "2026-08-01T00:00:00Z",
        "pull_request": {"url": "x"},
    }
    assert human_queue_issues([issue, pr]) == [issue]


def test_l1_runs_sinceはmerged_at以降のmainのrunだけ数える() -> None:
    """補償 dispatch の冪等キー (Codex P2 / PR #258)。

    無いと何が静かに通るか: 古い run や別ブランチの run を「補償済み」と数えると
    dispatch が打たれず image 未ビルド / 反映漏れが残る。逆に絞りすぎると
    sweep のたびに同じ build/deploy を二重起動し続ける。
    """
    merged_at = "2026-08-11T12:00:00Z"
    after = {"head_branch": "main", "created_at": "2026-08-11T12:05:00Z"}
    at_merge = {"head_branch": "main", "created_at": merged_at}
    before = {"head_branch": "main", "created_at": "2026-08-11T11:59:00Z"}
    other_branch = {"head_branch": "feature", "created_at": "2026-08-11T13:00:00Z"}
    no_time = {"head_branch": "main"}
    assert runs_since([after, before, other_branch, no_time], merged_at) == [after]
    assert runs_since([at_merge], merged_at) == [at_merge], "同時刻は補償済みに数える"
    assert runs_since([], merged_at) == []


def test_l1_deployのanchorはbuild完了時刻に結び付く() -> None:
    """Codex P1 (PR #258): build 完了前に deploy を出すと、deploy.yml の
    IMAGE_TAG 解決が「当該 SHA の run 無し → 直近の成功 run」に落ちる。

    無いと何が静かに通るか: **古い image が正常デプロイされ smoke も通る**
    (全部緑のまま dev に新コードが載らない静かな劣化)。
    """
    merged_at = "2026-08-11T12:00:00Z"
    # image 変更なし → merged_at 基準 (build を待つ理由が無い)
    anchor, _ = deploy_gate_anchor(merged_at, False, [])
    assert anchor == merged_at
    # build 未起動 / 進行中 → anchor 無し (deploy はまだ出さない)
    anchor, reason = deploy_gate_anchor(merged_at, True, [])
    assert anchor is None and "未起動" in reason
    anchor, reason = deploy_gate_anchor(
        merged_at, True, [{"status": "in_progress", "conclusion": None}]
    )
    assert anchor is None and "進行中" in reason
    # 完了 → anchor = build の完了時刻 (updated_at)。merged_at ではない
    done = {
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-08-11T12:01:00Z",
        "updated_at": "2026-08-11T12:10:00Z",
    }
    anchor, _ = deploy_gate_anchor(merged_at, True, [done])
    assert anchor == "2026-08-11T12:10:00Z"
    # 失敗完了でも deploy へ進む — push 経路と同じ (deploy.yml が直近成功 image で
    # 続行 + warning。build の赤は build-images 側で見える)
    anchor, _ = deploy_gate_anchor(merged_at, True, [{**done, "conclusion": "failure"}])
    assert anchor is not None
    # 進行中と完了が混在していれば最初の完了時刻を採る
    later = {**done, "updated_at": "2026-08-11T12:30:00Z"}
    anchor, _ = deploy_gate_anchor(
        merged_at, True, [{"status": "in_progress"}, later, done]
    )
    assert anchor == "2026-08-11T12:10:00Z"


def test_l1_並行マージでbuild前のdeployを補償済みと誤認しない() -> None:
    """Codex P1 再指摘 (PR #258): image 変更ありの A の build 進行中に、image 変更
    なしの B がマージされ deploy を出すシナリオ。

    無いと何が静かに通るか: merged_at 基準の deploy 済み判定だと、B の deploy
    (A の build 完了前 = 古い image) を A の補償と誤認し、**A の build 完了後の
    deploy が永久に出ない** — dev に A の image が載らないまま全部緑が続く。
    """
    a_merged = "2026-08-11T12:00:00Z"
    a_build_done = {
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-08-11T12:01:00Z",
        "updated_at": "2026-08-11T12:10:00Z",
    }
    # B (image 変更なし) の deploy は A の build 完了 (12:10) より前の 12:05 に走った
    b_deploy = {"head_branch": "main", "created_at": "2026-08-11T12:05:00Z"}
    anchor, _ = deploy_gate_anchor(a_merged, True, [a_build_done])
    assert anchor == "2026-08-11T12:10:00Z"
    # anchor 基準では B の deploy は「補償済み」に数えない → A の deploy が出る
    assert runs_since([b_deploy], anchor) == []
    # 旧実装 (merged_at 基準) では誤認していたことの固定
    assert runs_since([b_deploy], a_merged) == [b_deploy]
    # build 完了後の deploy (12:15) が現れれば補償済みになる
    after = {"head_branch": "main", "created_at": "2026-08-11T12:15:00Z"}
    assert runs_since([b_deploy, after], anchor) == [after]


def test_l1_earliest_isoは最古時刻を返しnoneと空を無視する() -> None:
    assert earliest_iso(["2026-08-11T02:00:00Z", "2026-08-11T00:00:00Z", None, ""]) == (
        "2026-08-11T00:00:00Z"
    )
    assert earliest_iso([None, ""]) is None


def test_l1_補償の再試行対象はlookback内にマージされたprだけ() -> None:
    """Codex P2 (PR #258): マージ後の dispatch 失敗は「マージ済みなのでイベント
    run は即終了 / sweep は open PR しか見ない」ため永久に再試行されなかった。

    無いと何が静かに通るか: 対象選定の退行は「クローズのみの PR や大昔の
    マージまで毎 sweep 照会し続ける」か「直近マージが漏れて補償が失われた
    まま」(= P2 が直っていない) に倒れ、どちらも run は緑のまま。
    """
    now = "2026-08-11T12:00:00Z"
    fresh = {"number": 1, "merged_at": "2026-08-11T00:00:00Z"}
    old = {"number": 2, "merged_at": "2026-08-09T00:00:00Z"}
    closed_only = {"number": 3, "merged_at": None}
    assert recently_merged([fresh, old, closed_only], now) == [fresh]
    assert recently_merged([], now) == []


def test_l1_image_buildの補償dispatchはpush触発のpaths写像() -> None:
    """無いと何が静かに通るか: GITHUB_TOKEN のマージ push は build-images の push
    トリガーを起動しない。写像が欠けると image ソースの変更が ghcr に積まれず、
    次の deploy が古い image を差し替え続ける (#107 の据え置きが形を変えて再発)。"""
    assert needs_image_build(["apps/services/ai-agent/app/main.py"])
    assert needs_image_build(["apps/services/voicevox/app/main.py"])
    assert needs_image_build([".github/workflows/build-images.yml"])
    assert not needs_image_build(["apps/bff/src/x.ts", "docs/x.md"])
    assert not needs_image_build([])
