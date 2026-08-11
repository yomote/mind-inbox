#!/usr/bin/env python3
"""UX プローブ記録の封筒 (kind: "ux-probe-record") を作る / 取り出す。

`envelope` がデータブランチ蓄積 (ADR 0041) 用の現行の出口。golden-path-monitor が
これで作った 1 行 JSON を `cicd/scripts/ux-data/append-observation.sh` で
`data/ux-observations` の probes/*.jsonl に追記する。封筒の形の真実はこのモジュール
(読み取り側 ux_eval.py とは round-trip テストで結合している)。

`format` / `extract` は Issue コメント時代 (ADR 0029 / #162) のレガシー。移行完了
(#162 クローズ) 後に削除してよいが、それまでは旧コメントの形の記録として残す。
artifact を使わなかった理由は ADR 0029 (#160): agent セッションからは artifact の
ダウンロード先が egress ポリシーで拒否される。

使い方:
    probe-record-comment.py envelope <記録 JSON>             # 封筒 JSON 1 行を stdout へ
    probe-record-comment.py format  <記録 JSON>              # (レガシー) コメント本文
    probe-record-comment.py extract <コメント本文> <出力先ディレクトリ>  # (レガシー)

`extract` は出力先ディレクトリに記録 JSON を書き出し、そのディレクトリを stdout に出す。
採点する材料があるかの判断はここではせず `inspect-probe-artifact.py` に委ねる (責務の分離)。

終了コード:
    0 = 成功
    1 = 前提不足・想定外 (ファイルが無い / JSON が壊れている)
    2 = コメントに ux-probe-record ブロックが無い (extract のみ)
    5 = 記録が大きすぎて 1 コメントに収まらない (format のみ)
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

KIND = "ux-probe-record"

# GitHub のコメント上限は 65536 文字。ヘッダとフェンスの分を引いた安全側の閾値。
# 超えたら**切り詰めて投稿しない** — 途中で切れた JSON は「壊れた記録」として
# 採点側で弾かれるが、なぜ壊れたかが追えなくなるため、投稿しない方を選ぶ。
MAX_BODY_CHARS = 60000

_FENCE = re.compile(r"```json\s*(.*?)```", re.DOTALL)

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_NO_BLOCK = 2
EXIT_TOO_LARGE = 5


def log(message: str) -> None:
    print(message, file=sys.stderr)


def _fence_safe(payload: str) -> str:
    """JSON 中のバッククォートを `\\u0060` に置き換え、フェンスが早期終了しないようにする。

    `record` には実 AI の自由文がそのまま入るため、応答が Markdown のコード例を含むと
    ``` がフェンス内に現れ、そこで切れた壊れた JSON が投稿される。読み取り側では
    `json.loads` が失敗して「記録ブロックが無い」(exit 2) に見えるため、**記録があったのに
    材料なしとして静かに握り潰される** — 採点が止まった理由も追えなくなる。

    JSON の構造文字にバッククォートは含まれないので、出力中のバッククォートは必ず文字列
    リテラルの内側にある。したがって一律置換で安全で、`json.loads` が元の文字に戻すため
    往復も厳密に保たれる (base64 と違って人がコメントを読んで調査できる形も維持できる)。
    """
    return payload.replace("`", "\\u0060")


def _load_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log(f"JSON を読めません: {path}: {exc}")
        return None


def build_envelope(record: dict, run_id: str) -> dict:
    """封筒 (kind: "ux-probe-record") を組み立てる (純粋関数)。ここが形の真実。"""
    scenario = (
        record.get("scenario") if isinstance(record.get("scenario"), dict) else {}
    )
    turns = record.get("turns") if isinstance(record.get("turns"), list) else []
    return {
        "kind": KIND,
        "runId": run_id,
        "scenarioId": scenario.get("id"),
        "plannedTurns": scenario.get("plannedTurns"),
        "completedTurns": len(turns),
        "probeId": record.get("probeId"),
        "record": record,
    }


