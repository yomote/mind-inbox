"""[単体] GitHub 設定の宣言 vs 現実の差分計算 (Issue #344)。

無いと何が静かに通るか:
    - **読めなかった設定が「宣言どおり」として緑になる** — 権限が落ちて 403 が返るように
      なっても「差分 0 件」と報告され、点検していない設定が点検済みの顔をする
    - **保護を弱める操作が、強める操作より先に実行される** — apply が途中で落ちたとき、
      リポジトリが「宣言より弱い状態」で放置される経路ができる
    - **宣言の書き間違い / 改竄で保護が黙って外れる** — weaken の分類が壊れると
      `allow_weakening` の門が素通りし、ブランチ保護が確認なしに消える
    - **PUT が送らない項目が既定値に静かに戻る** — 宣言で比較している項目集合と
      PUT が送る項目集合がズレると、宣言に無い保護が適用のたびに消える
    - **GET と PUT の形の非対称 (`{"enabled": bool}` vs `bool`、`checks` vs `contexts`) の
      取り違え** — apply 直後の check が永遠に「まだ差分がある」と言い続ける、
      あるいは適用できていないのに「一致」と出る
    - **`settings.yml` の typo が無視される** — 宣言したつもりの設定が一度も適用されない
    - **PO が見た差分と実際に適用される差分がズレる** — ダイジェストが計画の内容を
      反映しなくなると、「差分を見せてから適用する」という約束が形だけになる

性質の出どころ (docs/testing/strategy.md §3.2):
    - GitHub REST API の branch protection / secret scanning / Dependabot /
      code scanning の仕様 (GET と PUT の表現、PUT が省略項目を既定値に戻すこと、
      secret scanning → push protection / Dependabot alerts → security updates の
      有効化順序)
    - Issue #344 の「満たすべき性質」 2 (差分を見せてから適用する) / 3 (check は
      読み取りのみ) と、CLAUDE.md の「取れなかったものを合格と書かない」

プロパティは hypothesis ではなく固定 seed の乱数生成で書いている
(Python 側に hypothesis が未導入 — strategy.md §3)。生成器はこのファイルの
`gen_*` にあり、seed 固定なので失敗は必ず再現する。
"""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest
from settings_diff import (
    BRANCH_BOOL_FIELDS,
    CONFIGURED,
    DISABLED,
    ENABLED,
    NONE,
    NOT_CONFIGURED,
    SECURITY_FIELDS,
    STRENGTHEN,
    WEAKEN,
    DeclarationError,
    Unavailable,
    branch_protection_payload,
    build_plan,
    diff_settings,
    normalize_branch_protection,
    normalize_security,
    plan_digest,
    render_report,
    render_snapshot,
    validate_declaration,
    weakening_operations,
)

REPO = "owner/repo"
DECLARATION_PATH = Path(__file__).resolve().parents[2] / "github" / "settings.yml"

CONTEXT_POOL = ["test", "lint-and-build", "review-gate", "iac-validate"]


# --- 生成器 -----------------------------------------------------------------


def gen_branch(rng: random.Random, protected: bool | None = None) -> dict:
    if protected is None:
        protected = rng.random() < 0.85
    if not protected:
        return {"protected": False}
    spec: dict = {"protected": True}
    if rng.random() < 0.75:
        spec["required_status_checks"] = {
            "strict": rng.random() < 0.3,
            "contexts": sorted(rng.sample(CONTEXT_POOL, rng.randint(0, 3))),
        }
    else:
        spec["required_status_checks"] = None
    if rng.random() < 0.8:
        spec["required_pull_request_reviews"] = {
            "required_approving_review_count": rng.randint(0, 2),
            "dismiss_stale_reviews": rng.random() < 0.3,
            "require_code_owner_reviews": rng.random() < 0.3,
            "require_last_push_approval": rng.random() < 0.3,
        }
    else:
        spec["required_pull_request_reviews"] = None
    for name in BRANCH_BOOL_FIELDS:
        spec[name] = rng.random() < 0.3
    spec["restrictions"] = NONE
    return spec


