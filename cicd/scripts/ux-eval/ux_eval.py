#!/usr/bin/env python3
"""UX プローブ記録から機械計測を抽出し、スコアボードへのコメント本文を作る (ADR 0037)。

なぜあるか:
    ux-judge Routine (claude.ai) は実行履歴がリポジトリに残らず、一度も投稿しない
    まま沈黙していた (ADR 0035)。LLM を Actions で走らせる経路は追加課金 (API キー)
    か長期クレデンシャル (OAuth トークン) を要するため全部却下 (ADR 0008/0009/0035 D5)。
    そこで**機械で計算できる計測だけ**をこの script に切り出して Actions (ux-eval.yml)
    で毎朝走らせ、LLM 採点 (ux-judge-score) は PM セッションの日次 tick が subagent で
    行う — 分担の判断は ADR 0037。

責務 (LLM 判断は含めない):
    - 記録 Issue #162 のコメント一覧 (JSON) から最新の ux-probe-record を選ぶ
    - 鮮度を確かめる (既定 26 時間)。**古い記録を「今日の計測」として積まない** —
      ここを黙って通すと、プローブが止まっているのにトレンドが伸び続ける
    - 記録 JSON から区間レイテンシ統計 / 往復数 / 警告・エラー数を計算する
    - スコアボード Issue #127 への投稿本文 (kind: "ux-eval-mech") を stdout に出す

封筒の形 (kind: "ux-probe-record") の真実は cicd/scripts/ux-probe/probe-record-comment.py。
test_ux_eval.py が format との往復を実 module で検証している (片方だけ直すと落ちる)。

使い方:
    ux_eval.py <comments.json>
      comments.json = [{"body": str, "created_at": ISO8601}, ...] (gh api の出力)
      環境変数: UX_EVAL_MAX_AGE_HOURS (既定 26) / GITHUB_RUN_ID / GITHUB_SERVER_URL /
                GITHUB_REPOSITORY (計測 run へのリンク用・無くても動く)

診断は stderr、成果物 (コメント本文) だけを stdout に出す (PR #88 で踏んだ実例と同じ規律)。

終了コード:
    0 = 計測できた (コメント本文を stdout に出力)
    3 = 鮮度内の記録が無い (記録ゼロ / 全部古い) — 呼び出し側は run を赤にする
    1 = 前提不足・想定外 (ファイルが無い / JSON が壊れている)
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RECORD_KIND = "ux-probe-record"
OUTPUT_KIND = "ux-eval-mech"
DEFAULT_MAX_AGE_HOURS = 26.0

# probe-record-comment.py と同じフェンス規則 (封筒の真実はあちら)
_FENCE = re.compile(r"```json\s*(.*?)```", re.DOTALL)

# 記録 JSON の turns[].timings のキー (真実: apps/frontend/e2e-live/ux-probe.spec.ts の
# TurnRecord。実コメント #162 でも確認済み — 2026-08-10)
SEGMENTS = (
    "sendToTrpcResponseMs",
    "sendToReplyVisibleMs",
    "replyVisibleToTtsRequestMs",
    "ttsRequestToResponseMs",
    "sendToTtsResponseMs",
)

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_NO_FRESH_RECORD = 3


def log(message: str) -> None:
    print(message, file=sys.stderr)


def parse_probe_comment(body: str) -> dict | None:
    """コメント本文から ux-probe-record の封筒を取り出す。無ければ None。"""
    for block in _FENCE.findall(body):
        try:
            envelope = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(envelope, dict) and envelope.get("kind") == RECORD_KIND:
            if isinstance(envelope.get("record"), dict):
                return envelope
    return None


def parse_ts(ts: str | None) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def latest_record(comments: list) -> tuple[dict, datetime] | None:
    """コメント一覧から最新の記録 (封筒, created_at) を返す。無ければ None。

    created_at で選ぶ (配列の並び順に依存しない — ページネーションで
    順序の保証が崩れても最新を取り違えないため)。
    """
    best: tuple[dict, datetime] | None = None
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        created = parse_ts(comment.get("created_at"))
        body = comment.get("body")
        if created is None or not isinstance(body, str):
            continue
        envelope = parse_probe_comment(body)
        if envelope is None:
            continue
        if best is None or created > best[1]:
            best = (envelope, created)
    return best


def is_fresh(created_at: datetime, now: datetime, max_age_hours: float) -> bool:
    """記録がまだ「今日の計測」として使える鮮度かを判定する (純粋関数)。"""
    return now - created_at <= timedelta(hours=max_age_hours)


def _stats(values: list[float]) -> dict:
    if not values:
        return {"samples": 0, "minMs": None, "avgMs": None, "maxMs": None}
    return {
        "samples": len(values),
        "minMs": min(values),
        "avgMs": round(sum(values) / len(values)),
        "maxMs": max(values),
    }


def measure(record: dict) -> dict:
    """記録 JSON から機械計測を計算する (純粋関数)。

    null (欠測) は統計から除外し、除外した数を missing として残す —
    欠測を 0ms として混ぜると平均が静かに良く見えてしまう。
    """
    turns = record.get("turns") if isinstance(record.get("turns"), list) else []
    scenario = (
        record.get("scenario") if isinstance(record.get("scenario"), dict) else {}
    )

    latency: dict[str, dict] = {}
    for seg in SEGMENTS:
        values: list[float] = []
        for turn in turns:
            timings = turn.get("timings") if isinstance(turn, dict) else None
            v = timings.get(seg) if isinstance(timings, dict) else None
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                values.append(v)
        latency[seg] = {**_stats(values), "missing": len(turns) - len(values)}

    warnings = {"latency": 0, "functional": 0, "other": 0}
    tts_error_turns = 0
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        for warning in turn.get("warnings") or []:
            category = warning.get("category") if isinstance(warning, dict) else None
            warnings[category if category in warnings else "other"] += 1
        status = turn.get("ttsStatus")
        # null は「未観測」(functional warning が数える)。エラーは非 200 だけ数える
        if isinstance(status, int) and status != 200:
            tts_error_turns += 1

    first = turns[0] if turns and isinstance(turns[0], dict) else {}
    first_timings = (
        first.get("timings") if isinstance(first.get("timings"), dict) else {}
    )

    return {
        "completedTurns": len(turns),
        "plannedTurns": scenario.get("plannedTurns"),
        "latency": latency,
        "warnings": warnings,
        "ttsErrorTurns": tts_error_turns,
        "thresholds": record.get("thresholds"),
        # 1 往復目は scale-to-zero のコールドスタートを含み得る (ADR 0013) ので別掲
        "firstTurnSendToReplyVisibleMs": first_timings.get("sendToReplyVisibleMs"),
    }


def build_comment(
    envelope: dict,
    record_created_at: datetime,
    metrics: dict,
    now: datetime,
    eval_run_id: str | None,
    eval_run_url: str | None,
) -> str:
    """#127 へのコメント本文を組み立てる (純粋関数)。

    会話本文は含めない — 自由文を含めないことでフェンス破壊 (probe-record-comment.py
    が \\u0060 置換で守っている問題) をそもそも持ち込まない。
    """
    record = envelope.get("record") or {}
    env = (
        record.get("environment") if isinstance(record.get("environment"), dict) else {}
    )
    probe_run_id = envelope.get("runId")
    probe_run_url = env.get("runUrl")

    out = {
        "kind": OUTPUT_KIND,
        "schemaVersion": 1,
        "evaluatedAt": now.isoformat().replace("+00:00", "Z"),
        "probeId": envelope.get("probeId"),
        "probeRunId": probe_run_id,
        "scenarioId": envelope.get("scenarioId"),
        "recordCommentCreatedAt": record_created_at.isoformat().replace("+00:00", "Z"),
        "metrics": metrics,
        "evalRunId": eval_run_id,
        "evalRunUrl": eval_run_url,
    }

    visible = metrics["latency"]["sendToReplyVisibleMs"]
    run_line = (
        f"[run {probe_run_id}]({probe_run_url})"
        if probe_run_url
        else f"run {probe_run_id}"
    )
    return "\n".join(
        [
            "## UX 機械計測 (ux-eval-mech)",
            "",
            f"- 対象プローブ: {run_line} / scenario `{envelope.get('scenarioId', '?')}` / "
            f"完了 {metrics['completedTurns']}/{metrics.get('plannedTurns') or '?'} 往復",
            f"- send→表示: avg {visible['avgMs'] if visible['avgMs'] is not None else '欠測'} ms / "
            f"max {visible['maxMs'] if visible['maxMs'] is not None else '欠測'} ms",
            f"- warnings: latency {metrics['warnings']['latency']} / "
            f"functional {metrics['warnings']['functional']} / "
            f"TTS エラー往復 {metrics['ttsErrorTurns']}",
            "",
            "機械で計算できる値だけを積んでいます。LLM 採点 (kind: `ux-judge-score`) は"
            " PM セッションの日次 tick が別コメントで積みます (ADR 0037)。",
            "",
            "```json",
            json.dumps(out, ensure_ascii=False),
            "```",
        ]
    )


def run(comments_path: Path, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    try:
        comments = json.loads(comments_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log(f"コメント一覧を読めません: {comments_path}: {exc}")
        return EXIT_UNEXPECTED
    if not isinstance(comments, list):
        log(f"コメント一覧が配列ではありません: {comments_path}")
        return EXIT_UNEXPECTED

    try:
        max_age_hours = float(
            os.environ.get("UX_EVAL_MAX_AGE_HOURS") or DEFAULT_MAX_AGE_HOURS
        )
    except ValueError:
        log("UX_EVAL_MAX_AGE_HOURS が数値ではありません")
        return EXIT_UNEXPECTED

    found = latest_record(comments)
    if found is None:
        log(f"kind={RECORD_KIND} のコメントが 1 件もありません。")
        log("  → golden-path-monitor の投稿ステップが動いていない可能性があります。")
        return EXIT_NO_FRESH_RECORD

    envelope, created_at = found
    age_hours = (now - created_at).total_seconds() / 3600
    if not is_fresh(created_at, now, max_age_hours):
        log(
            f"最新の記録が古すぎます: {created_at.isoformat()} "
            f"({age_hours:.1f} 時間前 > 期待 {max_age_hours:g} 時間以内)。"
        )
        log("  → プローブ (golden-path-monitor) か記録の投稿が止まっています。")
        log("     古い記録を今日の計測として積まないため、この run は赤にします。")
        return EXIT_NO_FRESH_RECORD

    metrics = measure(envelope["record"])
    log(
        f"記録: probeId={envelope.get('probeId', '?')} "
        f"scenario={envelope.get('scenarioId', '?')} "
        f"created={created_at.isoformat()} ({age_hours:.1f} 時間前) "
        f"turns={metrics['completedTurns']}/{metrics.get('plannedTurns') or '?'}"
    )

    run_id = os.environ.get("GITHUB_RUN_ID")
    server = os.environ.get("GITHUB_SERVER_URL", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_url = (
        f"{server}/{repo}/actions/runs/{run_id}" if run_id and server and repo else None
    )

    print(build_comment(envelope, created_at, metrics, now, run_id, run_url))
    return EXIT_OK


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        log(f"使い方: {Path(argv[0]).name} <comments.json>")
        return EXIT_UNEXPECTED
    return run(Path(argv[1]))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
