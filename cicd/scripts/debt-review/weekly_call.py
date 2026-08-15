#!/usr/bin/env python3
"""週次 AI 負債審査 (debt-review) の依頼文を組み立てる (#410)。

役割 — **審査はしない**。審査本体は `.claude/agents/debt-reviewer.md`
(基準は `.github/claude/debt-rubric.md` D1〜D7) を当番 PM tick / 窓口 PM が
新品コンテキストで回す。LLM を Actions で走らせる経路は却下済み
(`debt-check.yml` 冒頭 / ADR 0035 D5) なので、CI が持つのは「依頼と痕跡」だけ:

    - 週替わりの審査範囲を決める (ROTATION — 毎週リポジトリ全体は現実的でない)
    - 台帳 Issue へ依頼コメントを投稿する (投稿は workflow 側)
    - 前回の依頼に審査結果が付いていなければ、翌週の依頼にそれを明示する —
      「呼ばれなかった週」と「呼ばれて 0 件だった週」を外形で区別する (#410 の核)

審査結果の生死は status-page の trace (labeled_issue_comment) が見る。
マーカー文字列と台帳ラベルは**このファイルが正典** — watchers.json 側とのズレは
test_weekly_call.py の契約テストが落とす。

使い方:
    weekly_call.py scope [--repo-root DIR] [--date YYYY-MM-DD]
        今週の範囲を 1 行で stdout に出す。範囲ディレクトリが実在しなければ exit 1
        (黙って通すと、実在しない場所への依頼が毎週「審査済みの顔」で積まれる)。
    weekly_call.py request --run-url URL [--repo-root DIR] [--date YYYY-MM-DD]
                           [--comments FILE.jsonl]
        依頼コメントの Markdown を stdout に出す。--comments は台帳 Issue の
        既存コメント (1 行 1 JSON: {"body": ..., "created_at": ...})。
    weekly_call.py ledger-body
        台帳 Issue の本文 (初回作成時に workflow が使う)。

終了コード: 0 = 組み立てた / 1 = 前提不足 (範囲ディレクトリ欠落・入力が読めない)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))

# 台帳 Issue のラベル (workflow が Issue を見つける手掛かり / status-page の trace が読む)
LEDGER_LABEL = "debt-review"

# コメントの種別マーカー。**先頭一致 (startswith) で判定する** — 依頼コメントは
# 本文中で結果マーカーを引用する (「このマーカーで始めて」と指示する) ため、
# 部分一致にすると毎週の自動依頼が審査の心拍に化け、審査が止まっても 🟢 が続く。
REQUEST_MARKER = "📮 debt-review 審査依頼"
RESULT_MARKER = "🔍 debt-review 審査結果"

# 週替わりの審査範囲 (Issue #410 §2-2)。ISO 週番号で巡回する。
# 年替わり (第 52/53 週 → 第 1 週) は番号が連続しないため、同じ範囲が 2 週続く /
# 1 つ飛ぶことがある — 依頼コメントに毎回範囲を明記するので、ズレても
# 「どこを見ていないか」は台帳から読める (ズレを隠さない)。
ROTATION = (
    "apps/bff/src",
    "apps/frontend/src",
    "apps/services/ai-agent",
    "cicd/scripts",
)


def scope_for(day: date) -> tuple[str, int]:
    """その日付の週の審査範囲を返す (範囲, ローテーション位置 1 始まり)。純粋関数。"""
    index = day.isocalendar().week % len(ROTATION)
    return ROTATION[index], index + 1


def unanswered_request(comments: list[dict]) -> str | None:
    """直近の依頼に結果が付いていなければ、その依頼の created_at を返す。純粋関数。

    依頼 (📮) の**後に**結果 (🔍) が付いたら解消とみなす。結果が依頼より前に
    しか無い状態 (先週の結果だけある) は未実施として返す — 時系列を無視して
    「結果コメントが 1 件でもあれば OK」にすると、審査が止まった 2 週目から
    永遠に警告が出なくなる。
    """
    stamped = sorted(
        (c for c in comments if c.get("created_at")),
        key=lambda c: c["created_at"],
    )
    pending: str | None = None
    for c in stamped:
        body = c.get("body") or ""
        if body.startswith(REQUEST_MARKER):
            pending = c["created_at"]
        elif body.startswith(RESULT_MARKER):
            pending = None
    return pending


def build_request(
    scope: str,
    position: int,
    day: date,
    run_url: str,
    unanswered_at: str | None,
) -> str:
    """依頼コメントの Markdown を組み立てる。純粋関数。

    先頭は必ず REQUEST_MARKER (unanswered_request が依頼を数える手掛かり)。
    RESULT_MARKER は本文の指示として**先頭以外に**含める。
    """
    lines = [
        f"{REQUEST_MARKER} ({day.isoformat()})",
        "",
        f"- **今週の範囲: `{scope}`** (週替わりローテーション {position}/{len(ROTATION)})",
        f"- run: {run_url}",
    ]
    if unanswered_at:
        lines += [
            "",
            f"⚠️ **前回の依頼 ({unanswered_at}) に審査結果が付いていません — 未実施のまま 1 週が経過しています。**"
            "「呼ばれなかった週」を「指摘 0 件の週」と混同しないため、先に前回分を実施するか、"
            "実施しない判断をこのコメントへの返信で残してください。",
        ]
    lines += [
        "",
        "## やること (当番 PM tick / 窓口 PM)",
        "",
        "1. `debt-reviewer` subagent を**新品コンテキスト**で起動し、範囲に "
        f"`{scope}` を渡す (基準は `.github/claude/debt-rubric.md` D1〜D7)",
        f"2. 報告を **この Issue に `{RESULT_MARKER}` で始まるコメント**としてそのまま貼る "
        "(rubric の出力ルールの形: 1 行 verdict / 調査した範囲と母集団 / findings テーブル / "
        "T3 別表 / **UNKNOWN 一覧** / UNCOVERED の消化状況)。**範囲と UNKNOWN の無い報告は貼らない** "
        "— 見ていない場所を「問題なし」と読ませないため",
        "3. 段 T1/T2 の問いは **1 件ずつ Issue に降ろす** (降ろし先: `detect.py` の検出器 / "
        "lint / 契約テスト)。降ろした Issue 番号を結果コメントに書く — "
        "「AI が指摘して終わり」なら回す意味が無い (#410 完遂条件)",
        "",
        "---",
        "",
        f"この依頼は `.github/workflows/debt-review-request.yml` が毎週投稿しています。"
        f"結果コメント (先頭が `{RESULT_MARKER}`) が途絶えると "
        "[状況ページ](https://yomote.github.io/mind-inbox/status/) の「週次 AI 負債審査の結果」行が 🔴 になります。",
    ]
    return "\n".join(lines)


LEDGER_BODY = f"""週次 AI 負債審査 (#410) の**依頼と結果の台帳**。

- 毎週月曜 07:00 JST に `.github/workflows/debt-review-request.yml` が
  `{REQUEST_MARKER}` で始まる依頼コメントを投稿する (範囲は週替わりローテーション)
- 当番 PM tick / 窓口 PM が `debt-reviewer` subagent (`.github/claude/debt-rubric.md`
  D1〜D7) を新品コンテキストで回し、報告を `{RESULT_MARKER}` で始まるコメントで残す
- 段 T1/T2 の問いは機械側 (`cicd/scripts/debt-check/detect.py` の検出器 / lint /
  契約テスト) に降ろす — 回すほど AI の担当範囲が減るのが rubric の設計思想
- 審査の生死は [状況ページ](https://yomote.github.io/mind-inbox/status/) が
  結果マーカーの最終時刻で判定する (`cicd/scripts/status-page/watchers.json`)

**この Issue は閉じない** (台帳)。閉じると workflow が次回 run で新しい台帳を作る。
"""


def _load_comments(path: str | None) -> list[dict]:
    if not path:
        return []
    p = Path(path)
    if not p.is_file():
        # 「コメント取得を飛ばした」と「コメントが 0 件」を混同しない — 前者は
        # 未実施検知が黙って無効になるので落とす
        print(f"ERROR: コメントファイルがありません: {path}", file=sys.stderr)
        raise SystemExit(1)
    comments = []
    for n, line in enumerate(p.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"ERROR: {path}:{n} を JSON として読めません: {e}", file=sys.stderr)
            raise SystemExit(1)
        if isinstance(obj, dict):
            comments.append(obj)
    return comments


def _resolve_scope(repo_root: str, day: date) -> tuple[str, int]:
    scope, position = scope_for(day)
    if not (Path(repo_root) / scope).is_dir():
        print(
            f"ERROR: 審査範囲 `{scope}` がリポジトリに存在しません。"
            "ディレクトリ構成が変わったら ROTATION も直すこと "
            "(黙って通すと実在しない範囲への依頼が毎週積まれる)",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return scope, position


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_scope = sub.add_parser("scope", help="今週の審査範囲を出す")
    p_req = sub.add_parser("request", help="依頼コメントの Markdown を出す")
    sub.add_parser("ledger-body", help="台帳 Issue の本文を出す")

    for p in (p_scope, p_req):
        p.add_argument("--repo-root", default=".")
        p.add_argument("--date", help="YYYY-MM-DD (省略時は今日 JST)")
    p_req.add_argument("--run-url", required=True)
    p_req.add_argument("--comments", help="台帳の既存コメント (1 行 1 JSON)")

    args = parser.parse_args(argv)

    if args.command == "ledger-body":
        print(LEDGER_BODY)
        return 0

    day = (
        date.fromisoformat(args.date)
        if args.date
        else datetime.now(JST).date()
    )
    scope, position = _resolve_scope(args.repo_root, day)

    if args.command == "scope":
        print(scope)
        return 0

    comments = _load_comments(args.comments)
    print(
        build_request(
            scope, position, day, args.run_url, unanswered_request(comments)
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