def gen_declaration(rng: random.Random) -> dict:
    return {
        "version": 1,
        "security": {
            name: rng.choice(choices) for name, (choices, _p) in SECURITY_FIELDS.items()
        },
        "branch_protection": {
            branch: gen_branch(rng) for branch in ("main", "release")
        },
    }


def gen_actual(rng: random.Random, declaration: dict, allow_unavailable: bool) -> dict:
    security: dict = {}
    for name, (choices, _p) in SECURITY_FIELDS.items():
        if allow_unavailable and rng.random() < 0.2:
            security[name] = Unavailable("HTTP 403 (権限なし / 機能が無効)")
        else:
            security[name] = rng.choice(choices)
    branches: dict = {}
    for branch in declaration["branch_protection"]:
        if allow_unavailable and rng.random() < 0.2:
            branches[branch] = Unavailable("ブランチがありません")
        else:
            spec = gen_branch(rng)
            # 現実には制限が付いていることがある (宣言には書けない)
            if spec["protected"] and rng.random() < 0.2:
                spec["restrictions"] = CONFIGURED
            branches[branch] = spec
    return {"security": security, "branch_protection": branches}


# --- GitHub の GET 表現のモデル ---------------------------------------------


def as_get_shape(payload: dict) -> dict:
    """PUT の body を、GitHub が GET で返す形に写す (テスト側のモデル)。

    実 API の非対称をここに 1 か所だけ写し取り、正規化のラウンドトリップを見る。
    """
    raw: dict = {}
    rsc = payload["required_status_checks"]
    if rsc is not None:
        raw["required_status_checks"] = {
            "strict": rsc["strict"],
            # 新しい API 表現 (contexts ではなく checks で返る)
            "checks": [{"context": c, "app_id": None} for c in rsc["contexts"]],
        }
    rpr = payload["required_pull_request_reviews"]
    if rpr is not None:
        raw["required_pull_request_reviews"] = dict(rpr)
    for name in BRANCH_BOOL_FIELDS:
        raw[name] = {"enabled": payload[name]}
    # restrictions は未設定なら GET の応答にキーごと現れない
    return raw


def apply_to_model(plan, reality: dict, declaration: dict) -> dict:
    """計画を「現実のモデル」に適用する (実 API の代わり)。"""
    reality = copy.deepcopy(reality)
    for op in plan:
        kind, name = op.target.split(".", 1)
        if kind == "security":
            reality["security"][name] = declaration["security"][name]
        else:
            branch = name
            if op.method == "DELETE":
                reality["branch_protection"][branch] = normalize_branch_protection(None)
            else:
                reality["branch_protection"][branch] = normalize_branch_protection(
                    as_get_shape(op.payload)
                )
    return reality


def minimal_declaration() -> dict:
    return {
        "version": 1,
        "security": {
            name: choices[-1] for name, (choices, _p) in SECURITY_FIELDS.items()
        },
        "branch_protection": {
            "main": {
                "protected": True,
                "required_status_checks": {"strict": False, "contexts": ["test"]},
                "required_pull_request_reviews": {
                    "required_approving_review_count": 0,
                    "dismiss_stale_reviews": False,
                    "require_code_owner_reviews": False,
                    "require_last_push_approval": False,
                },
                "restrictions": NONE,
                **{name: False for name in BRANCH_BOOL_FIELDS},
            }
        },
    }


# --- 宣言の検証 -------------------------------------------------------------


def test_単体_実際の宣言ファイルが検証を通る():
    """cicd/github/settings.yml が壊れていたら、check は一度も走らない。"""
    yaml = pytest.importorskip(
        "yaml", reason="PyYAML が無い環境では宣言ファイルを検証できない"
    )
    with DECLARATION_PATH.open(encoding="utf-8") as handle:
        doc = yaml.safe_load(handle)
    validate_declaration(doc)  # 例外が出なければ良い


def test_単体_宣言の書き漏らしは全キーで検出される():
    """1 項目でも書き漏らすと、その項目は比較されないまま PUT の既定値に戻る。"""
    base = minimal_declaration()
    for key in list(base["branch_protection"]["main"]):
        broken = copy.deepcopy(base)
        del broken["branch_protection"]["main"][key]
        with pytest.raises(DeclarationError):
            validate_declaration(broken)
    for key in list(base["security"]):
        broken = copy.deepcopy(base)
        del broken["security"][key]
        with pytest.raises(DeclarationError):
            validate_declaration(broken)


