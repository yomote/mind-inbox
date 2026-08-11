"""[L1] 既存ロール割り当ての名前解決 (#262)。

無いと何が静かに通るか:
    名前解決が空振りしても az は 0 を返し、シェルは黙って
    「養子縁組しない」まま bicep を適用する。結果は
    **RoleAssignmentExists でデプロイ全体が落ち、dev が古いまま止まる**。
    2026-08-11 は実際にこれで半日赤だった (#262 / #287)。
    綴りの揺れ (scope の大小) と ARM のダッシュ無し GUID 表記という
    「見た目は些細だが結果は全損」の 2 点をここで固定する。
"""

import pytest
from role_assignment import name_from_error, normalize_guid, pick_existing_name

SCOPE = (
    "/subscriptions/52b97dd7-805f-4026-b8ec-4be344272c29/resourceGroups/"
    "rg-dev-mind-inbox/providers/Microsoft.CognitiveServices/accounts/oai-dev-mindbox"
)
PRINCIPAL = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
ROLE = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"


def _assignment(**overrides: str) -> dict:
    base = {
        "name": "314eb860-f90c-493b-aa0c-55e4226c2d34",
        "scope": SCOPE,
        "principalId": PRINCIPAL,
        "roleDefinitionId": (
            "/subscriptions/52b97dd7-805f-4026-b8ec-4be344272c29/providers/"
            f"Microsoft.Authorization/roleDefinitions/{ROLE}"
        ),
    }
    base.update(overrides)
    return base


def test_l1_一致する既存割り当ての名前を返す() -> None:
    assert (
        pick_existing_name(
            [_assignment()],
            scope=SCOPE,
            principal_id=PRINCIPAL,
            role_definition_guid=ROLE,
        )
        == "314eb860-f90c-493b-aa0c-55e4226c2d34"
    )


def test_l1_scope_の綴り違いでも見つける() -> None:
    """ARM が返す scope は `resourceGroups` の大小が揺れる。

    ここを素の == で比較していたのが PR #278 が翌回から効かなくなった原因。
    """
    wobbled = SCOPE.replace("/resourceGroups/", "/resourcegroups/").replace(
        "Microsoft.CognitiveServices", "microsoft.cognitiveservices"
    )
    assert (
        pick_existing_name(
            [_assignment(scope=wobbled)],
            scope=SCOPE,
            principal_id=PRINCIPAL,
            role_definition_guid=ROLE,
        )
        is not None
    )


def test_l1_別scope_別principal_別role_は拾わない() -> None:
    """--scope 付きの一覧でも継承分 (RG / サブスクリプション) が混ざる。

    ここを緩めると **無関係な割り当ての名前を bicep に渡してしまい**、
    ARM が別 scope に同名の割り当てを作りに行く。
    """
    other_scope = _assignment(scope=SCOPE.rsplit("/providers/", 1)[0])
    other_principal = _assignment(principalId="ffffffff-ffff-ffff-ffff-ffffffffffff")
    other_role = _assignment(
        roleDefinitionId=(
            "/subscriptions/52b97dd7-805f-4026-b8ec-4be344272c29/providers/"
            "Microsoft.Authorization/roleDefinitions/f2dc8367-1007-4938-bd23-fe263f013447"
        )
    )
    for noise in (other_scope, other_principal, other_role):
        assert (
            pick_existing_name(
                [noise], scope=SCOPE, principal_id=PRINCIPAL, role_definition_guid=ROLE
            )
            is None
        )


def test_l1_一覧が空でも落ちない() -> None:
    """初回デプロイ (割り当てがまだ無い) は None = guid() で新規作成に倒す。"""
    assert (
        pick_existing_name(
            [], scope=SCOPE, principal_id=PRINCIPAL, role_definition_guid=ROLE
        )
        is None
    )


def test_l1_ARMのエラー本文からダッシュ無しGUIDを復元する() -> None:
    """実際に 2026-08-11 の deploy が吐いた本文 (run 31502886155)。"""
    text = (
        'ERROR: {"status":"Failed","error":{"code":"DeploymentFailed","details":'
        '[{"code":"RoleAssignmentExists","message":"The role assignment already '
        "exists. The ID of the existing role assignment is "
        '314eb860f90c493baa0c55e4226c2d34."}]}}'
    )
    assert name_from_error(text) == "314eb860-f90c-493b-aa0c-55e4226c2d34"


def test_l1_ダッシュ付きで返ってきてもそのまま使える() -> None:
    text = (
        "The role assignment already exists. The ID of the existing role "
        "assignment is 314EB860-F90C-493B-AA0C-55E4226C2D34."
    )
    assert name_from_error(text) == "314eb860-f90c-493b-aa0c-55e4226c2d34"


def test_l1_別のエラーを養子縁組と誤認しない() -> None:
    """ここが誤爆すると、無関係な失敗に対して**同じデプロイを 2 回**流す。"""
    text = (
        'ERROR: {"code":"InvalidTemplateDeployment","message":"The template '
        'deployment failed: quota exceeded. id 314eb860f90c493baa0c55e4226c2d34"}'
    )
    assert name_from_error(text) is None
    assert name_from_error("") is None


def test_l1_GUIDとして読めない値は例外にする() -> None:
    with pytest.raises(ValueError):
        normalize_guid("not-a-guid")
