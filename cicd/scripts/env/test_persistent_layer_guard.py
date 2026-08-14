"""[単体] persistent_layer_guard の判定テスト。

各テストの「無いと何が静かに通るか」:
  - test_refuses_persistent_rg_itself       … 持続層 RG そのものが削除される
  - test_override_cannot_delete_persistent_rg … override を付けるだけで持続層 RG が消える
  - test_refuses_when_persistent_resources_present … Cosmos ごと環境層と一緒に消える
  - test_refuses_when_inventory_unavailable … 「調べられなかった」が「持続層なし」として通る
  - test_refuses_when_rg_existence_unknown  … 存在確認の失敗が「RG 不在」として通り、
                                              中身を一度も見ないまま削除へ進む
  - test_allows_app_layer_only              … 撤収そのものが常に拒否され、down が使えなくなる
  - test_allows_when_rg_absent              … 冪等な再実行 (RG 不在) が拒否で落ちる
  - test_override_allows_with_findings      … 明示許可の逃げ道が消え、移行前に down できない
  - test_case_insensitive_*                 … 大文字小文字違いで判定をすり抜ける
  - test_layer_tag_*                        … 持続層の Storage / LAW / KV が型で拾えず黙って消える
  - test_env_layer_*                        … 環境層の KV / Storage / LAW で撤収が常に拒否される
                                              (= override 常用でガードが死ぬ)
  - test_persistent_name_*                  … 層タグの無い移行前リソースを守る手段が消える
  - test_rg_reappeared_*                    … 「不在」で通した RG が再出現しても、中身を
                                              一度も検証しないまま削除へ進む
"""

from __future__ import annotations

import pytest

from persistent_layer_guard import (
    CODE_RG_ABSENT,
    CODE_RG_REAPPEARED,
    DEFAULT_PERSISTENT_RG,
    decide,
    main,
)

APP_LAYER_TYPES = [
    "Microsoft.Web/staticSites",
    "Microsoft.Web/sites",
    "Microsoft.Web/serverfarms",
    "Microsoft.App/managedEnvironments",
    "Microsoft.App/containerApps",
]

# 環境層に**必ず**居る両層型 (bootstrap-core.bicep)。
# Function App の実行 storage と ops workspace は環境を作り直すたびに作られる。
ENV_LAYER_AMBIGUOUS = [
    {"type": "Microsoft.Storage/storageAccounts", "name": "stdevmindbox", "tags": {}},
    {
        "type": "Microsoft.OperationalInsights/workspaces",
        "name": "law-dev-mindbox-ops",
        "tags": {},
    },
]

PERSISTENT_TAGS = {"mindInboxLayer": "persistent", "mindInboxEnvironment": "dev"}


def test_refuses_persistent_rg_itself() -> None:
    d = decide(target_rg=DEFAULT_PERSISTENT_RG, resources=[])
    assert not d.allowed
    assert d.code == "target-is-persistent-rg"


def test_override_cannot_delete_persistent_rg() -> None:
    """持続層 RG だけは override でも通らない — ここが層分断の実体。"""
    d = decide(
        target_rg=DEFAULT_PERSISTENT_RG,
        resources=APP_LAYER_TYPES,
        allow_persistent=True,
    )
    assert not d.allowed
    assert d.code == "target-is-persistent-rg"


def test_case_insensitive_persistent_rg_match() -> None:
    d = decide(target_rg="RG-Shared-MindBox", resources=[])
    assert not d.allowed
    assert d.code == "target-is-persistent-rg"


def test_allows_app_layer_only() -> None:
    d = decide(target_rg="rg-dev-mind-inbox", resources=APP_LAYER_TYPES)
    assert d.allowed
    assert d.code == "ok"


def test_allows_when_rg_absent() -> None:
    d = decide(target_rg="rg-dev-mind-inbox", rg_exists=False, resources=None)
    assert d.allowed
    assert d.code == "rg-absent"


# ---- RG の存在確認 (不在 / 確認失敗 を混同しない) ----


def test_refuses_when_rg_existence_unknown() -> None:
    """存在を確かめられなかった (None) を「不在」(False) に潰さない。

    潰すと inventory を一度も見ないままガードを通過し、後段の再確認が成功した
    ときに `az group delete` まで進む。
    """
    d = decide(target_rg="rg-dev-mind-inbox", rg_exists=None, resources=None)
    assert not d.allowed
    assert d.code == "rg-existence-unknown"