def test_単体_未知キーは黙って無視されない():
    """typo (`secret_scaning`) を無視すると、宣言したつもりの設定が一度も適用されない。"""
    for path in (
        ("security", "secret_scaning"),
        ("branch_protection", "main", "allow_forcepushes"),
    ):
        broken = minimal_declaration()
        node = broken
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = True
        with pytest.raises(DeclarationError):
            validate_declaration(broken)

    broken = minimal_declaration()
    broken["branch_protection"]["main"]["required_status_checks"]["contexts_"] = []
    with pytest.raises(DeclarationError):
        validate_declaration(broken)


def test_単体_宣言できる制限はnoneだけ():
    """宣言から復元できない状態 (誰を許すか) を宣言させない。

    許すと「適用しても差分が消えない」状態が固定化する。
    """
    broken = minimal_declaration()
    broken["branch_protection"]["main"]["restrictions"] = CONFIGURED
    with pytest.raises(DeclarationError):
        validate_declaration(broken)


# --- PUT と宣言の対応 -------------------------------------------------------


def test_単体_PUT_が送る項目は全部宣言で比較されている():
    """PUT は送らなかった項目を既定値に戻す。

    比較していない項目を PUT が送ると、その保護は適用のたびに静かに消える。
    """
    spec = minimal_declaration()["branch_protection"]["main"]
    payload_keys = set(branch_protection_payload(spec))
    declared_keys = set(spec) - {"protected"}
    # restrictions は「比較はするが宣言からは復元しない」ので payload では None 固定
    assert payload_keys == declared_keys, (
        f"PUT が送る項目と宣言の項目がズレています: "
        f"PUT のみ={sorted(payload_keys - declared_keys)} / "
        f"宣言のみ={sorted(declared_keys - payload_keys)}"
    )


def test_単体_正規化は宣言へのラウンドトリップで戻る():
    """宣言 → PUT body → GitHub の GET 表現 → 正規化 で元に戻る。

    GET と PUT の非対称を取り違えると、apply 直後の check が永遠に赤いままになる。
    """
    rng = random.Random(20260812)
    for _ in range(200):
        spec = gen_branch(rng, protected=True)
        restored = normalize_branch_protection(
            as_get_shape(branch_protection_payload(spec))
        )
        assert restored == spec


def test_単体_未保護はprotected_falseに正規化される():
    assert normalize_branch_protection(None) == {"protected": False}


# --- 差分 -------------------------------------------------------------------


def test_単体_読めなかった項目は一致にも差分にもならない():
    """403 が返るようになっても「差分 0 件」と言わない。

    (取れなかったものを合格と書かない — CLAUDE.md / security-sweep と同じ規律)
    """
    rng = random.Random(4242)
    for _ in range(200):
        declaration = validate_declaration(gen_declaration(rng))
        actual = gen_actual(rng, declaration, allow_unavailable=True)
        report = diff_settings(declaration, actual)

        unavailable_paths = {path for path, _reason in report.unavailable}
        touched = {f.path for f in report.findings} | set(report.matched)
        for path in unavailable_paths:
            assert not any(t == path or t.startswith(path + ".") for t in touched)
        if unavailable_paths:
            # 未検証があるなら「全部見た」とは言わせない。差分 0 でも complete=False
            assert not report.complete
            # レポートにも必ず未検証として出る (数だけでも見えなければ気づけない)
            assert "未検証" in render_report(REPO, report, (), "check")

        plan = build_plan(declaration, report, REPO)
        for op in plan:
            for finding in op.findings:
                assert finding.path not in unavailable_paths


def test_単体_差分がゼロのときだけ計画が空になる():
    """差分があるのに計画が空 = apply しても直らない。

    逆に差分ゼロで計画が立つ = 何も変わらない PUT を打ち続ける (冪等性が壊れる)。
    """
    rng = random.Random(777)
    for _ in range(200):
        declaration = validate_declaration(gen_declaration(rng))
        actual = gen_actual(rng, declaration, allow_unavailable=False)
        report = diff_settings(declaration, actual)
        plan = build_plan(declaration, report, REPO)
        assert bool(plan) == bool(report.findings)


