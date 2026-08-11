#!/usr/bin/env python3
"""UX プローブ記録から機械計測を抽出し、データブランチへの追記 payload を作る (ADR 0037 / 0040)。

なぜあるか:
    ux-judge Routine (claude.ai) は実行履歴がリポジトリに残らず、一度も投稿しない
    まま沈黙していた (ADR 0035)。LLM を Actions で走らせる経路は追加課金 (API キー)
    か長期クレデンシャル (OAuth トークン) を要するため全部却下 (ADR 0008/0009/0035 D5)。
    そこで**機械で計算できる計測だけ**をこの script に切り出して Actions (ux-eval.yml)
    で毎朝走らせ、LLM 採点 (ux-judge-score) は PM セッションの日次 tick が subagent で
    行う — 分担の判断は ADR 0037。蓄積先は Issue コメントから git データブランチ
    `data/ux-observations` へ移した — 判断は ADR 0041 (PO 裁定 2026-08-11 / #197)。

責務 (LLM 判断は含めない):
    - データブランチ checkout の probes/*.jsonl から最新の ux-probe-record を選ぶ
    - 鮮度を確かめる (既定 26 時間)。**古い記録を「今日の計測」として積まない** —
      ここを黙って通すと、プローブが止まっているのにトレンドが伸び続ける
    - 記録 JSON から区間レイテンシ統計 / 往復数 / 警告・エラー数を計算する
    - evals/*.jsonl への追記 payload (kind: "ux-eval-mech") を stdout に 1 行で出す
      (追記そのものは append-observation.sh の責務)

蓄積の形 (JSONL / 封筒) の真実は cicd/scripts/ux-data/append.py と
cicd/scripts/ux-probe/probe-record-comment.py (envelope)。test_ux_eval.py が
envelope との往復を実 module で検証している (片方だけ直すと落ちる)。

使い方:
    ux_eval.py <data_dir>
      data_dir = データブランチ (data/ux-observations) の checkout。
                 probes/*.jsonl を読み、evals/*.jsonl で評価済み runId を確認して
                 再評価を拒否する — 鮮度 26h は前日 07:00 の記録 (約 25h 前) を通して
                 しまうため、時刻だけでは「今朝の記録が欠けた朝」を検出できない
                 (Codex レビュー指摘 / PR #224)
      環境変数: UX_EVAL_MAX_AGE_HOURS (既定 26) / GITHUB_RUN_ID / GITHUB_SERVER_URL /
                GITHUB_REPOSITORY (計測 run へのリンク用・無くても動く)

診断は stderr、成果物 (payload JSON 1 行) だけを stdout に出す (PR #88 で踏んだ実例と同じ規律)。

終了コード:
    0 = 計測できた (payload を stdout に出力)
    3 = 鮮度内の記録が無い (記録ゼロ / 全部古い) — 呼び出し側は run を赤にする
    4 = 最新の記録は評価済み (今朝の新しい記録が来ていない) — 同じく赤にする
    1 = 前提不足・想定外 (ディレクトリが無い / JSON が壊れている)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RECORD_KIND = "ux-probe-record"
OUTPUT_KIND = "ux-eval-mech"
DEFAULT_MAX_AGE_HOURS = 26.0

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
EXIT_ALREADY_EVALUATED = 4


def log(message: str) -> None:
    print(message, file=sys.stderr)


def parse_ts(ts: str | None) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def read_observations(subdir: Path) -> list[dict]:
    """月別 JSONL から観測を読む。壊れた行は読み飛ばす (stderr に位置を出す)。

    黙って全滅させない — 1 行の破損で「記録なし」に見えると、破損と停止の
    区別がつかないまま採点が止まる。
    """
    observations: list[dict] = []
    if not subdir.is_dir():
        return observations
    for path in sorted(subdir.glob("*.jsonl")):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                log(f"壊れた行を読み飛ばします: {path}:{lineno}")
                continue
            if isinstance(obj, dict):
                observations.append(obj)
    return observations


def latest_record(observations: list[dict]) -> tuple[dict, datetime] | None:
    """観測一覧から最新のプローブ記録 (封筒, recordedAt) を返す。無ければ None。

    recordedAt で選ぶ (ファイル内の並び順に依存しない — 移行データと実運用の
    追記が混ざっても最新を取り違えないため)。
    """
    best: tuple[dict, datetime] | None = None
    for obs in observations:
        if obs.get("kind") != RECORD_KIND or not isinstance(obs.get("record"), dict):
            continue
        recorded = parse_ts(obs.get("recordedAt"))
        if recorded is None:
            log(f"recordedAt の無い記録を読み飛ばします: probeId={obs.get('probeId')!r}")
            continue
        if best is None or recorded > best[1]:
            best = (obs, recorded)
    return best


def is_fresh(recorded_at: datetime, now: datetime, max_age_hours: float) -> bool:
    """記録がまだ「今日の計測」として使える鮮度かを判定する (純粋関数)。"""
    return now - recorded_at <= timedelta(hours=max_age_hours)


def evaluated_probe_run_ids(observations: list[dict]) -> set[str]:
    """evals の観測から評価済みプローブの runId を集める (純粋関数)。

    自分 (kind: ux-eval-mech) の出力だけを見る — ux-judge-score 等の他 kind は
    「機械計測が積まれた」ことを意味しないので数えない。
    """
    ids: set[str] = set()
    for obs in observations:
        if not isinstance(obs, dict) or obs.get("kind") != OUTPUT_KIND:
            continue
        rid = obs.get("probeRunId")
        if isinstance(rid, str) and rid:
            ids.add(rid)
    return ids


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


def build_payload(
    envelope: dict,
    probe_recorded_at: datetime,
    metrics: dict,
    now: datetime,
    eval_run_id: str | None,
    eval_run_url: str | None,
) -> dict:
    """evals/*.jsonl への追記 payload を組み立てる (純粋関数)。

    会話本文は含めない — 自由文を持ち込まないことで肥大化を避け、トレンド描画
    (status-page) が読むのは数値だけ、という分担を保つ。
    """
    now_iso = now.isoformat().replace("+00:00", "Z")
    return {
        "kind": OUTPUT_KIND,
        "schemaVersion": 2,  # 2 = データブランチ蓄積 (ADR 0041)。1 は #127 コメント時代
        "recordedAt": now_iso,
        "evaluatedAt": now_iso,
        "probeId": envelope.get("probeId"),
        "probeRunId": envelope.get("runId"),
        "scenarioId": envelope.get("scenarioId"),
        "probeRecordedAt": probe_recorded_at.isoformat().replace("+00:00", "Z"),
        "metrics": metrics,
        "evalRunId": eval_run_id,
        "evalRunUrl": eval_run_url,
    }


def run(data_dir: Path, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    if not data_dir.is_dir():
        log(f"データディレクトリがありません: {data_dir}")
        log("  → データブランチ (data/ux-observations) の checkout を渡してください。")
        return EXIT_UNEXPECTED

    try:
        max_age_hours = float(
            os.environ.get("UX_EVAL_MAX_AGE_HOURS") or DEFAULT_MAX_AGE_HOURS
        )
    except ValueError:
        log("UX_EVAL_MAX_AGE_HOURS が数値ではありません")
        return EXIT_UNEXPECTED

    probes = read_observations(data_dir / "probes")
    evals = read_observations(data_dir / "evals")

    found = latest_record(probes)
    if found is None:
        log(f"kind={RECORD_KIND} の観測が 1 件もありません。")
        log("  → golden-path-monitor の追記ステップが動いていない可能性があります。")
        return EXIT_NO_FRESH_RECORD

    envelope, recorded_at = found
    age_hours = (now - recorded_at).total_seconds() / 3600
    if not is_fresh(recorded_at, now, max_age_hours):
        log(
            f"最新の記録が古すぎます: {recorded_at.isoformat()} "
            f"({age_hours:.1f} 時間前 > 期待 {max_age_hours:g} 時間以内)。"
        )
        log("  → プローブ (golden-path-monitor) か記録の追記が止まっています。")
        log("     古い記録を今日の計測として積まないため、この run は赤にします。")
        return EXIT_NO_FRESH_RECORD

    # 鮮度 26h は前日 07:00 の記録 (約 25h 前) も通す。時刻だけで「今朝の記録が
    # 欠けた朝」は検出できないので、評価済み runId の再評価をここで拒否する。
    evaluated = evaluated_probe_run_ids(evals)
    probe_run_id = envelope.get("runId")
    if isinstance(probe_run_id, str) and probe_run_id in evaluated:
        log(
            f"最新の記録 (runId={probe_run_id}) は評価済みです。"
            " 今朝の新しい記録が来ていません。"
        )
        log("  → golden-path-monitor か記録の追記が止まっています。")
        log("     同じ記録を重複して積まないため、この run は赤にします。")
        return EXIT_ALREADY_EVALUATED
    if not (isinstance(probe_run_id, str) and probe_run_id):
        log("記録に runId が無いため重複判定はできません — そのまま計測します。")

    metrics = measure(envelope["record"])
    log(
        f"記録: probeId={envelope.get('probeId', '?')} "
        f"scenario={envelope.get('scenarioId', '?')} "
        f"recorded={recorded_at.isoformat()} ({age_hours:.1f} 時間前) "
        f"turns={metrics['completedTurns']}/{metrics.get('plannedTurns') or '?'}"
    )

    run_id = os.environ.get("GITHUB_RUN_ID")
    server = os.environ.get("GITHUB_SERVER_URL", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_url = (
        f"{server}/{repo}/actions/runs/{run_id}" if run_id and server and repo else None
    )

    payload = build_payload(envelope, recorded_at, metrics, now, run_id, run_url)
    print(json.dumps(payload, ensure_ascii=False))
    return EXIT_OK


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        log(f"使い方: {Path(argv[0]).name} <data_dir>")
        return EXIT_UNEXPECTED
    return run(Path(argv[1]))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