def test_rg_existence_unknown_beats_inventory_that_looks_clean() -> None:
    """中身が「きれい」に見えても、存在確認が失敗していたら通さない。"""
    d = decide(target_rg="rg-dev-mind-inbox", rg_exists=None, resources=APP_LAYER_TYPES)
    assert not d.allowed
    assert d.code == "rg-existence-unknown"


def test_rg_existence_unknown_is_overridable() -> None:
    d = decide(target_rg="rg-dev-mind-inbox", rg_exists=None, allow_persistent=True)
    assert d.allowed
    assert d.code == "rg-existence-unknown-overridden"


def test_persistent_rg_wins_over_existence_unknown() -> None:
    """持続層 RG は「存在すら確かめられない」状況でも override で通らない。"""
    d = decide(target_rg=DEFAULT_PERSISTENT_RG, rg_exists=None, allow_persistent=True)
    assert not d.allowed
    assert d.code == "target-is-persistent-rg"


# ---- 状態遷移 (不在で通したあとに RG が現れた) ----
#
# 撤収は破壊系の手前で何度も判定する。**1 回分の材料では判定できない**ので、
# 過去の判定コードを渡して decide 側で遷移を見る。


def test_rg_reappeared_after_absent_is_refused() -> None:
    """「不在」で通したあとに RG が現れたら消さない。

    無いと: 不在判定では中身 (inventory) を一度も見ていないのに、その後 provision が
    作り直した RG を「空に見えるから」という理由だけで削除できてしまう。
    """
    d = decide(
        target_rg="rg-dev-mind-inbox",
        rg_exists=True,
        resources=APP_LAYER_TYPES,
        previous_codes=[CODE_RG_ABSENT],
    )
    assert not d.allowed
    assert d.code == CODE_RG_REAPPEARED
    # 今の材料だけなら通っていたことを黙らない (何を退けたのかが追えなくなる)。
    assert any("ok" in n for n in d.notes)


def test_rg_reappeared_after_absent_is_not_overridable() -> None:
    """再出現だけは ALLOW_PERSISTENT_DELETE でも通さない。

    無いと: override は「持続層を承知で捨てる」ためのものなのに、**他人の RG を
    無検証で消す**ためにも効いてしまう。
    """
    d = decide(
        target_rg="rg-dev-mind-inbox",
        rg_exists=True,
        resources=APP_LAYER_TYPES,
        previous_codes=[CODE_RG_ABSENT],
        allow_persistent=True,
    )
    assert not d.allowed
    assert d.code == CODE_RG_REAPPEARED


def test_rg_reappeared_refuses_when_existence_becomes_unknown() -> None:
    """不在で通したあと存在を確かめられなくなったら、「まだ不在のはず」に読み替えない。

    無いと: override 付きの実行で `az group exists` が落ちた瞬間だけ、再出現の判定が
    素通りする窓が空く。
    """
    d = decide(
        target_rg="rg-dev-mind-inbox",
        rg_exists=None,
        previous_codes=[CODE_RG_ABSENT],
        allow_persistent=True,
    )
    assert not d.allowed
    assert d.code == CODE_RG_REAPPEARED


def test_still_absent_after_absent_stays_allowed() -> None:
    """不在のままなら遷移していないので通す (冪等な再実行を壊さない)。"""
    d = decide(
        target_rg="rg-dev-mind-inbox",
        rg_exists=False,
        resources=None,
        previous_codes=[CODE_RG_ABSENT],
    )
    assert d.allowed
    assert d.code == CODE_RG_ABSENT


def test_absent_after_delete_is_not_treated_as_reappearance() -> None:
    """自分で消したあとの判定 (存在 → 不在) を再出現と誤認しない。

    無いと: RG 削除の後に走る purge の直前判定が常に拒否になり、
    名前衝突の手当て (PURGE_DELETED_*) が一切使えなくなる。
    """
    d = decide(
        target_rg="rg-dev-mind-inbox",
        rg_exists=False,
        resources=None,
        deleted_resources=[DELETED_ENV_KV],
        previous_codes=["ok"],
    )
    assert d.allowed
    assert d.code == CODE_RG_ABSENT