def test_単体_計画を適用すると差分が消えて冪等になる():
    """計画が差分を取りこぼすと、apply しても check が永遠に赤いままになる。"""
    rng = random.Random(31337)
    for _ in range(200):
        declaration = validate_declaration(gen_declaration(rng))
        actual = gen_actual(rng, declaration, allow_unavailable=False)
        report = diff_settings(declaration, actual)
        plan = build_plan(declaration, report, REPO)

        after = apply_to_model(plan, actual, declaration)
        report_after = diff_settings(declaration, after)
        assert report_after.findings == ()
        # 冪等: もう一度計画を立てても空
        assert build_plan(declaration, report_after, REPO) == ()


# --- 順序と安全性 -----------------------------------------------------------


def test_単体_弱める操作は強める操作より後ろに来る():
    """途中で落ちたときに「保護を外したところで止まる」経路を作らない。"""
    rng = random.Random(99)
    for _ in range(200):
        declaration = validate_declaration(gen_declaration(rng))
        actual = gen_actual(rng, declaration, allow_unavailable=True)
        plan = build_plan(declaration, diff_settings(declaration, actual), REPO)
        risks = [op.risk for op in plan]
        assert risks == sorted(risks, key=lambda r: 0 if r == STRENGTHEN else 1)


def test_単体_有効化の依存順を守る():
    """secret scanning より先に push protection を有効化すると API が失敗する。

    無効化はその逆順 (push protection を外してから secret scanning を外す)。
    """
    declaration = validate_declaration(minimal_declaration())
    actual = {
        "security": {name: DISABLED for name in SECURITY_FIELDS},
        "branch_protection": {
            "main": normalize_branch_protection(
                as_get_shape(
                    branch_protection_payload(declaration["branch_protection"]["main"])
                )
            )
        },
    }
    actual["security"]["code_scanning_default_setup"] = NOT_CONFIGURED
    plan = build_plan(declaration, diff_settings(declaration, actual), REPO)
    ids = [op.op_id for op in plan]
    assert ids.index("10-security-secret-scanning") < ids.index(
        "11-security-secret-scanning-push-protection"
    )
    assert ids.index("12-security-dependabot-alerts") < ids.index(
        "13-security-dependabot-security-updates"
    )

    # すべて無効化する宣言なら、依存の逆順に並ぶ
    off = copy.deepcopy(minimal_declaration())
    off["security"] = {
        name: choices[0] for name, (choices, _p) in SECURITY_FIELDS.items()
    }
    off = validate_declaration(off)
    all_on = {
        "security": {
            name: choices[-1] for name, (choices, _p) in SECURITY_FIELDS.items()
        },
        "branch_protection": actual["branch_protection"],
    }
    plan_off = build_plan(off, diff_settings(off, all_on), REPO)
    ids_off = [op.op_id for op in plan_off]
    assert ids_off.index("11-security-secret-scanning-push-protection") < ids_off.index(
        "10-security-secret-scanning"
    )
    assert ids_off.index("13-security-dependabot-security-updates") < ids_off.index(
        "12-security-dependabot-alerts"
    )
    assert all(op.risk == WEAKEN for op in plan_off)


def test_単体_ブランチ保護は強めるとき最初に弱めるとき最後に来る():
    """一番価値のある保護を、強めるときは真っ先に立て、外すときは最後に外す。

    (op_id の並びは名前空間ではなく先頭の数字で決まる — 実装時に一度間違えた)
    """
    declaration = validate_declaration(minimal_declaration())
    nothing = {
        "security": {
            name: choices[0] for name, (choices, _p) in SECURITY_FIELDS.items()
        },
        "branch_protection": {"main": normalize_branch_protection(None)},
    }
    plan = build_plan(declaration, diff_settings(declaration, nothing), REPO)
    assert plan[0].target == "branch_protection.main"
    assert all(op.risk == STRENGTHEN for op in plan)

    off = copy.deepcopy(minimal_declaration())
    off["security"] = {
        name: choices[0] for name, (choices, _p) in SECURITY_FIELDS.items()
    }
    off["branch_protection"]["main"] = {"protected": False}
    off = validate_declaration(off)
    everything = {
        "security": {
            name: choices[-1] for name, (choices, _p) in SECURITY_FIELDS.items()
        },
        "branch_protection": {
            "main": normalize_branch_protection(
                as_get_shape(
                    branch_protection_payload(declaration["branch_protection"]["main"])
                )
            )
        },
    }
    plan_off = build_plan(off, diff_settings(off, everything), REPO)
    assert plan_off[-1].target == "branch_protection.main"
    assert all(op.risk == WEAKEN for op in plan_off)


