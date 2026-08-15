"""[L1] 週次 AI 負債審査の依頼組み立て — 「依頼」を「審査の心拍」に化けさせない。

無いと何が静かに通るか:
    依頼コメントは結果マーカーを本文中で引用する。マーカーの判定・引用のしかたが
    ずれると、**毎週の自動依頼そのものが審査の痕跡として数えられ**、審査が一度も
    走っていなくても状況ページが 🟢 のままになる — #410 が潰そうとしている
    「呼ばれなかった週と 0 件の週が外形上同じ」がそのまま残る。
"""

import json
import pathlib
import subprocess
import sys
from datetime import date, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import weekly_call

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = pathlib.Path(__file__).with_name("weekly_call.py")
WATCHERS = REPO_ROOT / "cicd" / "scripts" / "status-page" / "watchers.json"


def _req(created_at: str) -> dict:
    return {"body": f"{weekly_call.REQUEST_MARKER} (2026-08-17)", "created_at": created_at}


def _res(created_at: str) -> dict:
    return {"body": f"{weekly_call.RESULT_MARKER}\n🔍 新しい問い 2 件", "created_at": created_at}


# --- ローテーション ------------------------------------------------------


def test_L1_同じ日付は同じ範囲で週が進むと全範囲を巡る():
    """無いと何が静かに通るか: ローテーションが日付に対して非決定的だと、
    同じ週に別の範囲が出て「先週見た場所」の追跡 (UNCOVERED の消化) が壊れる。
    巡回しないと特定の範囲だけが永遠に審査されない。"""
    monday = date(2026, 8, 17)  # 月曜
    scopes = [weekly_call.scope_for(monday + timedelta(weeks=i))[0] for i in range(4)]
    assert sorted(scopes) == sorted(weekly_call.ROTATION), "4 週で全範囲を巡っていない"
    assert weekly_call.scope_for(monday) == weekly_call.scope_for(monday)
    # 5 週目は 1 週目と同じ範囲に戻る
    assert weekly_call.scope_for(monday + timedelta(weeks=4)) == weekly_call.scope_for(monday)


def test_L1_ローテーションの範囲はリポジトリに実在する():
    """無いと何が静かに通るか: ディレクトリ構成の変更 (改名・移動) で ROTATION が
    指す先が消えても、依頼は文字列としては成立し続け、その範囲の週は
    「実在しない場所を審査した」ことになる (D4 宣言と実体の不一致そのもの)。"""
    for scope in weekly_call.ROTATION:
        assert (REPO_ROOT / scope).is_dir(), f"ROTATION の `{scope}` が存在しない"


def test_L1_範囲ディレクトリが無ければ落ちる(tmp_path):
    """無いと何が静かに通るか: 実在チェックが無いと、上のテストが CI で落ちても
    **既にマージ済みの workflow は毎週動き続け**、実在しない範囲への依頼が
    積まれ続ける。run 自体が赤くなること (→ report-failure) が止血になる。"""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "scope", "--repo-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "存在しません" in proc.stderr


# --- 未実施検知 ----------------------------------------------------------


def test_L1_依頼の後に結果が無ければ未実施として返す():
    """無いと何が静かに通るか: 未実施検知が無いと、審査されなかった週は台帳上
    「依頼コメントが 1 件あるだけ」で終わり、翌週の依頼と見分けが付かない。"""
    comments = [_req("2026-08-17T22:00:00Z")]
    assert weekly_call.unanswered_request(comments) == "2026-08-17T22:00:00Z"


def test_L1_依頼の後に結果が付けば解消():
    comments = [_req("2026-08-17T22:00:00Z"), _res("2026-08-18T09:00:00Z")]
    assert weekly_call.unanswered_request(comments) is None


def test_L1_古い結果では新しい依頼は解消されない():
    """無いと何が静かに通るか: 時系列を無視して「結果が 1 件でもあれば OK」に
    すると、審査が止まった 2 週目以降は永遠に警告が出ない (先週の結果が
    今週の依頼を解消してしまう)。"""
    comments = [
        _req("2026-08-10T22:00:00Z"),
        _res("2026-08-11T09:00:00Z"),
        _req("2026-08-17T22:00:00Z"),
    ]
    assert weekly_call.unanswered_request(comments) == "2026-08-17T22:00:00Z"


def test_L1_コメントゼロは未実施扱いにしない():
    """初回 (台帳を作った直後) を「前回未実施」と騒がせない。"""
    assert weekly_call.unanswered_request([]) is None


# --- 依頼コメントの形 ----------------------------------------------------


def test_L1_依頼は依頼マーカーで始まり結果マーカーで始まらない():
    """無いと何が静かに通るか: 依頼が結果マーカーで**始まる**と、status-page の
    startswith 判定 (labeled_issue_comment) が毎週の自動依頼を審査の心拍として
    数え、審査が止まっていても 🟢 が続く。逆に結果マーカーの指示 (本文中の引用)
    が消えると、審査役がマーカー無しで報告して心拍が付かず、実施しても 🔴 になる。"""
    md = weekly_call.build_request(
        "apps/bff/src", 1, date(2026, 8, 17), "https://example.test/run/1", None
    )
    assert md.startswith(weekly_call.REQUEST_MARKER)
    assert not md.startswith(weekly_call.RESULT_MARKER)
    assert weekly_call.RESULT_MARKER in md, "結果マーカーの指示が依頼から消えている"
    assert "apps/bff/src" in md
    assert "https://example.test/run/1" in md
    assert "UNKNOWN" in md, "UNKNOWN 明記の指示が消えている (#410 完遂条件)"


def test_L1_未実施があれば依頼に明示される():
    md = weekly_call.build_request(
        "cicd/scripts", 4, date(2026, 8, 24), "https://example.test/run/2",
        "2026-08-17T22:00:00Z",
    )
    assert "2026-08-17T22:00:00Z" in md
    assert "審査結果が付いていません" in md


# --- watchers.json との契約 ----------------------------------------------


def test_L1_マーカーと台帳ラベルはwatchersの定義と一致する():
    """無いと何が静かに通るか: マーカー文字列は「依頼を書く側 (このスクリプト)」と
    「心拍を数える側 (watchers.json)」の 2 箇所に現れる。片方だけ変えると、
    審査は実施されているのに状況ページが恒久 🔴 (またはその逆) になり、
    どちらが正しいか誰にも分からなくなる。このテストが正典 (このスクリプト) と
    watchers.json を突き合わせる。"""
    defs = json.loads(WATCHERS.read_text())
    rows = [
        t
        for t in defs.get("traces", [])
        if t.get("trace", {}).get("kind") == "labeled_issue_comment"
        and t["trace"].get("label") == weekly_call.LEDGER_LABEL
    ]
    assert len(rows) == 1, "watchers.json に debt-review の trace 行が 1 行必要"
    assert rows[0]["trace"]["body_startswith"] == weekly_call.RESULT_MARKER
    ids = [w["id"] for w in defs["workflows"]]
    assert "debt-review-request.yml" in ids, (
        "依頼 workflow が watchers.json に載っていない (CLAUDE.md: 足せないなら作らない)"
    )
