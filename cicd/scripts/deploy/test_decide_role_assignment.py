"""[単体] ai-agent の OpenAI ロール割り当てを bicep にどう宣言させるかの判定 (#262 / #277)。

無いと何が静かに通るか:
    「az の参照に失敗した」を「既存が無い」に丸めると、bicep は guid() 名で
    同じ principal+role+scope をもう一度宣言し、ARM が `RoleAssignmentExists` で
    **デプロイ全体を落とす**。しかも落ちるのは bicep の中なので、
    ログには「ロール割り当てが引けなかった」とは一言も出ない。
    2026-08-11 の dev 停止 (run 31500334037 / 31500900952) がまさにこれで、
    直前の run 31481914784 では同じコードが成功していた =
    **緑と赤が参照の運で決まる**状態だった。

    逆に「参照できたが既存は無い」で skip に倒すと、bicep が割り当てを
    宣言しなくなり ADR 0017 の「誰が宣言しているか」が崩れる。
    どちらへ倒れても run は静かに進むので、ここは表で固定する。
"""

import pytest

from decide_role_assignment import ADOPT, CREATE_NEW, SKIP, decide

EXISTING = "314eb860-f90c-493b-aa0c-55e4226c2d34"


def test_単体_既存割り当てがあれば養子縁組する() -> None:
    action, detail = decide("ok", "ok", "ok", EXISTING)
    assert (action, detail) == (ADOPT, EXISTING)


def test_単体_参照できたが既存が無ければ新規作成に倒す() -> None:
    assert decide("ok", "ok", "ok", "")[0] == CREATE_NEW
    # 一覧が空 (absent) も「既存なし」であって「分からない」ではない
    assert decide("ok", "ok", "absent", "")[0] == CREATE_NEW


def test_単体_対象リソースが未作成なら新規作成に倒す() -> None:
    """初回デプロイ: Container App も OpenAI アカウントも同じ deployment で作られる。

    principal がまだ存在しない以上、同じ三つ組の既存割り当てはありえない。
    """
    assert decide("absent", "ok", "absent", "")[0] == CREATE_NEW
    assert decide("ok", "absent", "absent", "")[0] == CREATE_NEW


@pytest.mark.parametrize(
    "ca,oai,role",
    [
        ("error", "ok", "absent"),
        ("ok", "error", "absent"),
        ("ok", "ok", "error"),
    ],
)
def test_単体_参照に失敗したら推測せず宣言を見送る(ca: str, oai: str, role: str) -> None:
    """**これが #278 に無かった分岐**。

    ここを create-new に倒すと RoleAssignmentExists で dev が止まる。
    adopt に倒すと存在しない名前を宣言してしまう。どちらも取らない。
    """
    action, _ = decide(ca, oai, role, EXISTING)
    assert action == SKIP


def test_単体_未知の状態を正常系に丸めない() -> None:
    assert decide("weird", "ok", "ok", EXISTING)[0] == SKIP
    assert decide("ok", "weird", "ok", EXISTING)[0] == SKIP
    assert decide("ok", "ok", "weird", EXISTING)[0] == SKIP


@pytest.mark.parametrize(
    "name",
    [
        "ERROR: (AuthorizationFailed) does not have authorization",
        "not-a-guid",
        "314eb860f90c493baa0c55e4226c2d34",  # ARM のエラー文が返すダッシュ無し表記
    ],
)
def test_単体_GUID_でない名前は_resource_名に使わない(name: str) -> None:
    """az の出力が想定外だったとき、それを bicep の resource 名へ流し込まない。

    流し込むと bicep のテンプレート検証で落ちるか、最悪**別の割り当てを上書き**する。
    """
    assert decide("ok", "ok", "ok", name)[0] == SKIP


def test_単体_大文字や空白が混ざっても判定できる() -> None:
    assert decide(" OK ", "Ok", "ok", f"  {EXISTING.upper()}  ") == (ADOPT, EXISTING.upper())
