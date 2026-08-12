"""[単体] GitHub 設定の宣言 vs 現実の差分計算 (Issue #344)。

無いと何が静かに通るか:
    - **読めなかった設定が「宣言どおり」として緑になる** — 権限が落ちて 403 が返るように
      なっても「差分 0 件」と報告され、点検していない設定が点検済みの顔をする
    - **宣言のブランチ名が API のパスを組み替える** — `branch_protection` のキーは
      `repos/{owner}/{repo}/branches/{branch}/protection` に文字列連結されるので、
      `../../../../repos/victim/repo/branches/main` と書くと**別リポジトリへの PUT**
      が組める。しかもその操作は `strengthen` に分類され、`allow_weakening` の門でも
      止まらない (Issue #372 major 1)
    - **apply の安全弁 (ダイジェスト照合 / 弱化ゲート) が退行する** — PO が見た計画と
      違う計画が黙って適用される / 保護を外す操作が確認なしに実行される
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
    APPLY_DIGEST_MISMATCH,
    APPLY_NOTHING_TO_DO,
    APPLY_WEAKENING_NOT_ALLOWED,
    BRANCH_BOOL_FIELDS,
    CONFIGURED,
    DISABLED,
    ENABLED,
    NONE,
    NEGATIVE,
    NOT_CONFIGURED,
    POSITIVE,
    SECURITY_FIELDS,
    STRENGTHEN,
    UNMANAGED,
    WEAKEN,
    DeclarationError,
    Unavailable,
    branch_protection_payload,
    build_plan,
    decide_apply,
    diff_settings,
    direction_of,
    normalize_branch_protection,
    normalize_security,
    plan_digest,
    render_report,
    render_snapshot,
    validate_branch_name,
    validate_declaration,
    validate_repository,
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


def test_単体_宣言のブランチ名は検証される():
    """ブランチ名は API のパスに直結する (Issue #372 major 1)。

    無いと何が静かに通るか: `branch_protection` のキーに
    `../../../../repos/victim/repo/branches/main` と書くと、
    `PUT repos/{owner}/{repo}/branches/../../../../repos/victim/repo/branches/main/protection`
    という**別リポジトリへの書き込み**が組める。しかもそれは「宣言どおりにする」
    操作なので `strengthen` に分類され、`allow_weakening` の門でも止まらない。
    """
    traversal = "../../../../repos/victim/repo/branches/main"
    for bad in (
        traversal,
        "..",
        "main/../../x",
        "/main",
        "main/",
        "a//b",
        "",
        "main branch",  # 空白
        "main?per_page=1",  # クエリを生やす
        "main#x",
        "main%2f..%2fx",  # パーセントエンコード
        "main\n",
        "main/protection\nX",
    ):
        broken = minimal_declaration()
        spec = broken["branch_protection"].pop("main")
        broken["branch_protection"][bad] = spec
        with pytest.raises(DeclarationError):
            validate_declaration(broken)

    # 普通のブランチ名は通る (検証が厳しすぎて宣言が書けないのも困る)
    for good in (
        "main",
        "release",
        "feature/foo-bar",
        "v1.2.3",
        "dependabot/npm_and_yarn/x",
    ):
        ok = minimal_declaration()
        spec = ok["branch_protection"].pop("main")
        ok["branch_protection"][good] = spec
        assert validate_declaration(ok)
        assert validate_branch_name(good) == good


def test_単体_計画のAPIパスは対象リポジトリの外に出ない():
    """ブランチ名の検証は**書き込みのパスを組む直前でも**効く。

    validate_declaration を通さない dict が build_plan に渡る経路ができても、
    別リポジトリを指す endpoint が組み上がってはいけない。
    """
    sneaky = minimal_declaration()
    spec = sneaky["branch_protection"].pop("main")
    sneaky["branch_protection"]["../../../../repos/victim/repo/branches/main"] = spec
    report = diff_settings(sneaky, {"security": {}, "branch_protection": {}})
    with pytest.raises(DeclarationError):
        build_plan(sneaky, report, REPO)

    # 正常な宣言では、endpoint は必ず対象リポジトリの下に閉じている
    rng = random.Random(606)
    for _ in range(50):
        declaration = validate_declaration(gen_declaration(rng))
        actual = gen_actual(rng, declaration, allow_unavailable=False)
        plan = build_plan(declaration, diff_settings(declaration, actual), REPO)
        for op in plan:
            assert op.endpoint == f"repos/{REPO}" or op.endpoint.startswith(
                f"repos/{REPO}/"
            )
            assert ".." not in op.endpoint


def test_単体_適用先リポジトリも検証される():
    """`../victim` はスラッシュ 1 個なので「owner/repo の形」の検査だけでは抜ける。"""
    for bad in (
        "",
        "owner",
        "../victim",
        "owner/..",
        "owner/repo/extra",
        "own er/repo",
        "owner/repo?x=1",
        None,
    ):
        with pytest.raises(DeclarationError):
            validate_repository(bad)
    for good in ("yomote/mind-inbox", "o.w-n_er/re.po-1"):
        assert validate_repository(good) == good


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


def test_単体_push制限は現実から読み取られ弱める差分になる():
    """`restrictions` を常に `none` と読むと何が静かに通るか (Issue #372 major 2):

    宣言は `none` しか書けないので、現実の制限を読まないと**常に一致**する。
    すると「現実に push 制限がある」ことが差分として出ず、他の項目のついでに
    打たれる `PUT ... restrictions: null` が **strengthen 扱い**で通り、
    push 制限が確認なしに外れる。
    """
    declaration = validate_declaration(minimal_declaration())
    raw = as_get_shape(
        branch_protection_payload(declaration["branch_protection"]["main"])
    )
    # 制限があるときだけ GET に restrictions が現れる (中身は取り込まない)
    raw_restricted = dict(raw)
    raw_restricted["restrictions"] = {
        "users": [],
        "teams": [{"slug": "release-managers"}],
        "apps": [],
    }
    assert normalize_branch_protection(raw_restricted)["restrictions"] == CONFIGURED
    # 制限が無ければ none (常に configured と読む壊れ方でも落ちる)
    assert normalize_branch_protection(raw)["restrictions"] == NONE

    actual = {
        "security": dict(declaration["security"]),
        "branch_protection": {"main": normalize_branch_protection(raw_restricted)},
    }
    report = diff_settings(declaration, actual)
    assert [f.path for f in report.findings] == ["branch_protection.main.restrictions"]
    plan = build_plan(declaration, report, REPO)
    # 制限を外す = 弱める。allow_weakening 無しには適用されない
    assert weakening_operations(plan) == plan
    assert plan[0].payload["restrictions"] is None


def test_単体_どちらとも言えない差は弱める側に倒す():
    """判定不能を strengthen に倒すと何が静かに通るか:

    意味を決められない差 (集合の入れ替え・値の横移動) が「強める」として
    `allow_weakening` の門を素通りし、確認なしに適用される。安全側 = weaken。
    """
    # 順序を決められない値の組 (どちらも「強さ」としては同じ位置)
    ambiguous = (None, False, NONE, DISABLED, NOT_CONFIGURED, 0)
    for declared in ambiguous:
        for actual in ambiguous:
            if declared == actual or declared is actual:
                continue
            for polarity in (POSITIVE, NEGATIVE):
                assert direction_of(declared, actual, polarity) == WEAKEN, (
                    declared,
                    actual,
                    polarity,
                )
    # 集合は包含でしか強められない。入れ替え / 減少はどちらも弱める
    assert direction_of(("a",), ("b",), POSITIVE) == WEAKEN
    assert direction_of((), ("a",), POSITIVE) == WEAKEN
    assert direction_of(("a", "b"), ("a",), POSITIVE) == STRENGTHEN


# --- 差分 -------------------------------------------------------------------


def expected_unavailable_paths(declaration: dict, actual: dict) -> set[str]:
    """**入力だけ**を見て「未検証になるべきパス」を数える。

    出力 (`report.unavailable`) を起点に数えると、出力が空になる壊れ方
    (Unavailable を matched に丸める) をテストが検出できない — 検証ごと空振りする。
    Issue #372 major 2 はこの空振りだった。
    """
    expected: set[str] = set()
    for name, declared in declaration["security"].items():
        if declared == UNMANAGED:
            continue  # 比較しないので未検証にもならない
        # 欠けているキーも「未取得」として扱われる (diff_settings の既定と同じ)
        current = actual.get("security", {}).get(name, Unavailable("未取得"))
        if isinstance(current, Unavailable):
            expected.add(f"security.{name}")
    for branch in declaration["branch_protection"]:
        current = actual.get("branch_protection", {}).get(branch, Unavailable("未取得"))
        if isinstance(current, Unavailable):
            expected.add(f"branch_protection.{branch}")
    return expected


def test_単体_読めなかった項目は一致にも差分にもならない():
    """403 が返るようになっても「差分 0 件」と言わない。

    (取れなかったものを合格と書かない — CLAUDE.md / security-sweep と同じ規律)

    **入力起点**で書く: 「Unavailable を渡した項目は、必ず report.unavailable に
    現れる」。出力を起点にすると、Unavailable を matched に丸める壊れ方で
    ループが 0 周になり、テストが緑のまま素通りする (Issue #372 major 2)。
    """
    rng = random.Random(4242)
    saw_unavailable = False
    saw_fully_readable = False
    for _ in range(200):
        declaration = validate_declaration(gen_declaration(rng))
        actual = gen_actual(rng, declaration, allow_unavailable=True)
        expected = expected_unavailable_paths(declaration, actual)
        report = diff_settings(declaration, actual)

        # 過不足なく一致すること (丸めても、逆に読めたものを未検証にしても落ちる)
        assert {path for path, _reason in report.unavailable} == expected
        # 未検証があるなら「全部見た」とは言わせない。差分 0 でも complete=False
        assert report.complete == (not expected)

        touched = {f.path for f in report.findings} | set(report.matched)
        for path in expected:
            assert not any(t == path or t.startswith(path + ".") for t in touched)

        plan = build_plan(declaration, report, REPO)
        for op in plan:
            for finding in op.findings:
                assert finding.path not in expected

        if expected:
            saw_unavailable = True
            # レポートにも必ず出る。**どれが読めなかったか**まで出す (数だけだと
            # 「何を点検していないのか」が分からず、結局「異常なし」と読まれる)
            text = render_report(REPO, report, plan, "check")
            assert "未検証" in text
            for path in expected:
                assert path in text
        else:
            saw_fully_readable = True

    # 生成が偏って一方しか出ていないなら、上の assert は空振りしている
    assert saw_unavailable and saw_fully_readable


def test_単体_全部読めなかったときに一致とは書かない():
    """judge が再現した実害そのもの (Issue #372 major 2)。

    全 API が 403 のとき、`drift:0 / in_sync:true` だけを見ると「宣言どおり」に
    見える。**未検証 = 全項目**であり、レポートも「一致しています」とは書かない。
    """
    declaration = validate_declaration(minimal_declaration())
    blind = Unavailable("HTTP 403 (権限なし / 機能が無効)")
    actual = {
        "security": {name: blind for name in SECURITY_FIELDS},
        "branch_protection": {"main": blind},
    }
    report = diff_settings(declaration, actual)

    assert {path for path, _ in report.unavailable} == {
        *(f"security.{name}" for name in SECURITY_FIELDS),
        "branch_protection.main",
    }
    assert report.matched == ()
    assert report.findings == ()
    assert not report.complete  # 差分 0 でも「点検できた」ではない
    assert build_plan(declaration, report, REPO) == ()

    text = render_report(REPO, report, (), "check")
    # 「一致しています」と言い切る文は、未検証があるときは出してはいけない
    assert "宣言と現実は一致しています。" not in text
    assert f"未検証: **{len(report.unavailable)} 項目**" in text


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


# --- apply の安全弁 ---------------------------------------------------------


def test_単体_applyはダイジェストが一致しない限り何もしない():
    """無いと何が静かに通るか (Issue #372 minor 3):

    PO が check の出力で見た差分と、apply の時点で計算し直した計画がズレていても
    適用される — 「差分を見せてから適用する」約束 (Issue #344 の性質 2) が形だけになる。
    ダイジェスト照合は **allow_weakening があっても先に効く**。
    """
    rng = random.Random(20260813)
    saw_plan = False
    for _ in range(200):
        declaration = validate_declaration(gen_declaration(rng))
        actual = gen_actual(rng, declaration, allow_unavailable=False)
        plan = build_plan(declaration, diff_settings(declaration, actual), REPO)
        digest = plan_digest(plan)
        if not plan:
            for allow in (False, True):
                assert decide_apply(plan, digest, allow).outcome == APPLY_NOTHING_TO_DO
                assert not decide_apply(plan, digest, allow).proceed
            continue
        saw_plan = True
        wrong_last = "0" if digest[-1] != "0" else "1"
        for wrong in ("", "0" * len(digest), digest[:-1] + wrong_last, digest[:-1]):
            for allow in (False, True):
                decision = decide_apply(plan, wrong, allow)
                assert not decision.proceed
                assert decision.is_refusal  # 実行しなかっただけでなく run を落とす
                assert decision.outcome == APPLY_DIGEST_MISMATCH
    assert saw_plan  # 計画が 1 度も立たないなら上のループは空振り


def test_単体_弱める操作は許可がない限り一件も実行されない():
    """無いと何が静かに通るか: 保護を外す操作が確認なしに実行される。

    弱化ゲートは**計画単位**で効く (弱める操作が 1 件でもあれば、強める操作も
    含めて何も実行しない) — 部分適用は「弱いところで止まる」状態を作りうる。
    """
    rng = random.Random(1213)
    saw_weak = False
    for _ in range(200):
        declaration = validate_declaration(gen_declaration(rng))
        actual = gen_actual(rng, declaration, allow_unavailable=False)
        plan = build_plan(declaration, diff_settings(declaration, actual), REPO)
        if not plan or not weakening_operations(plan):
            continue
        saw_weak = True
        digest = plan_digest(plan)
        refused = decide_apply(plan, digest, False)
        assert not refused.proceed
        assert refused.outcome == APPLY_WEAKENING_NOT_ALLOWED
        assert refused.is_refusal
        # 明示の許可があれば通る (門であって通せんぼではない)
        assert decide_apply(plan, digest, True).proceed
    assert saw_weak

    # 強めるだけの計画は許可なしで通る (門が「常に拒む」になっていないこと。
    # 常に拒むと apply が一度も成立せず、この仕組み自体が使われなくなる)
    declaration = validate_declaration(minimal_declaration())
    nothing = {
        "security": {
            name: choices[0] for name, (choices, _p) in SECURITY_FIELDS.items()
        },
        "branch_protection": {"main": normalize_branch_protection(None)},
    }
    strengthen_only = build_plan(declaration, diff_settings(declaration, nothing), REPO)
    assert strengthen_only and weakening_operations(strengthen_only) == ()
    assert decide_apply(strengthen_only, plan_digest(strengthen_only), False).proceed


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


def test_単体_security_and_analysisが読めないときdisabledと断定しない():
    """無いと何が静かに通るか (Issue #372 minor):

    `GET /repos/{o}/{r}` は 200 でも、**admin 権限が無い呼び出しでは
    `security_and_analysis` ごと応答から消える**。「無い = 無効」と読むと、
    読めていないだけの状態が `"secret_scanning": "disabled"` という**事実**として
    public のデータブランチに永続し、さらに「宣言どおり enabled に戻す」計画が
    毎回立つ (何も壊れていないのに apply を促す)。
    """
    for repo_doc in (
        {"full_name": "owner/repo"},  # キーごと無い (admin でない呼び出し)
        {"security_and_analysis": None},  # null で返る
        None,
    ):
        out = normalize_security(
            repo_doc, True, {"enabled": True}, {"state": "configured"}
        )
        assert isinstance(out["secret_scanning"], Unavailable), repo_doc
        assert isinstance(out["secret_scanning_push_protection"], Unavailable), repo_doc
        # 読めた項目まで巻き添えにしない
        assert out["dependabot_alerts"] == ENABLED

    # 読めているなら今までどおり disabled と書く (未検証で埋め尽くさない)
    out = normalize_security(
        {"security_and_analysis": {"secret_scanning": {"status": "disabled"}}},
        True,
        {"enabled": True},
        {"state": "configured"},
    )
    assert out["secret_scanning"] == DISABLED
    assert out["secret_scanning_push_protection"] == DISABLED
