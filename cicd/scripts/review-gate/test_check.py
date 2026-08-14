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
    Carryover,
    all_check_runs_green,
    codex_present_in,
    deploy_gate_anchor,
    diff_digest_from_files,
    earliest_iso,
    decide,
    evaluate_carryover,
    has_pm_accept,
    latest_pm_accept_token,
    parse_merge_group_pr,
    human_queue_issues,
    REVIEWER_CODEX,
    REVIEWER_STANDIN,
    STANDIN_REVIEW_MARKER,
    has_standin_review,
    should_notice_standin_pass,
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


def test_l1_独立レビューは環境変数では切れない(monkeypatch) -> None:
    """Issue #400 D1: 旧 REVIEW_GATE_REQUIRE_CODEX を false にしても要求は生きること。

    無いと何が静かに通るか:
        フラグが残る限り「上限が来たら false にする」経路が生き続ける。実際に
        2026-08-13 の実測で false のまま門はレビューを一度も見ておらず、
        status は「レビューが揃った」と出続けた (PR #338)。degrade したことは
        Actions run のログを開かないと分からなかった。
    """
    assert check.REQUIRE_INDEPENDENT_REVIEW is True
    monkeypatch.setenv("REVIEW_GATE_REQUIRE_CODEX", "false")

    def fake_gh(*args):
        path = args[1]
        if path.endswith("/files"):
            return [{"filename": "apps/bff/x.ts"}]
        if path.endswith("/comments"):
            return [
                {
                    "body": "[pm-accept] abc1234 — 意図どおり",
                    "author_association": "OWNER",
                }
            ]
        raise AssertionError(f"想定外の gh 呼び出し: {args}")

    monkeypatch.setattr(check, "gh", fake_gh)
    monkeypatch.setattr(check, "fetch_unresolved_threads", lambda *a: 0)
    monkeypatch.setattr(check, "fetch_codex_present", lambda *a: False)
    ev = check.evaluate_gate("o/r", 1, HEAD)
    assert not ev.verdict.ok, "env が false でも独立レビューは要求される"
    assert any("独立レビュー" in m for m in ev.verdict.missing)


# ---- 受け入れ行の構文一致 (Issue #380) ----


def test_l1_380_否定文と見出しのshaでは門が開かない() -> None:
    """Issue #380 の実物 (PR #379, 2026-08-12): 見出しに head SHA、本文末尾に
    「[pm-accept] は押していません。」という進捗コメントに、13 秒後に
    success が貼られた。

    無いと何が静かに通るか:
        「マーカーを含む + SHA を含む」の含有判定は文脈も否定も見ない。
        このリポジトリの規約は [pm-accept] を頻繁に文章で参照するため、
        攻撃なしの日常コメントで門が緑になり、常設承認と組み合わさると
        受け入れの無い PR が黙ってマージされる。
    """
    body = (
        f"### 進捗 (`{HEAD[:7]}`)\n\n"
        "実装が完了しました。CI は緑です。\n\n"
        "[pm-accept] は押していません。\n"
    )
    assert not has_pm_accept([(body, "OWNER")], HEAD)
    assert not decide(HEAD, ["apps/bff/x.ts"], [(body, "OWNER")], 0, True, True).ok


