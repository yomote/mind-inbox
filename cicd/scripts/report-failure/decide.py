#!/usr/bin/env python3
"""落ちた workflow が Issue をどう扱うかを決める (ADR 0035 D2)。

なぜ純粋関数に切り出すか:
    この判定は「Issue を立てる / 追記する / 閉じる」という副作用の入口で、
    間違えると **障害を隠す** (success を復旧と誤認して閉じる) か
    **ノイズで埋める** (正常な自己中断で毎回 Issue を立てる)。
    どちらも「動いているように見えて壊れている」типの失敗なので、
    シェルの中に埋めずにテストできる形にする
    (CLAUDE.md「状態・副作用を持つ新モジュールはテストファーストで切る」)。

使い方:
    python3 decide.py <job_status> <work_ran> <cancelled_is_failure> <has_open_issue>
      → open / append / close / noop を stdout に 1 行

判断の背景 (2026-08-10 の Codex + レビューの指摘):
    - success は復旧を意味しない。guard で skip された run / `down` 実行でも success
    - cancelled はタイムアウト (= 障害) のこともあれば、concurrency による
      自己中断 (= 正常) のこともある。**呼び出し元しか区別できない**
"""

from __future__ import annotations

import sys

OPEN, APPEND, CLOSE, NOOP = "open", "append", "close", "noop"


def decide(
    job_status: str,
    work_ran: bool,
    cancelled_is_failure: bool,
    has_open_issue: bool,
) -> str:
    """Issue に対して何をするかを返す。

    job_status: success / failure / cancelled (呼び出し元の job.status)
    work_ran:   監視対象の処理が実際に走ったか
    cancelled_is_failure:
        cancelled を障害として扱うか。**呼び出し元ごとに違う** —
        deploy / golden-path はタイムアウトが障害なので true、
        status-page は cancel-in-progress による自己中断が正常なので false
    has_open_issue: 同じ workflow の open な ci-failure Issue があるか
    """
    status = job_status.strip().lower()

    if status == "cancelled":
        if not cancelled_is_failure:
            return NOOP
        status = "failure"

    if status == "failure":
        return APPEND if has_open_issue else OPEN

    if status == "success":
        # 監視対象が走っていないなら「直った」とは言えない。開きも閉じもしない。
        if not work_ran:
            return NOOP
        return CLOSE if has_open_issue else NOOP

    # 未知の状態を「異常なし」に丸めない。報告側に寄せる。
    return APPEND if has_open_issue else OPEN


def _flag(v: str) -> bool:
    return v.strip().lower() == "true"


def main() -> int:
    if len(sys.argv) != 5:
        print(NOOP)
        return 2
    print(
        decide(
            job_status=sys.argv[1],
            work_ran=_flag(sys.argv[2]),
            cancelled_is_failure=_flag(sys.argv[3]),
            has_open_issue=_flag(sys.argv[4]),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
