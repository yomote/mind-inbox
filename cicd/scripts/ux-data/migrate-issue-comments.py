#!/usr/bin/env python3
"""#162 / #127 の既存 Issue コメントを、データブランチ用の payload に変換する (ADR 0041 D7)。

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
      comments.json = [{"body": str, "created_at": ISO8601,
                        "login": str, "author_association": str}, ...] (gh api の出力)
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

# 移行を許す投稿者 (PR #260 Codex P1 指摘): #162/#127 は誰でもコメントできるため、
# 投稿者を見ないと第三者のコメントが観測データとして永続化される。正規の記録は
# workflow (github-actions[bot]) と PO/PM (リポジトリ OWNER) しか投稿していない。
TRUSTED_LOGINS = {"github-actions[bot]"}
TRUSTED_ASSOCIATIONS = {"OWNER"}


def _is_trusted(comment: dict) -> bool:
    return (
        comment.get("login") in TRUSTED_LOGINS
        or comment.get("author_association") in TRUSTED_ASSOCIATIONS
    )


def _numeric_or_none(value) -> bool:
    return value is None or (
        isinstance(value, (int, float)) and not isinstance(value, bool)
    )


def _valid_shape(obj: dict) -> bool:
    """kind ごとの下流 (ux_eval / status-page trend) が触る形を検証する。

    型だけ偽装したデータ (例: avgMs が文字列) が描画側の TypeError で
    status-page を止めるのを移行時点で防ぐ (PR #260 Codex P1)。
    """
    kind = obj.get("kind")
    if kind == "ux-probe-record":
        return isinstance(obj.get("record"), dict)
    if kind == "ux-eval-mech":
        metrics = obj.get("metrics")
        if metrics is None:
            return (
                True  # 欠落は下流で欠測扱い。偽装で問題になるのは「在るのに型が違う」
            )
        if not isinstance(metrics, dict):
            return False
        latency = metrics.get("latency") or {}
        if not isinstance(latency, dict):
            return False
        for stat in latency.values():
            if not isinstance(stat, dict):
                return False
            if not all(_numeric_or_none(v) for v in stat.values()):
                return False
        warnings = metrics.get("warnings") or {}
        thresholds = metrics.get("thresholds") or {}
        if not isinstance(warnings, dict) or not isinstance(thresholds, dict):
            return False
        return all(_numeric_or_none(v) for v in thresholds.values())
    if kind == "ux-judge-score":
        scores = obj.get("scores")
        return scores is None or isinstance(scores, dict)
    return False


_FENCE = re.compile(r"```json\s*(.*?)```", re.DOTALL)

EXIT_OK = 0
EXIT_UNEXPECTED = 1


def log(message: str) -> None:
    print(message, file=sys.stderr)


def extract_observations(comments: list) -> list[dict]:
    """コメント一覧から観測 payload を時系列順に取り出す (純粋関数)。

    1 コメントに複数の対象ブロックがあれば全部取り込む (実運用では 1 つだが、
    切り捨てると静かにデータが欠ける)。壊れたブロック・対象外 kind は読み飛ばす。
    信頼できない投稿者のコメントと、下流が触る形を満たさないブロックは
    件数を報告した上で捨てる (静かに混入させない / 静かに欠けさせない)。
    """
    found: list[tuple[str, dict]] = []
    untrusted = 0
    malformed = 0
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        body = comment.get("body")
        created = comment.get("created_at")
        if not isinstance(body, str) or not isinstance(created, str):
            continue
        if not _is_trusted(comment):
            if any(
                json.loads(b).get("kind") in KINDS
                for b in _FENCE.findall(body)
                if _loads_dict(b)
            ):
                untrusted += 1
            continue
        for block in _FENCE.findall(body):
            try:
                obj = json.loads(block)
            except json.JSONDecodeError:
                continue
            if not (isinstance(obj, dict) and obj.get("kind") in KINDS):
                continue
            if not _valid_shape(obj):
                malformed += 1
                continue
            found.append((created, {**obj, "recordedAt": created}))
    if untrusted:
        log(
            f"信頼できない投稿者の観測風コメントを {untrusted} 件捨てた (login/author_association を確認)"
        )
    if malformed:
        log(f"形の壊れた観測ブロックを {malformed} 件捨てた (kind ごとのスキーマ検証)")
    found.sort(key=lambda pair: pair[0])
    return [payload for _, payload in found]


def _loads_dict(block: str) -> bool:
    try:
        return isinstance(json.loads(block), dict)
    except json.JSONDecodeError:
        return False


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