def test_l1_受け入れ行は行頭マーカー直後のshaだけを読む() -> None:
    """受け入れ = 行頭 `[pm-accept] <sha>` の構文一致 (#380 案 A+B)。

    無いと何が静かに通るか:
        正の形を固定しないと、厳密化のつもりが正規の受け入れ (merge skill の
        規定書式) まで弾いて全 PR が受け入れ不能になる。負の形を固定しないと、
        含有判定への退行 (#380 の穴の復活) に気づけない。
    """
    # 正: 規定書式 (`/merge` skill) / 理由なし / 全長 SHA / 複数行コメントの中の 1 行
    assert has_pm_accept([("[pm-accept] abc1234 — 意図どおり", "OWNER")], HEAD)
    assert has_pm_accept([("[pm-accept] abc1234", "OWNER")], HEAD)
    assert has_pm_accept([(f"[pm-accept] {HEAD}", "OWNER")], HEAD)
    assert has_pm_accept(
        [(f"確認しました。\n\n[pm-accept] {HEAD[:7]} — ok", "OWNER")], HEAD
    )
    # 負: 行の途中のマーカー言及 (規約の説明文が SHA を含むだけ)
    assert not has_pm_accept(
        [(f"マージには [pm-accept] {HEAD[:7]} の投稿が必要です", "OWNER")], HEAD
    )
    # 負: マーカーと SHA が別の場所 (含有判定なら通っていた形)
    assert not has_pm_accept(
        [(f"`{HEAD[:7]}` を確認中。\n[pm-accept] はまだです", "OWNER")], HEAD
    )


def test_l1_引用とコードフェンス内の受け入れ行は数えない() -> None:
    """無いと何が静かに通るか:
    引用: 第三者の偽受け入れを権限保持者が Quote reply すると association が
    OWNER に化けて門が開く (standin マーカーで実測済みの経路と同型)。
    フェンス: 書式説明で skill の例文を貼っただけのコメントが受け入れになる
    (#380 と同じ「言及を宣言と読む」誤爆)。
    """
    quoted = (f"> [pm-accept] {HEAD[:7]} — ok\n\n引用を確認しました", "OWNER")
    fenced = (f"書式はこうです:\n```\n[pm-accept] {HEAD[:7]} — 理由\n```", "OWNER")
    assert not has_pm_accept([quoted], HEAD)
    assert not has_pm_accept([fenced], HEAD)


def test_l1_markdownの全コードブロック形式を判定対象から外す() -> None:
    """Codex P1 (PR #404): ``` フェンスだけ外しても、GitHub Markdown でコードに
    なる形式は他に 2 つある (~~~ フェンス / 4 スペース・タブのインデントコード)。

    無いと何が静かに通るか:
        権限保持者が現 head の SHA 入りの書式例をインデントコードで貼るだけで
        受け入れ・代役レビューが成立し、以前の受け入れで auto-merge が武装済みの
        PR なら実際の受け入れ・レビューなしでマージされる。
    """
    indented = (f"書式の例:\n\n    [pm-accept] {HEAD[:7]} — 理由\n", "OWNER")
    tabbed = (f"例:\n\n\t[pm-accept] {HEAD[:7]} — 理由\n", "OWNER")
    tilde = (f"~~~\n[pm-accept] {HEAD[:7]} — 理由\n~~~\n", "OWNER")
    assert not has_pm_accept([indented], HEAD)
    assert not has_pm_accept([tabbed], HEAD)
    assert not has_pm_accept([tilde], HEAD)
    standin_indented = (
        f"ヘッダの例:\n\n    {STANDIN_REVIEW_MARKER}\n    代役レビュー ({HEAD[:7]})\n",
        "OWNER",
    )
    standin_tilde = (
        f"~~~markdown\n{STANDIN_REVIEW_MARKER}\n代役レビュー ({HEAD[:7]})\n~~~\n",
        "OWNER",
    )
    assert not has_standin_review([standin_indented], HEAD)
    assert not has_standin_review([standin_tilde], HEAD)
    # フェンスの閉じは開いたのと同じ文字だけ — ``` の中の ~~~ 行はフェンスを
    # 閉じない (閉じ扱いにすると、以降のブロック内容が判定対象に漏れる)
    mixed = (
        f"```\n~~~\n[pm-accept] {HEAD[:7]} — 例\n```\n",
        "OWNER",
    )
    assert not has_pm_accept([mixed], HEAD)
    # 過剰除外の対照: インデント無しの正規の受け入れは通る (上の正のテストと重複
    # だが、この除外がどこまでかをこのテスト内で読めるようにする)
    assert has_pm_accept([(f"[pm-accept] {HEAD[:7]} — ok", "OWNER")], HEAD)


