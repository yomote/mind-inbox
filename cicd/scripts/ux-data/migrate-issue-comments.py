#!/usr/bin/env python3
"""#162 / #127 の既存 Issue コメントを、データブランチ用の payload に変換する (ADR 0040 D7)。

one-shot の移行スクリプト。Issue コメント時代 (ADR 0029 / 0037) の蓄積を
JSONL 蓄積 (append.py の形) に取り込むための前処理を担う:

    コメント一覧 JSON → 1 観測 = 1 payload ファイル (時系列順の連番)

payload には `recordedAt` としてコメントの created_at を入れる — 移行後も
「いつ観測されたか」の時系列が Issue コメント時代と同じ基準で保たれる。
月別ファイルへの振り分け・重複スキップは append.py が担うので、ここでは
**変換だけ**を行う (再実行しても append.py 側で弾かれる)。

旧コメントの封筒の形 (フェンス付き JSON / kind マーカー) は歴史的事実として凍結
されているため、正典 (probe-record-comment.py) には依存せず自前の切り出しで読む。
バッククォートの \\u0060 置換 (probe-record-comment.py の fence-safe) は
json.loads が元の文字に戻すので、ここで特別扱いは要らない。

使い方:
    migrate-issue-comments.py <out_dir> <comments.json>...
      comments.json = [{"body": str, "created_at": ISO8601}, ...] (gh api の出力)
      out_dir       = payload ファイル (NNNN-<kind>.json) を書き出す先

stdout には書き出した payload のパスを 1 行 1 件で出す (診断は stderr)。

終了コード:
    0 = 変換した (0 件でも成功 — 件数は stderr で報告)
    1 = 前提不足・想定外 (ファイルが無い / JSON が壊れている)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# 取り込む kind (それ以外の JSON ブロックは移行対象外として読み飛ばす)
KINDS = {"ux-probe-record", "ux-eval-mech", "ux-judge-score"}

_FENCE = re.compile(r"```json\s*(.*?)```", re.DOTALL)

EXIT_OK = 0
EXIT_UNEXPECTED = 1


def log(message: str) -> None:
    print(message, file=sys.stderr)


def extract_observations(comments: list) -> list[dict]:
    """コメント一覧から観測 payload を時系列順に取り出す (純粋関数)。

    1 コメントに複数の対象ブロックがあれば全部取り込む (実運用では 1 つだが、
    切り捨てると静かにデータが欠ける)。壊れたブロック・対象外 kind は読み飛ばす。
    """
    found: list[tuple[str, dict]] = []
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        body = comment.get("body")
        created = comment.get("created_at")
        if not isinstance(body, str) or not isinstance(created, str):
            continue
        for block in _FENCE.findall(body):
            try:
                obj = json.loads(block)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("kind") in KINDS:
                found.append((created, {**obj, "recordedAt": created}))
    found.sort(key=lambda pair: pair[0])
    return [payload for _, payload in found]


def run(out_dir: Path, comment_files: list[Path]) -> int:
    comments: list = []
    for path in comment_files:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            log(f"コメント一覧を読めません: {path}: {exc}")
            return EXIT_UNEXPECTED
        if not isinstance(loaded, list):
            log(f"コメント一覧が配列ではありません: {path}")
            return EXIT_UNEXPECTED
        comments.extend(loaded)

    observations = extract_observations(comments)
    counts: dict[str, int] = {}
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, payload in enumerate(observations, start=1):
        kind = payload["kind"]
        counts[kind] = counts.get(kind, 0) + 1
        path = out_dir / f"{i:04d}-{kind}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(path)

    log(f"変換 {len(observations)} 件: " + json.dumps(counts, ensure_ascii=False))
    if not observations:
        log("観測が 1 件もありません — コメント一覧の取得を確認してください。")
    return EXIT_OK


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        log(f"使い方: {Path(argv[0]).name} <out_dir> <comments.json>...")
        return EXIT_UNEXPECTED
    return run(Path(argv[1]), [Path(p) for p in argv[2:]])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
