"""[単体] 層ガード (persistent_layer_guard) の「表記ゆれ」プロパティテスト (hypothesis / #431)。

仕様の出どころ: [ADR 0056](../../../docs/adr/0056-management-and-app-layers-with-backup-based-data-protection.md) D1 /
`cicd/scripts/env/README.md` の層ガードの表と、`persistent_layer_guard.py` の
`normalize_rg` / `management_rg_set` の docstring。性質にしたのは次の 1 文:

> **Azure が同一視する表現は、ガードも同一視する。**
> Azure は RG 名・タグのキー/値を大文字小文字で区別しない。さらに RG 名は
> `cleanup-env.sh` の環境変数 (`RG` / `MGMT_RG` / `PROTECTED_RESOURCE_NAMES`)
> 経由で渡るので、受け渡しで混ざる前後の空白も同じ名前として扱う。
> 加えて **既定の管理系 RG (`rg-mgmt-mindbox`) はどんな設定でも撤収できない**
> (`MGMT_RG` は足すだけ / `ALLOW_PROTECTED_DELETE` でも通らない)。

無いと何が静かに通るか: 表記ゆれで正規化をすり抜けると、ガードは**エラーを出さずに
`allowed=True` を返す** — つまり `az group delete` がそのまま走り、管理系 RG や
復元を実証していない Cosmos が消える。落ちるのは削除のあとで、そのときにはもう
戻せない (2026-08-14 の内部 judge が「可変値 1 つで保護が消える」経路を実測した層)。
例ベース (`test_persistent_layer_guard.py`) は代表的な表記 (`RG-MGMT-MINDBOX` など) を
固定する担当で、こちらは**大小 × 前後空白 × 他の全引数**の組み合わせを道具に探させる。

反例が出たときの手順 (property-based-testing.md §3 / fast-check 側と同じ):
hypothesis が出す最小反例を、まず `test_persistent_layer_guard.py` に例ベースで
固定してから実装を直す。**実装の穴だと分かっても、このファイルからは直さない**
(ガード本体は保護対象の実装 — 直すのは裁定と別 PR)。

seed は固定しない (TS 側と同じ)。`max_examples` は 200
(このファイル 7 本で約 3 秒 / 実測。純粋関数のみで I/O は無い)。
"""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from persistent_layer_guard import (
    CODE_RG_ABSENT,
    DEFAULT_MANAGEMENT_RG,
    LAYER_TAG_KEY,
    LAYER_TAG_MANAGEMENT_VALUE,
    classify,
    decide,
    management_rg_set,
    normalize_rg,
)

# deadline=None: 1 例あたりの時間は測らない (純粋関数。CI の共有ランナーの
# ゆらぎで落ちる理由を作らない — 落ちるのは性質違反だけにする)。
PROPERTY = settings(max_examples=200, deadline=None)

# シェル / 環境変数の受け渡しで混ざりうる前後の空白。
SURROUNDING_SPACE = st.sampled_from(["", " ", "  ", "\t", "\n", " \t ", "\n  "])


@st.composite
def azure_equivalent(draw: st.DrawFn, name: str) -> str:
    """Azure が `name` と同一視する表記 (大小の入れ替え + 前後の空白)。"""
    cased = "".join(draw(st.sampled_from([c.lower(), c.upper()])) for c in name)
    return draw(SURROUNDING_SPACE) + cased + draw(SURROUNDING_SPACE)


# 「ふつうの」RG 名。既定の管理系 RG と衝突しない文字集合にしてある。
RG_NAME = st.builds(
    lambda head, tail: head + tail,
    st.sampled_from(list("rgptdv")),
    st.text(alphabet=st.sampled_from(list("rgptdv-0129")), min_size=1, max_size=12),
).filter(lambda n: normalize_rg(n) != normalize_rg(DEFAULT_MANAGEMENT_RG))

# 判定の材料。**管理系 RG の判定は他のどの材料よりも先に効く**はずなので、
# ここは「本来なら通るはずの材料」も「拒否になるはずの材料」も両方生成する。
RESOURCES = st.lists(
    st.sampled_from(
        [
            "Microsoft.Web/sites",
            "Microsoft.DocumentDB/databaseAccounts",
            "Microsoft.KeyVault/vaults",
            "Microsoft.Storage/storageAccounts",
        ]
    ),
    max_size=4,
)