def test_l1_トークン抽出も構文一致で行頭の保留宣言を尊重する() -> None:
    """carryover (ADR 0042) の起点も受け入れ行の構文で選ぶこと。

    無いと何が静かに通るか:
        含有判定のままだと「[pm-accept] は押していません」という保留コメントが
        最新の受け入れとして読まれ、そこに hex らしき語があれば引き継ぎの起点に
        すらなる (#380 の穴が門ではなく carryover 側から開く)。
    """
    accept = ("[pm-accept] aaa1111 — ok", "OWNER")
    hold = ("[pm-accept] は押していません。理由: レビュー待ち", "OWNER")
    prose = ("`[pm-accept]` の書式は merge skill を見てください", "OWNER")
    # 行頭の保留宣言 (SHA なし) は最新の意思 — 古い受け入れへ遡らない
    assert latest_pm_accept_token([accept, hold]) is None
    # 地の文・インラインコードの言及は素通しして、直近の正規の受け入れを読む
    assert latest_pm_accept_token([accept, prose]) == "aaa1111"


# ---- 代役 judge の独立レビュー (ADR 0052 D7) ----

STANDIN_OK = (
    f"{STANDIN_REVIEW_MARKER}\n代役レビュー ({HEAD[:7]}): blocker なし",
    "OWNER",
)


def test_l1_代役レビューはcodexの代わりに独立レビュー条件を満たす() -> None:
    """Codex が居なくても代役レビューがあればコード PR の門が開くこと。

    無いと何が静かに通るか:
        Codex が利用上限で黙っている間 (#345)、コード PR は全部
        「独立レビューが無い」で永久に赤のまま。門を開ける (require を false)
        以外に進む道が無くなり、**有限資源の都合で門が開く**前例になる。
    """
    code = ["apps/bff/x.ts"]
    assert decide(HEAD, code, [ACCEPT_OK, STANDIN_OK], 0, False, True).ok


def test_l1_古いshaの代役レビューは押し流される() -> None:
    """代役レビューは push で失効すること (pm-accept と同じ強度)。

    無いと何が静かに通るか:
        代役の投稿はレビュー対象を書いた本人と同じアカウントから出るため、
        SHA を縛らないと「1 回レビューを貼ってから、以後は何を push しても
        門が開いたまま」になる。実装者が自分で門を開けられる状態
        (#331 と同種の穴) が、代役の導入で新たに空く。
    """
    stale = (f"{STANDIN_REVIEW_MARKER}\n代役レビュー (9999999): blocker なし", "OWNER")
    assert not has_standin_review([stale], HEAD)
    assert not decide(HEAD, ["apps/bff/x.ts"], [ACCEPT_OK, stale], 0, False, True).ok


def test_l1_代役レビューはマーカーとshaの両方が要る() -> None:
    """マーカーだけ・SHA だけでは代役レビューと数えないこと。

    無いと何が静かに通るか:
        SHA を書かずマーカーだけ貼れば恒久的に門が開く (上のテストの抜け道)。
        逆に SHA だけで数えると、head SHA に言及した**ただの雑談コメント**が
        独立レビュー扱いになる (PR 本文に SHA を貼る運用があるので現実に起きる)。
    """
    assert not has_standin_review(
        [(f"{STANDIN_REVIEW_MARKER} SHA なし", "OWNER")], HEAD
    )
    assert not has_standin_review([(f"{HEAD[:7]} を見た", "OWNER")], HEAD)