def test_単体_保護を外す方向は必ずweakenに分類される():
    """weaken の分類が壊れると allow_weakening の門が素通りする。"""
    declaration = validate_declaration(minimal_declaration())
    protected = normalize_branch_protection(
        as_get_shape(
            branch_protection_payload(declaration["branch_protection"]["main"])
        )
    )

    # (1) 現実は保護済み、宣言が「保護しない」
    unprotect = copy.deepcopy(minimal_declaration())
    unprotect["branch_protection"]["main"] = {"protected": False}
    unprotect = validate_declaration(unprotect)
    actual = {
        "security": dict(declaration["security"]),
        "branch_protection": {"main": protected},
    }
    plan = build_plan(unprotect, diff_settings(unprotect, actual), REPO)
    assert [op.method for op in plan] == ["DELETE"]
    assert weakening_operations(plan) == plan

    # (2) 現実の方が強い = 宣言どおりにすると弱くなる:
    #     required check を減らす / 承認数を減らす / push 制限を外す
    for mutate in (
        lambda s: s["required_status_checks"]["contexts"].append("extra"),
        lambda s: s["required_pull_request_reviews"].update(
            {"required_approving_review_count": 2}
        ),
        lambda s: s.update({"restrictions": CONFIGURED}),
        lambda s: s.update({"required_linear_history": True}),
    ):
        stronger_reality = copy.deepcopy(protected)
        mutate(stronger_reality)
        actual = {
            "security": dict(declaration["security"]),
            "branch_protection": {"main": stronger_reality},
        }
        plan = build_plan(declaration, diff_settings(declaration, actual), REPO)
        assert len(plan) == 1
        assert plan[0].risk == WEAKEN, plan[0].findings

    # (3) 宣言の方が緩い (force push / ブランチ削除を許す) のも弱める
    for key in ("allow_force_pushes", "allow_deletions"):
        loose = copy.deepcopy(minimal_declaration())
        loose["branch_protection"]["main"][key] = True
        loose = validate_declaration(loose)
        actual = {
            "security": dict(declaration["security"]),
            "branch_protection": {"main": copy.deepcopy(protected)},
        }
        plan = build_plan(loose, diff_settings(loose, actual), REPO)
        assert len(plan) == 1
        assert plan[0].risk == WEAKEN, plan[0].findings

    # (4) 逆に、required check を増やす方向は strengthen
    weaker_reality = copy.deepcopy(protected)
    weaker_reality["required_status_checks"]["contexts"] = []
    actual = {
        "security": dict(declaration["security"]),
        "branch_protection": {"main": weaker_reality},
    }
    plan = build_plan(declaration, diff_settings(declaration, actual), REPO)
    assert plan[0].risk == STRENGTHEN


# --- ダイジェスト -----------------------------------------------------------


def test_単体_ダイジェストは計画の内容で決まる():
    """PO が見た差分と実際に適用される差分がズレる経路を塞ぐ (Issue #344 の性質 2)。"""
    rng = random.Random(2024)
    seen: dict[str, str] = {}
    for _ in range(200):
        declaration = validate_declaration(gen_declaration(rng))
        actual = gen_actual(rng, declaration, allow_unavailable=False)
        plan = build_plan(declaration, diff_settings(declaration, actual), REPO)
        digest = plan_digest(plan)
        key = json.dumps(
            [[o.op_id, o.method, o.endpoint, o.payload] for o in plan], sort_keys=True
        )
        # 同じ計画 → 同じダイジェスト / 違う計画 → 違うダイジェスト
        if digest in seen:
            assert seen[digest] == key
        seen[digest] = key
        assert plan_digest(plan) == digest  # 安定 (再計算しても変わらない)