@PROPERTY
@given(name=RG_NAME, data=st.data())
def test_normalize_rg_identifies_every_azure_equivalent_spelling(
    name: str, data: st.DataObject
) -> None:
    """[単体] Azure が同一視する表記は `normalize_rg` でも同じ値になる。

    無いと何が静かに通るか: 正規化が片方 (大小 or 空白) しか見ていなくても、
    上位の判定は例外を出さず「保護対象ではない」と読んで削除へ進む。
    """
    variant = data.draw(azure_equivalent(name))
    assert normalize_rg(variant) == normalize_rg(name)


@PROPERTY
@given(name=RG_NAME)
def test_normalize_rg_is_idempotent(name: str) -> None:
    """[単体] 正規化は冪等 — 正規化済みの名前を通しても変わらない。

    無いと何が静かに通るか: `management_rg_set` は**正規化済みの集合**を返す前提で
    比較されるので、ここが冪等でないと集合の中身と比較キーが食い違い、
    足したはずの `MGMT_RG` が保護されない。
    """
    once = normalize_rg(name)
    assert normalize_rg(once) == once


@PROPERTY
@given(
    extra=st.lists(st.one_of(RG_NAME, st.just(""), st.just("  ")), max_size=4),
    data=st.data(),
)
def test_management_rg_set_is_additive_only(
    extra: list[str], data: st.DataObject
) -> None:
    """[単体] `management_rg_set` は追加専用 — 既定名は何を渡しても必ず含まれ、正規化済み。

    無いと何が静かに通るか: `MGMT_RG` を別名に向けるだけで `rg-mgmt-mindbox` が
    保護から落ち、`ALLOW_PROTECTED_DELETE=true` と組み合わせると実際に
    `az group delete` まで通る (2026-08-14 の内部 judge が実測した経路)。
    """
    variants = [
        data.draw(azure_equivalent(name)) if name.strip() else name for name in extra
    ]
    result = management_rg_set(variants)

    assert normalize_rg(DEFAULT_MANAGEMENT_RG) in result
    # 既定だけの集合を必ず包含する (足すだけで、減らない)
    assert management_rg_set(None) <= result
    # 集合の中身は正規化済み (比較キーと同じ形でないと、足しても効かない)
    assert all(normalize_rg(name) == name for name in result)
    # 空白だけの指定は「名前を足した」ことにしない (`MGMT_RG=""` の既定)
    assert all(name for name in result)


@PROPERTY
@given(
    rg_exists=st.sampled_from([True, False, None]),
    resources=st.one_of(st.none(), RESOURCES),
    deleted=st.one_of(st.none(), RESOURCES),
    allow_protected=st.booleans(),
    previous_codes=st.lists(
        st.sampled_from([CODE_RG_ABSENT, "ok", "noop"]), max_size=3
    ),
    extra=st.lists(RG_NAME, max_size=3),
    data=st.data(),
)
def test_default_management_rg_is_never_deletable(
    rg_exists: bool | None,
    resources: list[Any] | None,
    deleted: list[Any] | None,
    allow_protected: bool,
    previous_codes: list[str],
    extra: list[str],
    data: st.DataObject,
) -> None:
    """[単体] 既定の管理系 RG は、**どんな表記・どんな他の材料でも**撤収できない。

    ここが性質にする価値のある形: 「逃げ道が無い」は他の全引数を渡り歩いて初めて
    言えることで、例をいくつ並べても「その組み合わせは試していない」が残る。
    override (`allow_protected=True`)・空の RG・取得失敗・過去の判定コードを
    すべて動かしても、判定は `target-is-management-rg` の一点であるべき。

    無いと何が静かに通るか: 管理系 RG が (表記ゆれや引数の組み合わせで) 1 通りでも
    `allowed=True` に落ちると、`cleanup-env.sh` はそのまま RG ごと削除する。
    バックアップ Storage・監査 LAW・E2E trace 復号鍵が同時に消えるので、
    気づいたときには戻す手段も一緒に無い。
    """
    target = data.draw(azure_equivalent(DEFAULT_MANAGEMENT_RG))
    decision = decide(
        target_rg=target,
        extra_management_rgs=extra,
        rg_exists=rg_exists,
        resources=resources,
        deleted_resources=deleted,
        allow_protected=allow_protected,
        previous_codes=previous_codes,
    )
    assert decision.allowed is False
    assert decision.code == "target-is-management-rg"


