"""[単体] 週次 AI 負債審査の依頼組み立て — 「依頼」を「審査の心拍」に化けさせない。

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


def _rem(created_at: str) -> dict:
    return {"body": f"{weekly_call.REMINDER_MARKER} (2026-08-24)", "created_at": created_at}


# --- ローテーション ------------------------------------------------------


def test_単体_同じ日付は同じ範囲で週が進むと全範囲を巡る():
    """無いと何が静かに通るか: ローテーションが日付に対して非決定的だと、
    同じ週に別の範囲が出て「先週見た場所」の追跡 (UNCOVERED の消化) が壊れる。
    巡回しないと特定の範囲だけが永遠に審査されない。"""
    monday = date(2026, 8, 17)  # 月曜
    scopes = [weekly_call.scope_for(monday + timedelta(weeks=i))[0] for i in range(4)]
    assert sorted(scopes) == sorted(weekly_call.ROTATION), "4 週で全範囲を巡っていない"
    assert weekly_call.scope_for(monday) == weekly_call.scope_for(monday)
    # 5 週目は 1 週目と同じ範囲に戻る
    assert weekly_call.scope_for(monday + timedelta(weeks=4)) == weekly_call.scope_for(monday)


def test_単体_ローテーションの範囲はリポジトリに実在する():
    """無いと何が静かに通るか: ディレクトリ構成の変更 (改名・移動) で ROTATION が
    指す先が消えても、依頼は文字列としては成立し続け、その範囲の週は
    「実在しない場所を審査した」ことになる (D4 宣言と実体の不一致そのもの)。"""
    for scope in weekly_call.ROTATION:
        assert (REPO_ROOT / scope).is_dir(), f"ROTATION の `{scope}` が存在しない"


def test_単体_範囲ディレクトリが無ければ落ちる(tmp_path):
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


def test_単体_依頼の後に結果が無ければ未実施として返す():
    """無いと何が静かに通るか: 未実施検知が無いと、審査されなかった週は台帳上
    「依頼コメントが 1 件あるだけ」で終わり、翌週の依頼と見分けが付かない。"""
    comments = [_req("2026-08-17T22:00:00Z")]
    assert weekly_call.unanswered_request(comments) == "2026-08-17T22:00:00Z"


def test_単体_依頼の後に結果が付けば解消():
    """無いと何が静かに通るか: 解消判定が壊れると、審査を実施しても翌週から
    再掲 (⏰) が出続けて新しい依頼が永遠に積まれず、ローテーションが止まったまま
    誤警報が台帳と状況ページの信頼を削る (誤検知は本物の赤を見なくさせる)。"""
    comments = [_req("2026-08-17T22:00:00Z"), _res("2026-08-18T09:00:00Z")]
    assert weekly_call.unanswered_request(comments) is None


def test_単体_古い結果では新しい依頼は解消されない():
    """無いと何が静かに通るか: 時系列を無視して「結果が 1 件でもあれば OK」に
    すると、審査が止まった 2 週目以降は永遠に警告が出ない (先週の結果が
    今週の依頼を解消してしまう)。"""
    comments = [
        _req("2026-08-10T22:00:00Z"),
        _res("2026-08-11T09:00:00Z"),
        _req("2026-08-17T22:00:00Z"),
    ]
    assert weekly_call.unanswered_request(comments) == "2026-08-17T22:00:00Z"


def test_単体_コメントゼロは未実施扱いにしない():
    """無いと何が静かに通るか: 初回 (台帳を作った直後) を「前回未実施」と騒がせる
    と、最初の依頼から誤警報になり、以後の警告が読み飛ばされるようになる。"""
    assert weekly_call.unanswered_request([]) is None


def test_単体_再掲は依頼にも結果にも数えない():
    """無いと何が静かに通るか: 再掲 (⏰) が依頼 (📮) に数えられると再掲のたびに
    pending が新しい方へ動いて「どの依頼が滞っているか」がずれ、結果 (🔍) に
    数えられると再掲を投稿しただけで未実施が解消扱いになる。"""
    comments = [_req("2026-08-17T22:00:00Z"), _rem("2026-08-24T22:00:00Z")]
    assert weekly_call.unanswered_request(comments) == "2026-08-17T22:00:00Z"


# --- 未解消の依頼は高々 1 件 (PR #465 Codex P1) ---------------------------


def test_単体_未解消の依頼がある週は新しい依頼を積まず再掲になる(tmp_path):
    """無いと何が静かに通るか (PR #465 Codex P1 の再現):
    未解消のまま新しい依頼 (📮) を積むと未解消の依頼が 2 件並ぶ。依頼文の指示
    どおり**古い方から**処理して結果 (🔍) を投稿すると、その結果は時系列上
    最新依頼より後に来るため、まだ審査していない最新の範囲まで解消扱いになり、
    翌週の警告が消えて未検証の範囲が「実施済みの顔」で進む。再掲 (⏰) なら
    依頼が 2 件並ぶ状態そのものが作れない。"""
    comments_file = tmp_path / "comments.jsonl"
    comments_file.write_text(json.dumps(_req("2026-08-17T22:00:00Z")) + "\n")
    proc = subprocess.run(
        [
            sys.executable, str(SCRIPT), "request",
            "--repo-root", str(REPO_ROOT),
            "--date", "2026-08-24",
            "--run-url", "https://example.test/run/2",
            "--comments", str(comments_file),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert out.startswith(weekly_call.REMINDER_MARKER), "未解消なのに再掲になっていない"
    assert not out.startswith(weekly_call.REQUEST_MARKER), "未解消なのに新しい依頼を積んでいる"
    assert "2026-08-17T22:00:00Z" in out, "どの依頼が滞っているか再掲から追えない"
    # 再掲を台帳に足しても pending は動かない (依頼 2 件問題が構造的に起きない)
    comments = [_req("2026-08-17T22:00:00Z"), {"body": out[:200], "created_at": "2026-08-24T22:00:30Z"}]
    assert weekly_call.unanswered_request(comments) == "2026-08-17T22:00:00Z"
    # その後に結果が付けば、唯一の pending が解消される
    comments.append(_res("2026-08-25T09:00:00Z"))
    assert weekly_call.unanswered_request(comments) is None


def test_単体_再掲には滞っている依頼の日時が明示される():
    """無いと何が静かに通るか: 再掲に前回依頼の日時が無いと、台帳のどの依頼が
    滞っているのか読み手が特定できず、「先に前回分を実施」の指示が実行できない
    (依頼が流れたまま誰も気づかない)。"""
    md = weekly_call.build_reminder(
        date(2026, 8, 24), "https://example.test/run/2", "2026-08-17T22:00:00Z"
    )
    assert md.startswith(weekly_call.REMINDER_MARKER)
    assert not md.startswith(weekly_call.REQUEST_MARKER)
    assert not md.startswith(weekly_call.RESULT_MARKER)
    assert "2026-08-17T22:00:00Z" in md
    assert "審査結果が付いていません" in md


# --- 依頼コメントの形 ----------------------------------------------------


def test_単体_依頼は依頼マーカーで始まり結果マーカーで始まらない():
    """無いと何が静かに通るか: 依頼が結果マーカーで**始まる**と、status-page の
    startswith 判定 (labeled_issue_comment) が毎週の自動依頼を審査の心拍として
    数え、審査が止まっていても 🟢 が続く。逆に結果マーカーの指示 (本文中の引用)
    が消えると、審査役がマーカー無しで報告して心拍が付かず、実施しても 🔴 になる。"""
    md = weekly_call.build_request(
        "apps/bff/src", 1, date(2026, 8, 17), "https://example.test/run/1"
    )
    assert md.startswith(weekly_call.REQUEST_MARKER)
    assert not md.startswith(weekly_call.RESULT_MARKER)
    assert weekly_call.RESULT_MARKER in md, "結果マーカーの指示が依頼から消えている"
    assert "apps/bff/src" in md
    assert "https://example.test/run/1" in md
    assert "UNKNOWN" in md, "UNKNOWN 明記の指示が消えている (#410 完遂条件)"


# --- watchers.json との契約 ----------------------------------------------


def test_単体_マーカーと台帳ラベルはwatchersの定義と一致する():
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
    # 再掲 (⏰) が結果の心拍に数えられないこと (どちらも同じ台帳に載る)
    assert not weekly_call.REMINDER_MARKER.startswith(rows[0]["trace"]["body_startswith"])
    ids = [w["id"] for w in defs["workflows"]]
    assert "debt-review-request.yml" in ids, (
        "依頼 workflow が watchers.json に載っていない (CLAUDE.md: 足せないなら作らない)"
    )