def test_l1_引用された代役マーカーは数えない() -> None:
    """権限保持者が第三者の投稿を引用しても門が開かないこと。

    無いと何が静かに通るか:
        マーカーは HTML コメントで**画面に見えない**。第三者が不可視の
        `<!-- standin-review --> <sha>` を投稿し、権限保持者が GitHub の
        "Quote reply" で返信すると raw markdown が `> ` 付きで複製され、
        association が NONE → OWNER に化けて **誰もレビューしていないのに
        門が開く** (security-reviewer が PR #361 で実測した経路)。
        `[pm-accept]` と違い可視の痕跡が PR 画面に残らないので、
        後から気づく手段も無い。
    """
    quoted = (
        f"> {STANDIN_REVIEW_MARKER}\n> 代役レビュー ({HEAD[:7]}): blocker なし\n\n"
        "ありがとうございます、確認します",
        "OWNER",
    )
    assert not has_standin_review([quoted], HEAD)
    assert not decide(HEAD, ["apps/bff/x.ts"], [ACCEPT_OK, quoted], 0, False, True).ok
    # 引用行に混ざっていても、自分の行に正しく書いてあれば数える
    mixed = (
        f"{STANDIN_REVIEW_MARKER}\n代役レビュー ({HEAD[:7]}): blocker なし\n\n> 引用\n",
        "OWNER",
    )
    assert has_standin_review([mixed], HEAD)


def test_l1_第三者の代役レビューは数えない() -> None:
    """権限保持者以外の投稿は代役レビューと数えないこと。

    無いと何が静かに通るか:
        このリポジトリは public なので誰でも PR にコメントできる。
        マーカーと SHA は本文に書くだけなので、第三者が
        `<!-- standin-review --> <sha>` と書けば門が開く (pm-accept で
        2026-08-10 に実際に見つかった穴と同型)。
    """
    outsider = (f"{STANDIN_REVIEW_MARKER} {HEAD[:7]} LGTM", "NONE")
    assert not has_standin_review([outsider], HEAD)
    assert not decide(HEAD, ["apps/bff/x.ts"], [ACCEPT_OK, outsider], 0, False, True).ok


def test_l1_代役レビューもpm_acceptと同じ引き継ぎが効く() -> None:
    """差分不変の base 追随では代役レビューも引き継がれること (ADR 0042 と同形)。

    無いと何が静かに通るか:
        pm-accept は carryover で生き残るのに独立レビューだけが失効するため、
        **main を追随するたびに門が「独立レビューが無い」で赤へ戻る**。
        受け入れ済み・レビュー済みの PR が、実装を 1 文字も変えていないのに
        延々とマージできなくなる (PR #330 の代役レビューが実測で見つけた非対称)。
    """
    carried = Carryover(ok=True, accepted_short="9999999")
    old_review = (
        f"{STANDIN_REVIEW_MARKER}\n代役レビュー (9999999): blocker なし",
        "OWNER",
    )
    assert has_standin_review([old_review], HEAD, "9999999")
    v = decide(HEAD, ["apps/bff/x.ts"], [old_review], 0, False, True, carried)
    assert v.ok, v.missing


def test_l1_引き継ぎが不成立なら代役レビューは失効する() -> None:
    """carryover が成立していないときは古い代役レビューを数えないこと。

    無いと何が静かに通るか:
        「引き継ぎ元 SHA も許す」を無条件にすると、実装を書き換える push でも
        古いレビューが生き続け、**1 回レビューを貼れば何を push しても門が開く**
        状態に戻る (SHA を縛った理由そのものが消える)。
    """
    old_review = (
        f"{STANDIN_REVIEW_MARKER}\n代役レビュー (9999999): blocker なし",
        "OWNER",
    )
    assert not has_standin_review([old_review], HEAD, None)
    # accepted_short を**あえて埋める** — 空だと carryover.ok を見ない実装でも
    # テストが通ってしまい、この検査が何も守らなくなる (ミューテーションで確認済み)
    failed = Carryover(
        ok=False, detail="実装差分が受け入れ時点から変化", accepted_short="9999999"
    )
    v = decide(HEAD, ["apps/bff/x.ts"], [ACCEPT_OK, old_review], 0, False, True, failed)
    assert not v.ok
    assert any("独立レビュー" in m for m in v.missing)


