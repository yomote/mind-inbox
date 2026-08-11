"""review-gate の判定ロジックのテスト (ADR 0036)。

無いと何が静かに通るか: SHA 照合や Codex 対象判定のバグは「門が開きっぱなし」を
意味するが、門は開いていても誰も気づかない (マージは普通に成功する) ため、
判定ロジックの退行はテストでしか捕まえられない。
"""

from check import (
    CODEX_RETRIGGER_MARKER,
    Carryover,
    codex_present_in,
    decide,
    diff_digest_from_files,
    evaluate_carryover,
    has_pm_accept,
    is_code_pr,
    is_codex_review_result,
    latest_pm_accept_token,
    minutes_between,
    parse_merge_group_pr,
    sensitive_paths,
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


# ---- pm-accept の引き継ぎ (ADR 0042) ----

# 受け入れ済みコミット A の上に「main からのマージ」M1, M2 が積まれた PR
BASE_PARENT = "0000000basecommit000"
A_SHA = "aaa1111acceptedaaaaa"
M1_SHA = "bbb2222mergemain1bbb"
M2_SHA = "ccc3333mergemain2ccc"
MAIN_TIP_1 = "ddd4444mainside1"
MAIN_TIP_2 = "eee5555mainside2"
PR_COMMITS = [
    {"sha": A_SHA, "parents": [BASE_PARENT]},
    {"sha": M1_SHA, "parents": [A_SHA, MAIN_TIP_1]},
    {"sha": M2_SHA, "parents": [M1_SHA, MAIN_TIP_2]},
]


def _from_base(sha: str) -> bool:
    return sha in {MAIN_TIP_1, MAIN_TIP_2}


def _carryover(**overrides) -> Carryover:
    """成立する既定値からの差分で書く (どの条件で落ちたかをテスト名で示す)。"""
    kwargs = dict(
        accepted_token=A_SHA[:7],
        head_sha=M2_SHA,
        pr_commits=PR_COMMITS,
        is_from_base=_from_base,
        diff_digest=lambda sha: "same-digest",
    )
    kwargs.update(overrides)
    return evaluate_carryover(**kwargs)


def test_l1_引き継ぎはmainマージのみかつ差分不変なら成立() -> None:
    """ADR 0042 の主目的: 追いつき競争 (base 追随 push のたびに pm-accept が失効し
    再受け入れ→また追いつかれる) を、実装差分が不変な限り引き継ぎで断つ。

    無いと何が静かに通るか: 逆方向 (成立すべきものが不成立) は「毎周 PM の
    再受け入れが要る」に戻るだけで気づけるが、判定式の退行で**成立しすぎる**
    方向は門が開くのに誰も気づかない。下の各否定テストとセットで固定する。
    """
    result = _carryover()
    assert result.ok
    assert result.accepted_short == A_SHA[:7]


def test_l1_引き継ぎ成立時のdescriptionに由来shaが出る() -> None:
    v = decide(M2_SHA, ["apps/x.ts"], [], 0, True, True, carryover=_carryover())
    assert v.ok
    assert f"pm-accept を {A_SHA[:7]} から引き継ぎ" in v.description
    assert "差分不変" in v.description


def test_l1_実装コミットが混ざると引き継ぎ不成立() -> None:
    """無いと何が静かに通るか: 受け入れ後に積んだ実装コミットが「main 追随」に
    紛れてレビューなしでマージされる (門の穴)。"""
    impl = {"sha": M2_SHA, "parents": [M1_SHA]}  # 1 親 = 普通の実装コミット
    result = _carryover(pr_commits=[PR_COMMITS[0], PR_COMMITS[1], impl])
    assert not result.ok
    assert "マージでない" in result.detail


def test_l1_マージ元がmain由来でないと引き継ぎ不成立() -> None:
    # 2 親のマージでも、第二親が base に無い (= 別ブランチの取り込み) なら実装変更
    result = _carryover(is_from_base=lambda sha: False)
    assert not result.ok
    assert "base からのマージでない" in result.detail


def test_l1_実装差分が変わると引き継ぎ不成立() -> None:
    """判定を緩めない要: 差分が 1 文字でも変わる push (evil merge 含む) は
    従来どおり新しい pm-accept を要求する。"""
    result = _carryover(diff_digest=lambda sha: f"digest-of-{sha}")
    assert not result.ok
    assert "実装差分が受け入れ時点から変化" in result.detail


def test_l1_差分が取得しきれないときは引き継ぎ不成立() -> None:
    # compare API の files 打ち切り (300 件) — 「見えなかったものを同一」と書かない
    result = _carryover(diff_digest=lambda sha: None)
    assert not result.ok
    assert "取得しきれない" in result.detail


def test_l1_受け入れshaがPRコミットに無いと引き継ぎ不成立() -> None:
    # rebase / force-push でコミットが書き換わった場合もここに落ちる (安全側)
    result = _carryover(accepted_token="fffffff")
    assert not result.ok
    assert "一意解決できない" in result.detail


def test_l1_第一親で辿れない履歴は引き継ぎ不成立() -> None:
    # マージの向きが逆 (第一親が main 側) — PR ブランチの継続性が壊れている
    twisted = [
        PR_COMMITS[0],
        {"sha": M2_SHA, "parents": [MAIN_TIP_2, A_SHA]},
    ]
    result = _carryover(pr_commits=twisted)
    assert not result.ok
    assert "第一親で辿れない" in result.detail


def test_l1_受け入れが現headそのものなら引き継ぎ扱いで成立() -> None:
    result = _carryover(accepted_token=M2_SHA[:7])
    assert result.ok


def test_l1_引き継ぎ不成立の理由はdescriptionに出る() -> None:
    v = decide(
        M2_SHA,
        [],
        [],
        0,
        False,
        False,
        carryover=Carryover(ok=False, detail="実装差分が受け入れ時点から変化"),
    )
    assert not v.ok
    assert "引き継ぎ不成立: 実装差分が受け入れ時点から変化" in v.description


def test_l1_最新のpm_acceptコメントからshaトークンを取る() -> None:
    comments = [
        ("[pm-accept] aaa1111 一次受け入れ", "OWNER"),
        ("経過コメント", "OWNER"),
        ("[pm-accept] BBB2222 受け入れをやり直し", "OWNER"),
    ]
    # 最新の受け入れが意思 — 古い受け入れへ遡らない。大文字は小文字化される
    assert latest_pm_accept_token(comments) == "bbb2222"


def test_l1_第三者のpm_acceptはトークン抽出でも無視される() -> None:
    comments = [
        ("[pm-accept] aaa1111 正規の受け入れ", "OWNER"),
        ("[pm-accept] ccc9999 攻撃コメント", "NONE"),
    ]
    assert latest_pm_accept_token(comments) == "aaa1111"


def test_l1_最新のpm_acceptにshaが無ければトークンなし() -> None:
    # sha 無しの受け入れやり直しは「どのコミットへの受け入れか」が無い — 引き継がない
    assert latest_pm_accept_token([("[pm-accept] sha を書き忘れ", "OWNER")]) is None
    assert latest_pm_accept_token([("ただのコメント", "OWNER")]) is None
    assert latest_pm_accept_token([]) is None


def test_l1_差分指紋は内容が同じなら順序によらず一致() -> None:
    files_a = [
        {"filename": "a.ts", "status": "modified", "sha": "s1", "patch": "@@ -1 +1 @@"},
        {"filename": "b.ts", "status": "added", "sha": "s2", "patch": "@@ +1 @@"},
    ]
    assert diff_digest_from_files(files_a) == diff_digest_from_files(files_a[::-1])


def test_l1_差分指紋はpatchやblobの変化で変わる() -> None:
    """無いと何が静かに通るか: 指紋が差分の一部しか見ていないと、実装が変わった
    push を「差分不変」と誤判定して未レビューコードが引き継ぎで通る。"""
    base = [{"filename": "a.ts", "status": "modified", "sha": "s1", "patch": "@@ x"}]
    changed_patch = [dict(base[0], patch="@@ y")]
    changed_blob = [dict(base[0], sha="s2")]
    renamed = [dict(base[0], previous_filename="old.ts")]
    d = diff_digest_from_files(base)
    assert d != diff_digest_from_files(changed_patch)
    assert d != diff_digest_from_files(changed_blob)
    assert d != diff_digest_from_files(renamed)


def test_l1_差分指紋は300件で打ち切りの可能性があると判定不能() -> None:
    files = [{"filename": f"f{i}.ts", "status": "modified"} for i in range(300)]
    assert diff_digest_from_files(files) is None
    assert diff_digest_from_files(files[:299]) is not None


# ---- merge_group (ADR 0042) ----


def test_l1_merge_group_refからPR番号を解決できる() -> None:
    """無いと何が静かに通るか: ref 形式の読み違いは「queue に入るたび review-gate が
    誤った PR (または PR なし) を判定する」— check は貼られるが中身が別物になる。"""
    assert parse_merge_group_pr("gh-readonly-queue/main/pr-267-0f1e2d3c") == 267
    assert parse_merge_group_pr("refs/heads/gh-readonly-queue/main/pr-8-abc123") == 8


def test_l1_merge_group_refが解決できないときはNone() -> None:
    # None は呼び出し側で failure (安全側) — 静かに緑を貼らない
    assert parse_merge_group_pr("") is None
    assert parse_merge_group_pr("refs/heads/feature/pr-12-abc") is None
    assert parse_merge_group_pr("gh-readonly-queue/main/pr-abc") is None


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
