"""persistent_layer_guard の判定テスト。

各テストの「無いと何が静かに通るか」:
  - test_refuses_persistent_rg_itself       … 持続層 RG そのものが削除される
  - test_override_cannot_delete_persistent_rg … override を付けるだけで持続層 RG が消える
  - test_refuses_when_persistent_resources_present … Cosmos ごと環境層と一緒に消える
  - test_refuses_when_inventory_unavailable … 「調べられなかった」が「持続層なし」として通る
  - test_allows_app_layer_only              … 撤収そのものが常に拒否され、down が使えなくなる
  - test_allows_when_rg_absent              … 冪等な再実行 (RG 不在) が拒否で落ちる
  - test_override_allows_with_findings      … 明示許可の逃げ道が消え、移行前に down できない
  - test_case_insensitive_*                 … 大文字小文字違いで判定をすり抜ける
"""

from __future__ import annotations

import pytest

from persistent_layer_guard import DEFAULT_PERSISTENT_RG, decide, main

APP_LAYER_TYPES = [
    "Microsoft.Web/staticSites",
    "Microsoft.Web/sites",
    "Microsoft.Web/serverfarms",
    "Microsoft.Storage/storageAccounts",
    "Microsoft.App/managedEnvironments",
    "Microsoft.App/containerApps",
]


def test_refuses_persistent_rg_itself() -> None:
    d = decide(target_rg=DEFAULT_PERSISTENT_RG, resource_types=[])
    assert not d.allowed
    assert d.code == "target-is-persistent-rg"


def test_override_cannot_delete_persistent_rg() -> None:
    """持続層 RG だけは override でも通らない — ここが層分断の実体。"""
    d = decide(
        target_rg=DEFAULT_PERSISTENT_RG,
        resource_types=APP_LAYER_TYPES,
        allow_persistent=True,
    )
    assert not d.allowed
    assert d.code == "target-is-persistent-rg"


def test_case_insensitive_persistent_rg_match() -> None:
    d = decide(target_rg="RG-Shared-MindBox", resource_types=[])
    assert not d.allowed
    assert d.code == "target-is-persistent-rg"


def test_allows_app_layer_only() -> None:
    d = decide(target_rg="rg-dev-mind-inbox", resource_types=APP_LAYER_TYPES)
    assert d.allowed
    assert d.code == "ok"


def test_allows_when_rg_absent() -> None:
    d = decide(target_rg="rg-dev-mind-inbox", rg_exists=False, resource_types=None)
    assert d.allowed
    assert d.code == "rg-absent"


@pytest.mark.parametrize(
    ("resource_type", "expected_fragment"),
    [
        ("Microsoft.DocumentDB/databaseAccounts", "Cosmos DB"),
        ("Microsoft.CognitiveServices/accounts", "Cognitive Services"),
        ("Microsoft.KeyVault/vaults", "Key Vault"),
    ],
)
def test_refuses_when_persistent_resources_present(
    resource_type: str, expected_fragment: str
) -> None:
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resource_types=[*APP_LAYER_TYPES, resource_type],
    )
    assert not d.allowed
    assert d.code == "persistent-resources-present"
    assert any(expected_fragment in f for f in d.findings)


def test_case_insensitive_resource_type_match() -> None:
    """ARM の type は大小が揺れる。小文字で来ても捕まえる。"""
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resource_types=["microsoft.documentdb/databaseaccounts"],
    )
    assert not d.allowed
    assert d.code == "persistent-resources-present"


def test_refuses_when_inventory_unavailable() -> None:
    """取得失敗 (None) と「調べたら空だった」(空リスト) を混同しない。"""
    d = decide(target_rg="rg-dev-mind-inbox", resource_types=None)
    assert not d.allowed
    assert d.code == "inventory-unavailable"

    empty = decide(target_rg="rg-dev-mind-inbox", resource_types=[])
    assert empty.allowed
    assert empty.code == "ok"


def test_override_allows_with_findings() -> None:
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resource_types=["Microsoft.DocumentDB/databaseAccounts"],
        allow_persistent=True,
    )
    assert d.allowed
    assert d.code == "persistent-resources-present-overridden"
    # 何を消そうとしているかはログに出し続ける (許可 = 黙るではない)。
    assert d.findings


def test_override_allows_when_inventory_unavailable() -> None:
    d = decide(
        target_rg="rg-dev-mind-inbox", resource_types=None, allow_persistent=True
    )
    assert d.allowed
    assert d.code == "inventory-unavailable-overridden"


def test_findings_are_deduplicated() -> None:
    d = decide(
        target_rg="rg-dev-mind-inbox",
        resource_types=[
            "Microsoft.CognitiveServices/accounts",
            "Microsoft.CognitiveServices/accounts",
        ],
    )
    assert len(d.findings) == 1


# ---- CLI (終了コードで呼び出し側が分岐するので、そこを押さえる) ----


def test_cli_exit_code_refused() -> None:
    code = main(
        [
            "--target-rg",
            "rg-dev-mind-inbox",
            "--inventory",
            '["Microsoft.DocumentDB/databaseAccounts"]',
        ]
    )
    assert code == 3


def test_cli_exit_code_allowed() -> None:
    code = main(
        ["--target-rg", "rg-dev-mind-inbox", "--inventory", '["Microsoft.Web/sites"]']
    )
    assert code == 0


def test_cli_broken_inventory_is_refused_not_treated_as_empty() -> None:
    """壊れた JSON を「空 = 持続層なし」に読み替えない。"""
    code = main(["--target-rg", "rg-dev-mind-inbox", "--inventory", "not-json"])
    assert code == 3