def test_l1_代役マーカーは行頭の行だけ数える() -> None:
    """standin マーカーにも #380 と同型の構文厳密化が効くこと。

    無いと何が静かに通るか:
        含有判定のままだと、書式の説明 (インラインコード / フェンス内の
        rubric 必須ヘッダの例文) に head SHA が並んだだけの権限保持者コメントが
        独立レビューになる — レビューされていないコード PR の門が開く。
    """
    inline = (f"`{STANDIN_REVIEW_MARKER}` を貼ってください ({HEAD[:7]})", "OWNER")
    fenced = (
        f"必須ヘッダ:\n```\n{STANDIN_REVIEW_MARKER}\n代役レビュー ({HEAD[:7]})\n```",
        "OWNER",
    )
    assert not has_standin_review([inline], HEAD)
    assert not has_standin_review([fenced], HEAD)
    # rubric (review-rubric.md Part 6) の必須ヘッダ書式はそのまま数える
    rubric_form = (
        f"{STANDIN_REVIEW_MARKER}\n\n**代役レビュー (`{HEAD[:7]}`)** — "
        "Codex 不在の埋め合わせであり、独立性は回復していません",
        "OWNER",
    )
    assert has_standin_review([rubric_form], HEAD)


def test_l1_緑のstatusは担い手を明示する() -> None:
    """Issue #400 P1 / D3-1: 何で門が通ったかが status の文言から読めること。

    無いと何が静かに通るか:
        固定文言「レビューが揃った」は、レビューを一度も見ていない PR (#338) にも
        出て嘘になった。degrade (代役で通った / レビュー対象外) が status から
        見えないと、独立性が落ちたまま誰も気づかない (#400 の PO 裁定の実体)。
    """
    code = ["apps/bff/x.ts"]
    by_codex = decide(HEAD, code, [ACCEPT_OK], 0, True, True)
    assert by_codex.ok and by_codex.reviewer == REVIEWER_CODEX
    assert "独立レビュー: Codex" in by_codex.description
    by_standin = decide(HEAD, code, [ACCEPT_OK, STANDIN_OK], 0, False, True)
    assert by_standin.ok and by_standin.reviewer == REVIEWER_STANDIN
    assert "代役 judge" in by_standin.description
    assert "未回復" in by_standin.description, "degrade を成功の顔で書かない"
    docs = decide(HEAD, ["docs/x.md"], [ACCEPT_OK], 0, False, True)
    assert docs.ok and docs.reviewer == ""
    assert "コード PR でない" in docs.description
    # 固定文言は返さない (見ていないものを「揃った」と書かない)
    assert "レビューが揃った" not in docs.description


def test_l1_代役通過の告知は代役で通ったときだけ1回() -> None:
    """Issue #400 D3-2: degrade 告知の発火条件。

    無いと何が静かに通るか:
        条件の退行は「Codex で通った PR にも毎回貼るノイズ」か「代役で通ったのに
        黙る (= 可視化の欠落)」に倒れ、どちらも run は緑のまま。
    """
    ok = dict(verdict_ok=True, reviewer=REVIEWER_STANDIN, marker_posted=False)
    assert should_notice_standin_pass(**ok)
    assert not should_notice_standin_pass(**{**ok, "verdict_ok": False})
    assert not should_notice_standin_pass(**{**ok, "reviewer": REVIEWER_CODEX})
    assert not should_notice_standin_pass(**{**ok, "reviewer": ""})
    assert not should_notice_standin_pass(**{**ok, "marker_posted": True}), "1 回だけ"


