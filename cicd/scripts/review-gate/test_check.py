"""review-gate の判定ロジックのテスト (ADR 0036)。

無いと何が静かに通るか: SHA 照合や Codex 対象判定のバグは「門が開きっぱなし」を
意味するが、門は開いていても誰も気づかない (マージは普通に成功する) ため、
判定ロジックの退行はテストでしか捕まえられない。
"""

from check import (
    decide,
    has_pm_accept,
    is_code_pr,
    minutes_between,
    sensitive_paths,
    should_request_security_review,
    should_retrigger_codex,
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