def envelope_line(probe_json: Path, run_id: str, now: datetime | None = None) -> int:
    """データブランチ追記用の封筒 JSON を 1 行で stdout に出す (ADR 0041)。

    recordedAt (追記時刻 = 鮮度判定の基準) を持たせる — Issue コメント時代の
    created_at に相当し、これが無いと ux_eval の鮮度・月振り分けが働かない。
    """
    record = _load_json(probe_json)
    if record is None:
        return EXIT_UNEXPECTED
    if not isinstance(record, dict):
        log(f"記録 JSON がオブジェクトではありません: {probe_json}")
        return EXIT_UNEXPECTED
    now = now or datetime.now(timezone.utc)
    envelope = {
        **build_envelope(record, run_id),
        "recordedAt": now.isoformat().replace("+00:00", "Z"),
    }
    print(json.dumps(envelope, ensure_ascii=False))
    return EXIT_OK


def format_comment(probe_json: Path, run_id: str, run_url: str) -> int:
    record = _load_json(probe_json)
    if record is None:
        return EXIT_UNEXPECTED
    if not isinstance(record, dict):
        log(f"記録 JSON がオブジェクトではありません: {probe_json}")
        return EXIT_UNEXPECTED

    scenario = (
        record.get("scenario") if isinstance(record.get("scenario"), dict) else {}
    )
    turns = record.get("turns") if isinstance(record.get("turns"), list) else []

    envelope = build_envelope(record, run_id)

    run_line = f"[run {run_id}]({run_url})" if run_url else f"run {run_id}"
    body = "\n".join(
        [
            "## UX プローブ記録",
            "",
            f"- {run_line}",
            f"- scenario: `{scenario.get('id', '?')}` / "
            f"完了 {len(turns)}/{scenario.get('plannedTurns', '?')} 往復",
            "",
            "毎朝の UX 採点 Routine がこのブロックを読みます "
            "(このスレッドに人間が返信しないでください / ADR 0029)。",
            "",
            "```json",
            _fence_safe(json.dumps(envelope, ensure_ascii=False)),
            "```",
        ]
    )

    if len(body) > MAX_BODY_CHARS:
        log(f"記録が大きすぎます ({len(body)} 文字 > {MAX_BODY_CHARS})。投稿しません。")
        log("  → artifact 側には残っているので、そちらから調査してください。")
        return EXIT_TOO_LARGE

    print(body)
    return EXIT_OK


def extract_record(comment_body: Path, out_dir: Path) -> int:
    try:
        text = comment_body.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log(f"コメント本文を読めません: {comment_body}: {exc}")
        return EXIT_UNEXPECTED

    for block in _FENCE.findall(text):
        try:
            envelope = json.loads(block)
        except json.JSONDecodeError:
            # 同じコメントに別用途の JSON ブロックが混ざっていても落ちないようにする
            continue
        if not isinstance(envelope, dict) or envelope.get("kind") != KIND:
            continue
        record = envelope.get("record")
        if record is None:
            log("ux-probe-record ブロックに record がありません。")
            return EXIT_UNEXPECTED

        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "probe.json").write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            log(f"記録を書き出せません: {out_dir}: {exc}")
            return EXIT_UNEXPECTED

        log(
            f"記録を書き出しました: {out_dir / 'probe.json'} (run {envelope.get('runId', '?')})"
        )
        print(out_dir)
        return EXIT_OK

    log(f"コメントに kind={KIND} の JSON ブロックがありません: {comment_body}")
    return EXIT_NO_BLOCK


def main(argv: list[str]) -> int:
    name = Path(argv[0]).name
    if len(argv) >= 2 and argv[1] == "envelope" and len(argv) == 3:
        import os

        return envelope_line(Path(argv[2]), os.environ.get("GITHUB_RUN_ID", "?"))

    if len(argv) >= 2 and argv[1] == "format" and len(argv) == 3:
        import os

        run_id = os.environ.get("GITHUB_RUN_ID", "?")
        server = os.environ.get("GITHUB_SERVER_URL", "")
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        run_url = f"{server}/{repo}/actions/runs/{run_id}" if server and repo else ""
        return format_comment(Path(argv[2]), run_id, run_url)

    if len(argv) >= 2 and argv[1] == "extract" and len(argv) == 4:
        return extract_record(Path(argv[2]), Path(argv[3]))

    log(f"使い方: {name} envelope <記録 JSON>")
    log(f"        {name} format <記録 JSON>  (レガシー)")
    log(f"        {name} extract <コメント本文> <出力先ディレクトリ>  (レガシー)")
    return EXIT_UNEXPECTED


if __name__ == "__main__":
    sys.exit(main(sys.argv))