def test_l1_独立レビュー不足の文言は担い手を限定しない() -> None:
    """欠落メッセージが「Codex が無い」と読めないこと。

    無いと何が静かに通るか:
        代役でも満たせる条件なのに status が「Codex レビューが無い」と出ると、
        PO と当番 PM は「Codex 復帰を待つしかない」と読んで詰まる
        (#345 で実際に 8 本の PR が待たされた読み方)。
    """
    v = decide(HEAD, ["apps/bff/x.ts"], [ACCEPT_OK], 0, False, True)
    assert not v.ok
    assert any("独立レビュー" in m for m in v.missing)
    assert not any("Codex" in m for m in v.missing)


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
    # 特権 workflow が呼ぶスクリプトと宣言 (2026-08-12 追加)。
    # 無いと何が静かに通るか: workflow ファイル 1 行の変更は指名されるのに、
    # その workflow が実際に叩く管理 API のコードは無審査で入る
    # (PR #347 が実際にそうなった)。
    assert sensitive_paths(["cicd/github/settings.yml"])
    assert sensitive_paths(["cicd/scripts/github-settings/sync.py"])
    assert sensitive_paths(["cicd/scripts/github-settings/device_login.py"])
    # マージの門そのもの — PR が自分の門を緩められる経路 (#331)
    assert sensitive_paths(["cicd/scripts/review-gate/check.py"])
    # 対象外のもの
    assert not sensitive_paths(["docs/adr/0038-x.md", "apps/frontend/src/App.tsx"])
    # cicd/ でも門・管理設定と無関係なものは対象外 (広げすぎない)
    assert not sensitive_paths(["cicd/scripts/status-page/build.py"])
    assert not sensitive_paths(["cicd/scripts/ux-probe/post-judge-score.sh"])
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


# ---- #258 (マージ執行) と ADR 0042 (pm-accept 引き継ぎ) の合流点 ----


def _stub_gate_io(monkeypatch, comments: list[dict], carryover) -> list[dict]:
    """evaluate_gate の I/O (files / comments / スレッド / Codex / 引き継ぎ) を差し替える。
    戻り値は compute_carryover が呼ばれた記録 (呼ばれなかったことも見たいので list)。"""
    carryover_calls: list[dict] = []

    def fake_gh(*args):
        path = args[1]
        if path.endswith("/files"):
            return [{"filename": "docs/x.md"}]
        if path.endswith("/comments"):
            return comments
        raise AssertionError(f"想定外の gh 呼び出し: {args}")

    monkeypatch.setattr(check, "gh", fake_gh)
    monkeypatch.setattr(check, "fetch_unresolved_threads", lambda *a: 0)
    monkeypatch.setattr(check, "fetch_codex_present", lambda *a: True)
    monkeypatch.setattr(
        check,
        "compute_carryover",
        lambda repo, pr, pairs: carryover_calls.append(pr) or carryover,
    )
    return carryover_calls


def test_l1_引き継ぎはマージ直前のフル再評価でも効く(monkeypatch) -> None:
    """引き継ぎ判定を evaluate_gate に置く (= マージ執行の再評価も同じ関数を通る)
    という設計を固定する。

    無いと何が静かに通るか: 引き継ぎをイベント経路だけに置き直すと、
    reverify_and_merge の再評価が引き継ぎを見ずに「PM 受け入れが無い」と判断し、
    緑の status を failure に**訂正して**マージを止める — PM から見えるのは
    「一度緑になった PR が 30 分後に赤に戻る」で、ADR 0042 が消したはずの
    追いつき競争が sweep 側にだけ残る。赤方向の退行なので門は破れず、
    テストが無ければ誰も気づかない。
    """
    pr = _mergeable_pr(head={"sha": "abc1234def5678"})
    carried = check.Carryover(ok=True, accepted_short="aaa1111")
    calls = _stub_gate_io(monkeypatch, comments=[], carryover=carried)

    ev = check.evaluate_gate("o/r", 1, "abc1234def5678", pr)
    assert ev.verdict.ok, "引き継ぎ成立なら再評価も緑 (failure へ訂正しない)"
    assert "pm-accept を aaa1111 から引き継ぎ" in ev.verdict.description
    assert calls == [pr], "引き継ぎ判定に渡すのは評価中の PR そのもの"