@PROPERTY
@given(name=RG_NAME, allow_protected=st.booleans(), data=st.data())
def test_extra_management_rg_protects_every_spelling(
    name: str, allow_protected: bool, data: st.DataObject
) -> None:
    """[単体] `MGMT_RG` で足した RG も、大小・前後空白のどの表記で撤収しようとしても拒否される。

    無いと何が静かに通るか: 足した保護が「渡した表記と 1 文字違わないときだけ」効く
    ザルになる。ワークフローの env と `az` の出力で大小が揃っている保証はない。
    """
    declared = data.draw(azure_equivalent(name))
    attempted = data.draw(azure_equivalent(name))
    decision = decide(
        target_rg=attempted,
        extra_management_rgs=[declared],
        rg_exists=True,
        resources=[],
        allow_protected=allow_protected,
    )
    assert decision.allowed is False
    assert decision.code == "target-is-management-rg"


# 層タグはどんな型に付いていても管理系 (README「型を問わず管理系」)。
# データ持ち (Cosmos) と曖昧型 (KV / Storage / LAW) とアプリ系を混ぜて生成する。
TAGGABLE_TYPES = st.sampled_from(
    [
        "Microsoft.KeyVault/vaults",
        "Microsoft.Storage/storageAccounts",
        "Microsoft.OperationalInsights/workspaces",
        "Microsoft.DocumentDB/databaseAccounts",
        "Microsoft.Web/sites",
        "Microsoft.Some/unknownType",
    ]
)
NOISE_TAGS = st.dictionaries(
    st.sampled_from(["env", "owner", "costCenter", "mindInboxRole"]),
    st.sampled_from(["dev", "po", "x"]),
    max_size=3,
)


@PROPERTY
@given(
    resource_type=TAGGABLE_TYPES,
    name=st.text(alphabet=st.sampled_from(list("abz019-")), min_size=1, max_size=10),
    noise=NOISE_TAGS,
    data=st.data(),
)
def test_layer_tag_is_recognized_in_any_spelling_and_on_any_type(
    resource_type: str, name: str, noise: dict[str, str], data: st.DataObject
) -> None:
    """[単体] 層タグ `mindInboxLayer=management` は、表記がどうであれ・型が何であれ管理系。

    Azure のタグはキーも値も大文字小文字を区別しない。`main-mgmt.bicep` が刻む
    キャメルケース (`mindInboxLayer`) と、ガードが持つ小文字定数の差でここを落とすと、
    **管理系として宣言したはずのリソースが「アプリ系」に落ちて黙って消える**。
    型を問わないことも同時に見る — 誤ってアプリ系 RG へ mgmt を流した場合も
    ここで捕まえるのが層タグの役目 (README「判定は 2 段」の 1 段目)。
    """
    key = data.draw(azure_equivalent(LAYER_TAG_KEY))
    value = data.draw(azure_equivalent(LAYER_TAG_MANAGEMENT_VALUE))
    tags = {**noise, key: value}

    management, data_findings, _notes = classify(
        [{"type": resource_type, "name": name, "tags": tags}]
    )

    assert len(management) == 1
    # 層タグが付いたものは「復元未実証データ」ではなく**管理系**に落ちる
    # (拒否コードが恒久 / 暫定のどちらになるかが変わる — README の 2 種類の区別)
    assert data_findings == []


@PROPERTY
@given(
    resource_type=TAGGABLE_TYPES,
    name=st.text(alphabet=st.sampled_from(list("abz019-")), min_size=1, max_size=10),
    data=st.data(),
)
def test_protected_name_matches_in_any_spelling(
    resource_type: str, name: str, data: st.DataObject
) -> None:
    """[単体] `PROTECTED_RESOURCE_NAMES` の名指しも、大小・前後空白のどの表記でも効く。

    無いと何が静かに通るか: 層タグを刻む前 (mgmt bicep 未適用) のリソースを守る
    唯一の逃げ道が、表記が 1 文字違うだけで無言で外れる。
    """
    declared = data.draw(azure_equivalent(name))
    actual = data.draw(azure_equivalent(name))

    management, _data, _notes = classify(
        [{"type": resource_type, "name": actual, "tags": {}}],
        protected_names=[declared],
    )
    assert len(management) == 1
