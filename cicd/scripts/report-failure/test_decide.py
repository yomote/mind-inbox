"""[L1] 落ちた workflow の Issue 判定 (ADR 0035 D2)。

無いと何が静かに通るか:
    この判定の誤りは 2 方向に効く。**障害を隠す** (走っていない success で
    Issue を閉じる) と **ノイズで埋める** (正常な自己中断で Issue を立てる)。
    どちらも run は緑のままなので、テストが無いと誰も気づけない。
    実際 2026-08-10 のレビューで、この 2 つを両方踏んでいた。
"""

from decide import APPEND, CLOSE, NOOP, OPEN, decide


def test_l1_失敗したら開く_既にあれば追記する() -> None:
    assert decide("failure", True, True, has_open_issue=False) == OPEN
    assert decide("failure", True, True, has_open_issue=True) == APPEND


def test_l1_走っていない成功では閉じない() -> None:
    """guard で skip / `down` 実行 — job は success でも監視対象は動いていない。

    ここを閉じると、**落ちたままの環境が「復旧しました」と記録される**。
    """
    assert decide("success", work_ran=False, cancelled_is_failure=True,
                  has_open_issue=True) == NOOP
    # 開いてもいけない (何も分かっていないので黙る)
    assert decide("success", work_ran=False, cancelled_is_failure=True,
                  has_open_issue=False) == NOOP


def test_l1_実際に走った成功なら閉じる() -> None:
    assert decide("success", work_ran=True, cancelled_is_failure=True,
                  has_open_issue=True) == CLOSE
    # 開いている Issue が無ければ何もしない (毎回 close を叩かない)
    assert decide("success", work_ran=True, cancelled_is_failure=True,
                  has_open_issue=False) == NOOP


def test_l1_タイムアウトの中断は障害として扱う() -> None:
    """deploy は timeout-minutes: 90、golden-path は 45。ハングは cancelled で現れる。"""
    assert decide("cancelled", True, cancelled_is_failure=True,
                  has_open_issue=False) == OPEN


def test_l1_concurrency_による自己中断は無視する() -> None:
    """status-page は cancel-in-progress: true で 4 系統のトリガーを持つ。

    自己中断は正常運用なので、ここで Issue を立てると
    「Issue 一覧が現在の状態を表す」という本来の目的を自分で壊す。
    """
    assert decide("cancelled", True, cancelled_is_failure=False,
                  has_open_issue=False) == NOOP
    assert decide("cancelled", True, cancelled_is_failure=False,
                  has_open_issue=True) == NOOP


def test_l1_未知の状態を異常なしに丸めない() -> None:
    assert decide("weird-new-status", True, True, has_open_issue=False) == OPEN


def test_l1_大文字や空白が混ざっても判定できる() -> None:
    assert decide(" SUCCESS ", work_ran=True, cancelled_is_failure=True,
                  has_open_issue=True) == CLOSE