def test_persistent_rg_refusal_wins_over_reappearance() -> None:
    """持続層 RG は遷移に関係なく持続層 RG として拒否する (理由を上書きしない)。"""
    d = decide(
        target_rg=DEFAULT_PERSISTENT_RG,
        rg_exists=True,
        resources=[],
        previous_codes=[CODE_RG_ABSENT],
    )
    assert not d.allowed
    assert d.code == "target-is-persistent-rg"


def test_persistent_resources_refusal_wins_over_reappearance() -> None:
    """今の材料で既に拒否なら、そちらの理由を残す (findings が消えると追えない)。"""
    d = decide(
        target_rg="rg-dev-mind-inbox",
        rg_exists=True,
        resources=["Microsoft.DocumentDB/databaseAccounts"],
        previous_codes=[CODE_RG_ABSENT],
    )
    assert not d.allowed
    assert d.code == "persistent-resources-present"
    assert d.findings


def test_previous_codes_without_absent_do_not_block() -> None:
    """不在を経ていない実行は普通に通す (遷移が無いのに止めない)。"""
    d = decide(
        target_rg="rg-dev-mind-inbox",
        rg_exists=True,
        resources=APP_LAYER_TYPES,
        previous_codes=["ok", "ok"],
    )
    assert d.allowed
    assert d.code == "ok"


# ---- 型だけで確定するもの (環境層に使い捨ての同型が無い) ----


@pytest.mark.parametrize(
    ("resource_type", "expected_fragment"),
    [
        ("Microsoft.DocumentDB/databaseAccounts", "Cosmos DB"),
        ("Microsoft.CognitiveServices/accounts", "Cognitive Services"),
    ],
)
def test_refuses_when_persistent_resources_present(
    resource_type: str, expected_fragment: str
) -> None:
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resources=[*APP_LAYER_TYPES, resource_type],
    )
    assert not d.allowed
    assert d.code == "persistent-resources-present"
    assert any(expected_fragment in f for f in d.findings)


def test_case_insensitive_resource_type_match() -> None:
    """ARM の type は大小が揺れる。小文字で来ても捕まえる。"""
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resources=["microsoft.documentdb/databaseaccounts"],
    )
    assert not d.allowed
    assert d.code == "persistent-resources-present"


# ---- 両層に出る型は層タグ / 名指しでのみ持続層 ----


@pytest.mark.parametrize(
    ("resource_type", "name"),
    [
        ("Microsoft.KeyVault/vaults", "kv-dev-mindbox"),
        ("Microsoft.Storage/storageAccounts", "stdevmindboxbak"),
        ("Microsoft.OperationalInsights/workspaces", "law-dev-mindbox-ops"),
    ],
)
def test_layer_tag_makes_ambiguous_types_persistent(
    resource_type: str, name: str
) -> None:
    """層タグの付いた Storage / LAW / KV は持続層として拒否する。

    無いと: 誤った RG へ shared を流した後の撤収で、バックアップ Storage と
    監査履歴が型に載っていないという理由だけで黙って消える。
    """
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resources=[
            *APP_LAYER_TYPES,
            {"type": resource_type, "name": name, "tags": PERSISTENT_TAGS},
        ],
    )
    assert not d.allowed
    assert d.code == "persistent-resources-present"
    assert any(name in f and "層タグ" in f for f in d.findings)


def test_layer_tag_key_is_case_insensitive() -> None:
    """Azure のタグキーは大小を区別しない。az の返し方が変わっても拾う。"""
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resources=[
            {
                "type": "Microsoft.Storage/storageAccounts",
                "name": "stdevmindboxbak",
                "tags": {"MINDINBOXLAYER": "Persistent"},
            }
        ],
    )
    assert not d.allowed
    assert d.code == "persistent-resources-present"


def test_layer_tag_wins_regardless_of_type() -> None:
    """層タグは型より強い — 型表に無いものでも持続層として拒否する。"""
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resources=[
            {
                "type": "Microsoft.SomethingNew/things",
                "name": "future-resource",
                "tags": PERSISTENT_TAGS,
            }
        ],
    )
    assert not d.allowed
    assert d.code == "persistent-resources-present"


def test_env_layer_key_vault_does_not_block_teardown() -> None:
    """環境層の SQL 管理者用 Key Vault で撤収を拒否しない。

    無いと: `enableSql=true` の環境で正当な撤収が常に拒否され、実行するには
    他の持続層まで一括で許可する ALLOW_PERSISTENT_DELETE=true が要る
    (= 逃げ道が常用になり、ガードが意味を失う)。
    """
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resources=[
            *APP_LAYER_TYPES,
            {
                "type": "Microsoft.KeyVault/vaults",
                "name": "kv-dev-mindbox-sql2",
                "tags": {},
            },
        ],
    )
    assert d.allowed
    assert d.code == "ok"