# --- 出力 -------------------------------------------------------------------


def test_単体_スナップショットは決定的で時刻を含まない():
    """キー順や時刻が揺れると、git の履歴が「いつ設定が変わったか」を語れなくなる。"""
    rng = random.Random(5)
    declaration = validate_declaration(gen_declaration(rng))
    actual = gen_actual(rng, declaration, allow_unavailable=True)

    first = render_snapshot(REPO, actual)
    shuffled = {
        "security": dict(reversed(list(actual["security"].items()))),
        "branch_protection": dict(reversed(list(actual["branch_protection"].items()))),
    }
    assert render_snapshot(REPO, shuffled) == first
    assert first.endswith("\n")
    doc = json.loads(first)
    assert doc["repository"] == REPO
    text = first.lower()
    for volatile in ("observedat", "recordedat", "timestamp", "run_id", "updated_at"):
        assert volatile not in text
    # 未検証は値ではなくマークとして残る (disabled と偽らない)
    for value in doc["security"].values():
        assert value in (ENABLED, DISABLED, CONFIGURED, NOT_CONFIGURED) or set(
            value
        ) == {"unavailable"}


def test_単体_unmanaged_は比較も適用もされないが必ず名指しで出る():
    """「この環境では読めない」機能の逃げ道。

    黙って見ないのと違い、レポートに毎回名前が出る。ここが黙ると、宣言に
    unmanaged と書いた項目が「点検済み」の顔で消える。
    """
    declaration = copy.deepcopy(minimal_declaration())
    declaration["security"]["code_scanning_default_setup"] = "unmanaged"
    declaration = validate_declaration(declaration)
    for observed in (
        NOT_CONFIGURED,
        CONFIGURED,
        Unavailable("HTTP 403 (権限なし / 機能が無効)"),
    ):
        actual = {
            "security": {
                **declaration["security"],
                "code_scanning_default_setup": observed,
            },
            "branch_protection": {
                "main": normalize_branch_protection(
                    as_get_shape(
                        branch_protection_payload(
                            declaration["branch_protection"]["main"]
                        )
                    )
                )
            },
        }
        report = diff_settings(declaration, actual)
        assert report.unmanaged == ("security.code_scanning_default_setup",)
        assert report.findings == ()
        assert report.unavailable == ()
        assert build_plan(declaration, report, REPO) == ()
        assert "管理対象外" in render_report(REPO, report, (), "check")


def test_単体_レポートは比較していない領域を常に出す():
    """「差分 0 = 全部宣言どおり」と読ませない (silent caps 禁止)。"""
    declaration = validate_declaration(minimal_declaration())
    actual = {
        "security": dict(declaration["security"]),
        "branch_protection": {
            "main": normalize_branch_protection(
                as_get_shape(
                    branch_protection_payload(declaration["branch_protection"]["main"])
                )
            )
        },
    }
    report = diff_settings(declaration, actual)
    assert report.in_sync
    text = render_report(REPO, report, (), "check")
    assert "比較していない領域" in text
    assert "ruleset" in text


# --- security の正規化 ------------------------------------------------------


def test_単体_security_の正規化は未取得を維持する():
    unavailable = Unavailable("HTTP 403 (権限なし / 機能が無効)")
    out = normalize_security(unavailable, unavailable, unavailable, unavailable)
    assert all(isinstance(v, Unavailable) for v in out.values())

    out = normalize_security(
        {
            "security_and_analysis": {
                "secret_scanning": {"status": "enabled"},
                "secret_scanning_push_protection": {"status": "disabled"},
            }
        },
        True,
        {"enabled": True, "paused": False},
        {"state": "configured"},
    )
    assert out == {
        "secret_scanning": ENABLED,
        "secret_scanning_push_protection": DISABLED,
        "dependabot_alerts": ENABLED,
        "dependabot_security_updates": ENABLED,
        "code_scanning_default_setup": CONFIGURED,
    }