def test_l1_直接の受け入れがあるとき引き継ぎ判定は呼ばれない(monkeypatch) -> None:
    """API 節約 (ADR 0042) の実装契約。無いと何が静かに通るか: compare API を
    毎回 2 往復以上叩くようになり、sweep が open PR 数に比例して重くなる
    (レート制限に触れて sweep 全体が落ちる = マージの下限保証が消える) —
    判定結果は同じなのでテストが無ければ気づけない。"""
    accepted = [{"body": "[pm-accept] abc1234", "author_association": "OWNER"}]
    calls = _stub_gate_io(monkeypatch, comments=accepted, carryover=None)

    ev = check.evaluate_gate("o/r", 1, "abc1234def5678")
    assert ev.verdict.ok
    assert calls == [], "現 head への直接の受け入れがあるなら compare API を叩かない"


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


def test_l1_sweep経路でも代役通過の告知はマージ前に出る(monkeypatch) -> None:
    """Codex P2 (PR #404): 告知がイベント経路 (evaluate_pr) にしか無いと、
    そこでの投稿が一過性で失敗した場合、次の sweep (reverify_and_merge) が
    告知なしでマージする。

    無いと何が静かに通るか:
        D3-2 の「代役で通った痕跡」が PR に永久に残らない — degrade の可視化が
        status の 1 行 (マージ後は誰も見ない) だけになり、#400 P1 の
        「黙って代役で通る」が sweep 経路にだけ残る。
    """
    order: list[str] = []
    standin_ok = check.GateEval(
        verdict=check.Verdict(ok=True, reviewer=check.REVIEWER_STANDIN),
        changed_paths=["apps/x.ts"],
        comment_pairs=[],
        code_pr=True,
        codex_present=False,
    )
    monkeypatch.setattr(check, "evaluate_gate", lambda *a: standin_ok)
    monkeypatch.setattr(
        check,
        "post_advisory_once",
        lambda *a: order.append("notice") or True,
    )
    monkeypatch.setattr(
        check, "try_merge", lambda *a: order.append("merge") or (True, "")
    )
    monkeypatch.setattr(check, "ensure_merge_followup", lambda *a: None)
    assert check.reverify_and_merge("o/r", 1, "abc1234")
    assert order == ["notice", "merge"], "告知はマージより前"
    # Codex で通った PR には告知を出さない (ノイズ防止)
    order.clear()
    codex_ok = check.GateEval(
        verdict=check.Verdict(ok=True, reviewer=check.REVIEWER_CODEX),
        changed_paths=["apps/x.ts"],
        comment_pairs=[],
        code_pr=True,
        codex_present=True,
    )
    monkeypatch.setattr(check, "evaluate_gate", lambda *a: codex_ok)
    assert check.reverify_and_merge("o/r", 1, "abc1234")
    assert order == ["merge"]
    # 告知の投稿が失敗したらマージしない (痕跡なしのマージより遅いマージ)
    order.clear()
    monkeypatch.setattr(check, "evaluate_gate", lambda *a: standin_ok)

    def failing_post(*args):
        raise subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 500")

    monkeypatch.setattr(check, "post_advisory_once", failing_post)
    import pytest

    with pytest.raises(subprocess.CalledProcessError):
        check.reverify_and_merge("o/r", 1, "abc1234")
    assert order == [], "投稿失敗の例外は握らず、マージ API も叩かない"


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