def test_env_layer_ambiguous_types_are_reported_not_silent() -> None:
    """環境層として通すときも、何をそう見なしたかは黙らない。"""
    d = decide(target_rg="rg-dev-mind-inbox", resources=ENV_LAYER_AMBIGUOUS)
    assert d.allowed
    assert d.code == "ok"
    assert len(d.notes) == 2
    assert any("stdevmindbox" in n for n in d.notes)
    assert any("law-dev-mindbox-ops" in n for n in d.notes)


def test_type_only_inventory_does_not_make_ambiguous_types_persistent() -> None:
    """name/tags の無い素朴な `[].type` 出力でも、両層型は型だけで拒否しない。"""
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resources=["Microsoft.KeyVault/vaults", "Microsoft.Storage/storageAccounts"],
    )
    assert d.allowed
    assert d.notes


# ---- 名指し (層タグの無い移行前リソース) ----


def test_persistent_name_blocks_teardown() -> None:
    """タグの無いバックアップ Storage を名指しで守れる。

    無いと: shared をまだ流していない (= タグの無い) 持続層リソースを守る手段が
    層タグしかなく、移行途中に守れない窓が空く。
    """
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resources=ENV_LAYER_AMBIGUOUS,
        persistent_names=["law-dev-mindbox-ops"],
    )
    assert not d.allowed
    assert d.code == "persistent-resources-present"
    assert any("law-dev-mindbox-ops" in f and "名指し" in f for f in d.findings)
    # 名指ししていない storage は環境層のまま (notes に残る)。
    assert any("stdevmindbox" in n for n in d.notes)


def test_persistent_name_is_case_insensitive() -> None:
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resources=ENV_LAYER_AMBIGUOUS,
        persistent_names=["LAW-DEV-MINDBOX-OPS"],
    )
    assert not d.allowed
    assert d.code == "persistent-resources-present"


# ---- soft-delete 済み (purge = 唯一の復旧手段を恒久的に消す) ----

DELETED_PERSISTENT_KV = {
    "type": "Microsoft.KeyVault/vaults",
    "name": "kv-dev-mindbox",
    "tags": PERSISTENT_TAGS,
}
DELETED_ENV_KV = {
    "type": "Microsoft.KeyVault/vaults",
    "name": "kv-dev-mindbox-sql2",
    "tags": {},
}
DELETED_OPENAI = {
    "type": "Microsoft.CognitiveServices/accounts",
    "name": "oai-dev-mindbox",
    "tags": {},
}


def test_refuses_persistent_soft_deleted_even_when_live_inventory_is_clean() -> None:
    """soft-delete 済みの持続層を purge しようとしたら止める。

    無いと: `az resource list` は live しか返さないので判定は ok になり、
    PURGE_DELETED_* を立てた実行が復旧手段を恒久的に消す。
    """
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resources=APP_LAYER_TYPES,
        deleted_resources=[DELETED_OPENAI],
    )
    assert not d.allowed
    assert d.code == "persistent-soft-deleted-present"
    assert any("oai-dev-mindbox" in f for f in d.findings)


def test_soft_deleted_check_survives_rg_absent() -> None:
    """RG が消えたあとでも soft-delete の判定は効く。

    無いと: purge は RG 削除の**後**に走るので、rg-absent で許可されて素通りする
    (この順序が今回の穴そのもの)。
    """
    d = decide(
        target_rg="rg-dev-mind-inbox",
        rg_exists=False,
        resources=None,
        deleted_resources=[DELETED_PERSISTENT_KV],
    )
    assert not d.allowed
    assert d.code == "persistent-soft-deleted-present"


def test_env_layer_soft_deleted_does_not_block_purge() -> None:
    """環境層の soft-delete (SQL 管理者用 vault) は purge を止めない。

    無いと: 名前衝突の手当てとしての purge が常に拒否され、逃げ道が常用になる。
    """
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resources=APP_LAYER_TYPES,
        deleted_resources=[DELETED_ENV_KV],
    )
    assert d.allowed
    assert d.code == "ok"


def test_soft_deleted_persistent_is_overridable() -> None:
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resources=APP_LAYER_TYPES,
        deleted_resources=[DELETED_OPENAI],
        allow_persistent=True,
    )
    assert d.allowed
    assert d.code == "persistent-soft-deleted-overridden"
    assert d.findings


def test_persistent_rg_wins_over_soft_deleted() -> None:
    d = decide(
        target_rg=DEFAULT_PERSISTENT_RG,
        resources=[],
        deleted_resources=[DELETED_OPENAI],
        allow_persistent=True,
    )
    assert not d.allowed
    assert d.code == "target-is-persistent-rg"


def test_no_deleted_inventory_means_purge_is_off() -> None:
    """purge を有効にしていなければ soft-delete は見ない (触らないので)。"""
    d = decide(target_rg="rg-dev-mind-inbox", resources=APP_LAYER_TYPES)
    assert d.allowed
    assert d.code == "ok"


def test_cli_deleted_inventory_refuses_persistent() -> None:
    inventory = '["Microsoft.Web/sites"]'
    env_only = (
        '[{"type": "Microsoft.KeyVault/vaults", '
        '"name": "kv-dev-mindbox-sql2", "tags": null}]'
    )
    persistent = (
        '[{"type": "Microsoft.CognitiveServices/accounts", '
        '"name": "oai-dev-mindbox", "tags": null}]'
    )
    base = ["--target-rg", "rg-dev-mind-inbox", "--inventory", inventory]
    assert main([*base, "--deleted-inventory", env_only]) == 0
    assert main([*base, "--deleted-inventory", persistent]) == 3


def test_cli_deleted_inventory_unavailable_is_refused() -> None:
    """soft-delete 一覧を取れなかったのを「持続層は無い」に読み替えない。"""
    code = main(
        [
            "--target-rg",
            "rg-dev-mind-inbox",
            "--inventory",
            '["Microsoft.Web/sites"]',
            "--deleted-inventory-unavailable",
        ]
    )
    assert code == 3


def test_cli_broken_deleted_inventory_is_refused() -> None:
    code = main(
        [
            "--target-rg",
            "rg-dev-mind-inbox",
            "--inventory",
            '["Microsoft.Web/sites"]',
            "--deleted-inventory",
            "not-json",
        ]
    )
    assert code == 3


# ---- 取得失敗 / override ----


def test_refuses_when_inventory_unavailable() -> None:
    """取得失敗 (None) と「調べたら空だった」(空リスト) を混同しない。"""
    d = decide(target_rg="rg-dev-mind-inbox", resources=None)
    assert not d.allowed
    assert d.code == "inventory-unavailable"

    empty = decide(target_rg="rg-dev-mind-inbox", resources=[])
    assert empty.allowed
    assert empty.code == "ok"


def test_override_allows_with_findings() -> None:
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resources=["Microsoft.DocumentDB/databaseAccounts"],
        allow_persistent=True,
    )
    assert d.allowed
    assert d.code == "persistent-resources-present-overridden"
    # 何を消そうとしているかはログに出し続ける (許可 = 黙るではない)。
    assert d.findings


def test_override_allows_when_inventory_unavailable() -> None:
    d = decide(target_rg="rg-dev-mind-inbox", resources=None, allow_persistent=True)
    assert d.allowed
    assert d.code == "inventory-unavailable-overridden"


def test_findings_are_deduplicated() -> None:
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resources=[
            "Microsoft.CognitiveServices/accounts",
            "Microsoft.CognitiveServices/accounts",
        ],
    )
    assert len(d.findings) == 1


def test_render_shows_findings_and_notes() -> None:
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resources=[
            "Microsoft.DocumentDB/databaseAccounts",
            *ENV_LAYER_AMBIGUOUS,
        ],
    )
    rendered = d.render()
    assert "REFUSED" in rendered
    assert "Cosmos DB" in rendered
    assert "law-dev-mindbox-ops" in rendered


# ---- CLI (終了コードで呼び出し側が分岐するので、そこを押さえる) ----


def test_cli_exit_code_refused() -> None:
    code = main(
        [
            "--target-rg",
            "rg-dev-mind-inbox",
            "--inventory",
            '[{"type": "Microsoft.DocumentDB/databaseAccounts", "name": "cosmos-dev-mindbox"}]',
        ]
    )
    assert code == 3


def test_cli_exit_code_allowed() -> None:
    code = main(
        ["--target-rg", "rg-dev-mind-inbox", "--inventory", '["Microsoft.Web/sites"]']
    )
    assert code == 0