def test_l1_pull_request経路のrunはマージを執行しない(monkeypatch) -> None:
    """Codex P1 (PR #258): pull_request イベントの run は PR 側の check.py を
    実行するため、workflow はマージ権限 (contents:write) を渡さない。

    無いと何が静かに通るか: 権限分離の意図 (PR 作成者が check.py を改変しても
    自分でマージできない) が check.py 側に残らず、将来 workflow の permissions
    だけ緩められた場合に pull_request run が黙ってマージ経路に戻る。この env
    ゲートは「PR 側コードの run はそもそも執行しない」という設計の固定。
    """
    monkeypatch.setenv("REVIEW_GATE_EXECUTE_MERGE", "false")
    monkeypatch.setattr(
        check,
        "try_merge",
        lambda *a: (_ for _ in ()).throw(
            AssertionError("執行無効の run でマージ API が呼ばれた")
        ),
    )
    assert not check.maybe_execute_merge(
        "o/r", 1, _mergeable_pr(), "abc1234", ["docs/x.md"]
    )
    # 既定 (env 未設定 = true) では従来どおり執行する
    monkeypatch.delenv("REVIEW_GATE_EXECUTE_MERGE")
    calls: list[str] = []
    monkeypatch.setattr(
        check, "try_merge", lambda *a: calls.append("merge") or (True, "")
    )
    monkeypatch.setattr(
        check, "ensure_merge_followup", lambda repo, merged_at, paths: None
    )
    assert check.maybe_execute_merge(
        "o/r", 1, _mergeable_pr(), "abc1234", ["docs/x.md"]
    )
    assert calls == ["merge"]


def test_l1_pr番号が空のときは明示エラーで落ちる(monkeypatch) -> None:
    """Codex P1-b (PR #258): pull_request_review の payload には issue.number が
    無く、yml の解決式を誤ると check.py に空文字が渡る。

    無いと何が静かに通るか: `int("")` の ValueError スタックトレースで落ち、
    「レビューの追加・取り消しが gate に反映されない」原因がログから読めない —
    取り消し後も緑 status が最大 30 分残る失敗を診断できない。
    """
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(
        check,
        "gh",
        lambda *a: (_ for _ in ()).throw(AssertionError("番号なしで API を叩いた")),
    )
    monkeypatch.setattr(check.sys, "argv", ["check.py", ""])
    assert check.main() == 1
    monkeypatch.setattr(check.sys, "argv", ["check.py"])
    assert check.main() == 1


def test_l1_マージ失敗の翻訳は405と409を正常系として区別する() -> None:
    """無いと何が静かに通るか: 405/409 (他 check 未完・base 遅れ) を想定外扱いに
    すると sweep が 30 分毎にノイズを吐く。逆に全部を正常系に丸めると
    権限退行 (404) がログから読めず、マージ執行が沈黙したまま誰も気づかない。"""
    assert "405" in merge_failure_reason("gh: Pull Request is not mergeable (HTTP 405)")
    assert "409" in merge_failure_reason("gh: Head branch was modified (HTTP 409)")
    assert "権限" in merge_failure_reason("gh: Not Found (HTTP 404)")
    assert "想定外" in merge_failure_reason("connection reset by peer")


def test_l1_405はgithubの理由をそのまま残す() -> None:
    """無いと何が静かに通るか: 405 を「まだマージできない」に丸めると、
    「承認が足りない」「保護ルールで弾かれた」「check が未完」が全部同じ 1 行になる。
    2026-08-12 に PR #286 でこれが起き、全 check 緑・auto-merge 武装済みなのに
    405 が 3 回続いた原因を切り分けられなかった (#327)。マージ執行機構が
    仕事をしていないことに、同じログが並ぶだけで誰も気づけない。"""
    approval = merge_failure_reason(
        "gh: At least 1 approving review is required by reviewers with write access. (HTTP 405)"
    )
    assert "At least 1 approving review is required" in approval

    # JSON body がそのまま乗る経路 (gh のバージョン差) でも同じ 1 文を拾う。
    # **エスケープされた引用符で切ってはいけない** — 切ると調べたい check 名が落ちる
    # (正規表現で "message": "..." を拾う実装はここで落ちる / PR #330 Codex P2)
    body = merge_failure_reason(
        '{"message": "Required status check \\"foo / bar\\" is expected.", "status": "405"}'
    )
    assert 'Required status check "foo / bar" is expected.' in body

    # 理由が取り出せないときは「取れなかった」と書く (推測で埋めない)
    assert "取り出せず" in merge_failure_reason("HTTP 405")


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