def test_cli_rg_unknown_is_refused() -> None:
    """`--rg-unknown` は `--rg-missing` と違って拒否側。"""
    assert main(["--target-rg", "rg-dev-mind-inbox", "--rg-unknown"]) == 3
    assert main(["--target-rg", "rg-dev-mind-inbox", "--rg-missing"]) == 0


def test_cli_rejects_missing_and_unknown_together() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--target-rg", "rg-dev-mind-inbox", "--rg-missing", "--rg-unknown"])
    assert exc.value.code == 2


def test_cli_persistent_name_flag() -> None:
    inventory = (
        '[{"type": "Microsoft.Storage/storageAccounts", '
        '"name": "stdevmindboxbak", "tags": null}]'
    )
    assert main(["--target-rg", "rg-dev-mind-inbox", "--inventory", inventory]) == 0
    assert (
        main(
            [
                "--target-rg",
                "rg-dev-mind-inbox",
                "--inventory",
                inventory,
                "--persistent-name",
                "stdevmindboxbak",
            ]
        )
        == 3
    )


def test_cli_layer_tag_from_az_output() -> None:
    """`az resource list --query "[].{type:type,name:name,tags:tags}"` の実形。"""
    inventory = (
        '[{"type": "Microsoft.Storage/storageAccounts", "name": "stdevmindboxbak", '
        '"tags": {"mindInboxLayer": "persistent", "mindInboxEnvironment": "dev"}}]'
    )
    assert main(["--target-rg", "rg-dev-mind-inbox", "--inventory", inventory]) == 3


def test_cli_prints_decision_code_on_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """判定コードは stdout に出す。

    無いと: 呼び出し側 (cleanup-env.sh) が許可/拒否の 2 値しか受け取れず、
    「不在だから通した」と「中身を見て通した」を区別できない。区別できないと、
    不在判定のあとに再作成された RG を無検証で消す経路が塞げない。
    """
    assert main(["--target-rg", "rg-dev-mind-inbox", "--rg-missing"]) == 0
    assert capsys.readouterr().out.strip() == "rg-absent"

    assert (
        main(
            [
                "--target-rg",
                "rg-dev-mind-inbox",
                "--inventory",
                '["Microsoft.Web/sites"]',
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.strip() == "ok"

    assert main(["--target-rg", "rg-shared-mindbox", "--rg-missing"]) == 3
    assert capsys.readouterr().out.strip() == "target-is-persistent-rg"


def test_cli_previous_code_blocks_reappeared_rg(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--previous-code` で渡した過去の判定が CLI 越しにも効く。

    無いと: 遷移の判定が呼び出し側 (cleanup-env.sh) の `if` に戻り、
    そこを壊してもテストが 1 つも落ちない。
    """
    inventory = '["Microsoft.Web/sites"]'
    base = ["--target-rg", "rg-dev-mind-inbox", "--inventory", inventory]

    assert main(base) == 0
    assert capsys.readouterr().out.strip() == "ok"

    assert main([*base, "--previous-code", "ok"]) == 0
    assert capsys.readouterr().out.strip() == "ok"

    assert main([*base, "--previous-code", CODE_RG_ABSENT]) == 3
    assert capsys.readouterr().out.strip() == CODE_RG_REAPPEARED

    # override でも通らない。
    assert main([*base, "--previous-code", CODE_RG_ABSENT, "--allow-persistent"]) == 3


def test_cli_previous_absent_still_absent_is_allowed() -> None:
    """不在のまま (RG 削除後の purge 直前など) は通す。"""
    assert (
        main(
            [
                "--target-rg",
                "rg-dev-mind-inbox",
                "--rg-missing",
                "--previous-code",
                CODE_RG_ABSENT,
            ]
        )
        == 0
    )


def test_cli_broken_inventory_is_refused_not_treated_as_empty() -> None:
    """壊れた JSON を「空 = 持続層なし」に読み替えない。"""
    code = main(["--target-rg", "rg-dev-mind-inbox", "--inventory", "not-json"])
    assert code == 3


def test_cli_unexpected_inventory_shape_is_refused() -> None:
    """要素が文字列でも object でもない = 材料の形が想定外。空扱いにしない。"""
    code = main(["--target-rg", "rg-dev-mind-inbox", "--inventory", "[1, 2, 3]"])
    assert code == 3
