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
    - データブランチ checkout の probes/*.jsonl から **シナリオごとに**最新の
      ux-probe-record を選ぶ (#435 で台本が 2 本になった。1 run = 1 記録ではない)
    - 鮮度を確かめる (既定 26 時間)。**古い記録を「今日の計測」として積まない** —
      ここを黙って通すと、プローブが止まっているのにトレンドが伸び続ける
    - 記録 JSON から区間レイテンシ統計 / 往復数 / 警告・エラー数を計算する
    - evals/*.jsonl への追記 payload (kind: "ux-eval-mech") を stdout に
      **シナリオごとに 1 行ずつ** (JSONL) 出す (追記そのものは append-observation.sh の責務)

シナリオが複数あるときの規律:
    - 重複判定は (probeRunId, scenarioId) の組で行う。**同じ run から複数シナリオの記録が
      来る**ので、runId だけで見ると 1 本目を積んだ時点で 2 本目が「評価済み」に化ける
    - 一部のシナリオだけ記録が欠けた朝も、**取れたものは必ず stdout に出す** (全体を
      落とすと取れていた計測まで捨てる)。そのうえで、**前回まで積めていたシナリオ集合
      (直近 window 内の evals の実績) より減っていれば exit 5 で赤にする** (#443 judge B —
      部分欠落を緑で通すと、封筒化失敗の経路 [monitor は warning + continue で緑] で
      1 シナリオだけ蓄積が止まっても、どこも赤くならない)。呼び出し側 (ux-eval.yml) は
      payload を追記してから run を落とす。「何を積んで何を飛ばしたか」は stderr に出す
    - 鮮度内の記録が 1 件も無い / 全部評価済み のときは従来どおり exit 3 / 4 の赤

蓄積の形 (JSONL / 封筒) の真実は cicd/scripts/ux-data/append.py と
cicd/scripts/ux-probe/probe-record-comment.py (envelope)。test_ux_eval.py が
envelope との往復を実 module で検証している (片方だけ直すと落ちる)。

使い方:
    ux_eval.py <data_dir>
      本体。data_dir = データブランチ (data/ux-observations) の checkout。
    ux_eval.py --step-outcome <rc>
      本体の終了コード <rc> を呼び出し側 (ux-eval.yml の eval step) の振る舞いへ
      変換する: annotation (::error::) を stdout に、「追記後に赤」へ倒すフラグを
      $GITHUB_OUTPUT に書き、step の終了コードで exit する。判定は
      eval_step_directives (テスト済みの純関数) が持つ — YAML の if 連鎖に埋めると
      出力名や条件式の退行を定期 workflow の実行までテストで検出できない
      (#440 / security-sweep の sast_annotation と同じ理由。Codex P1 PR #462)

    data_dir の読み方: probes/*.jsonl を読み、evals/*.jsonl で評価済み
                 (runId, scenarioId) を確認して再評価を拒否する — 鮮度 26h は
                 前日 07:00 の記録 (約 25h 前) を通してしまうため、時刻だけでは
                 「今朝の記録が欠けた朝」を検出できない (Codex レビュー指摘 / PR #224)
      環境変数: UX_EVAL_MAX_AGE_HOURS (既定 26) /
                UX_EVAL_EXPECTED_WINDOW_HOURS (既定 168 = 7 日 — 「前回まで積めていた
                シナリオ集合」を evals の実績から作る窓) /
                GITHUB_RUN_ID / GITHUB_SERVER_URL /
                GITHUB_REPOSITORY (計測 run へのリンク用・無くても動く)

診断は stderr、成果物 (payload JSON) だけを stdout に出す (PR #88 で踏んだ実例と同じ規律)。
**stdout は 1 行とは限らない** — シナリオごとに 1 行の JSONL。呼び出し側 (ux-eval.yml) は
1 行ずつ append-observation.sh に渡すこと (1 行目だけ読むと片方の計測が静かに落ちる)。

終了コード:
    0 = 前回までの全シナリオを計測できた (payload を stdout に JSONL で出力)
    5 = 計測はできた (payload は stdout に出力済み) が、前回まで積めていたシナリオ集合
        より減っている — 呼び出し側は **payload を追記したうえで** run を赤にする (#443)
    6 = 計測はできた (payload は stdout に出力済み) が、turns 0 件の空記録がある —
        呼び出し側は **payload を追記したうえで** run を赤にする (#401)。行は積む
        (時系列の欠落も情報) が、「計測が成功した」とは言わない。採点側
        (inspect-probe-artifact.py の exit 4 = 採点する材料がない) と判定を揃える
    3 = 鮮度内の記録が 1 件も無い (記録ゼロ / 全部古い) — 呼び出し側は run を赤にする
    4 = 鮮度内の記録はあるが全部評価済み (今朝の新しい記録が来ていない) — 同じく赤にする
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
# 「前回まで積めていたシナリオ集合」を evals の実績から作る窓 (#443)。
# 短すぎると 1 回の欠落で要求集合から外れて検知が消え、長すぎると意図した退役の赤が長引く
DEFAULT_EXPECTED_WINDOW_HOURS = 168.0

# scenarioId を持たない記録 (Issue コメント時代の移行データ) の置き場。
# 捨てると過去データが黙って計測対象から外れるので、1 つのシナリオとして扱う
UNKNOWN_SCENARIO = "(scenarioId なし)"

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
EXIT_SCENARIO_SET_SHRUNK = 5
EXIT_EMPTY_RECORD = 6


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


def latest_records_by_scenario(
    observations: list[dict],
) -> dict[str, tuple[dict, datetime]]:
    """シナリオごとの最新プローブ記録 (封筒, recordedAt) を返す (純粋関数)。

    recordedAt で選ぶ (ファイル内の並び順に依存しない — 移行データと実運用の
    追記が混ざっても最新を取り違えないため)。

    **シナリオごとに分ける理由** (#435): 台本が 2 本になり、同じ朝に別々の記録が積まれる。
    全体の最新 1 件だけを見ると、**もう片方のシナリオは一度も計測されない**まま
    トレンドが「1 本ぶんしか無い」ことに誰も気づけない。
    scenarioId が無い記録 (旧形式) は `UNKNOWN_SCENARIO` にまとめる — 捨てない。
    """
    best: dict[str, tuple[dict, datetime]] = {}
    for obs in observations:
        if obs.get("kind") != RECORD_KIND or not isinstance(obs.get("record"), dict):
            continue
        recorded = parse_ts(obs.get("recordedAt"))
        if recorded is None:
            log(f"recordedAt の無い記録を読み飛ばします: probeId={obs.get('probeId')!r}")
            continue
        scenario_id = obs.get("scenarioId")
        key = scenario_id if isinstance(scenario_id, str) and scenario_id else UNKNOWN_SCENARIO
        current = best.get(key)
        if current is None or recorded > current[1]:
            best[key] = (obs, recorded)
    return best


def is_fresh(recorded_at: datetime, now: datetime, max_age_hours: float) -> bool:
    """記録がまだ「今日の計測」として使える鮮度かを判定する (純粋関数)。"""
    return now - recorded_at <= timedelta(hours=max_age_hours)


def evaluated_probe_keys(observations: list[dict]) -> set[tuple[str, str]]:
    """evals の観測から評価済みプローブの (probeRunId, scenarioId) を集める (純粋関数)。

    自分 (kind: ux-eval-mech) の出力だけを見る — ux-judge-score 等の他 kind は
    「機械計測が積まれた」ことを意味しないので数えない。

    **runId だけで見ない** (#435): 同じ run から複数シナリオの記録が来るので、
    runId 単独だと 1 本目を積んだ時点で 2 本目が「評価済み」に化け、
    **そのシナリオの計測が永久に積まれない**まま run は緑のままになる。
    """
    keys: set[tuple[str, str]] = set()
    for obs in observations:
        if not isinstance(obs, dict) or obs.get("kind") != OUTPUT_KIND:
            continue
        rid = obs.get("probeRunId")
        if not (isinstance(rid, str) and rid):
            continue
        sid = obs.get("scenarioId")
        keys.add((rid, sid if isinstance(sid, str) and sid else UNKNOWN_SCENARIO))
    return keys


def expected_scenario_ids(
    observations: list[dict], now: datetime, window_hours: float
) -> set[str]:
    """「前回まで積めていたシナリオ集合」を evals の実績から作る (純粋関数 / #443)。

    直近 window_hours 内に機械計測 (ux-eval-mech) が積まれた scenarioId の集合。
    今朝の計測がこの集合より減っていたら EXIT_SCENARIO_SET_SHRUNK で赤にする —
    部分欠落を緑で通すと、封筒化失敗の経路 (monitor は warning + continue で緑) で
    1 シナリオ (例: U7 の観測器) だけ蓄積が止まっても、どこも赤くならない。

    - scenarioId の無い記録 (#435 以前の計測 / 移行データ) は数えない — どのシナリオの
      ものか判別できないものに「積め」とは要求できず、数えると #435 導入直後の朝が
      恒常的な偽陽性の赤になる
    - 意図してシナリオを退役させた直後も赤くなる (見えない断絶より見える赤を選ぶ)。
      window を過ぎると要求集合から外れて緑に戻る
    """
    ids: set[str] = set()
    for obs in observations:
        if not isinstance(obs, dict) or obs.get("kind") != OUTPUT_KIND:
            continue
        recorded = parse_ts(obs.get("recordedAt"))
        if recorded is None or now - recorded > timedelta(hours=window_hours):
            continue
        sid = obs.get("scenarioId")
        if isinstance(sid, str) and sid:
            ids.add(sid)
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
    try:
        expected_window_hours = float(
            os.environ.get("UX_EVAL_EXPECTED_WINDOW_HOURS")
            or DEFAULT_EXPECTED_WINDOW_HOURS
        )
    except ValueError:
        log("UX_EVAL_EXPECTED_WINDOW_HOURS が数値ではありません")
        return EXIT_UNEXPECTED

    probes = read_observations(data_dir / "probes")
    evals = read_observations(data_dir / "evals")

    found = latest_records_by_scenario(probes)
    if not found:
        log(f"kind={RECORD_KIND} の観測が 1 件もありません。")
        log("  → golden-path-monitor の追記ステップが動いていない可能性があります。")
        return EXIT_NO_FRESH_RECORD

    # 鮮度 26h は前日 07:00 の記録 (約 25h 前) も通す。時刻だけで「今朝の記録が
    # 欠けた朝」は検出できないので、評価済み (runId, scenarioId) の再評価をここで拒否する。
    evaluated = evaluated_probe_keys(evals)

    run_id = os.environ.get("GITHUB_RUN_ID")
    server = os.environ.get("GITHUB_SERVER_URL", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_url = (
        f"{server}/{repo}/actions/runs/{run_id}" if run_id and server and repo else None
    )

    payloads: list[dict] = []
    measured: list[str] = []
    stale: list[str] = []
    already: list[str] = []
    empty: list[str] = []

    for scenario_id, (envelope, recorded_at) in sorted(found.items()):
        age_hours = (now - recorded_at).total_seconds() / 3600
        if not is_fresh(recorded_at, now, max_age_hours):
            stale.append(scenario_id)
            log(
                f"[{scenario_id}] 最新の記録が古すぎます: {recorded_at.isoformat()} "
                f"({age_hours:.1f} 時間前 > 期待 {max_age_hours:g} 時間以内) — 積みません。"
            )
            continue

        probe_run_id = envelope.get("runId")
        if isinstance(probe_run_id, str) and probe_run_id:
            if (probe_run_id, scenario_id) in evaluated:
                already.append(scenario_id)
                log(
                    f"[{scenario_id}] 最新の記録 (runId={probe_run_id}) は評価済みです — "
                    "今朝の新しい記録が来ていません。"
                )
                continue
        else:
            log(
                f"[{scenario_id}] 記録に runId が無いため重複判定はできません — "
                "そのまま計測します。"
            )

        metrics = measure(envelope["record"])
        log(
            f"[{scenario_id}] 記録: probeId={envelope.get('probeId', '?')} "
            f"recorded={recorded_at.isoformat()} ({age_hours:.1f} 時間前) "
            f"turns={metrics['completedTurns']}/{metrics.get('plannedTurns') or '?'}"
        )
        payloads.append(
            build_payload(envelope, recorded_at, metrics, now, run_id, run_url)
        )
        measured.append(scenario_id)
        # 中身が空 (往復 0 件) の記録は「計測が成功した」とは言わない (#401)。
        # 記録は 1 往復ごとに書き出す設計 (ADR 0037) なので、プローブが送信前に
        # 自滅しても turns: [] の record が書かれ、鮮度チェックだけでは素通りする。
        # 行は積む (時系列の欠落も情報) が、run は赤にする — 採点側
        # (inspect-probe-artifact.py の exit 4 = 採点する材料がない) と判定を揃える。
        # 0 < completedTurns < plannedTurns の部分記録は正常な産物なので落とさない
        if metrics["completedTurns"] == 0:
            empty.append(scenario_id)
            log(
                f"[{scenario_id}] 記録に往復が 1 件もありません (turns 0 件) — "
                "行は積みますが、計測が成功したことにはしません。"
            )

    if not payloads:
        # 全シナリオが古い / 全シナリオが評価済み — 入力が止まっているので赤にする。
        # 「評価済み」が 1 件でもあれば、記録自体は届いているが今朝の更新が無い状態
        if already:
            log(
                f"鮮度内の記録はすべて評価済みです (シナリオ: {', '.join(already)})。"
            )
            log("  → golden-path-monitor か記録の追記が止まっています。")
            log("     同じ記録を重複して積まないため、この run は赤にします。")
            return EXIT_ALREADY_EVALUATED
        log(f"鮮度内のプローブ記録がありません (シナリオ: {', '.join(stale)})。")
        log("  → プローブ (golden-path-monitor) か記録の追記が止まっています。")
        log("     古い記録を今日の計測として積まないため、この run は赤にします。")
        return EXIT_NO_FRESH_RECORD

    skipped = stale + already
    if skipped:
        # 一部だけ積む朝は必ず名指しで残す。数だけ減って理由が残らないと、
        # 「シナリオが 1 本しか無かった日」と区別がつかなくなる
        log(
            f"注意: 計測したのは {len(payloads)} シナリオで、"
            f"{len(skipped)} シナリオ ({', '.join(skipped)}) は積んでいません "
            "(古い記録 / 評価済み)。そのシナリオのプローブ側を確認してください。"
        )

    for payload in payloads:
        print(json.dumps(payload, ensure_ascii=False))
    log(f"計測を {len(payloads)} 件出力しました。")

    # 「前回まで積めていた集合より減ったら赤」(#443 judge B)。
    # 「評価済み」で飛ばしたシナリオは covered に数える — 蓄積そのものは追いついている
    # (追記後の再実行や、1 本目だけ積んで落ちた朝の再 run を偽陽性の赤にしない)。
    # 逆に鮮度切れ (stale) は数えない — そのシナリオの新しい記録が来ていない状態そのもの
    covered = set(measured) | set(already)
    missing = sorted(
        expected_scenario_ids(evals, now, expected_window_hours) - covered
    )
    if empty:
        # 縮小 (exit 5) と同時に起きた朝も、空記録の存在は必ず stderr に残す —
        # 終了コードは 1 つしか返せないので、ここで黙るともう片方の異常が消える
        log(
            f"エラー: プローブが 1 往復も完了しないまま書き出した空記録があります "
            f"(シナリオ: {', '.join(empty)})。"
        )
        log("  → 行は追記してよいが、この run は赤にすること (exit 6)。")
        log("     プローブ側 (golden-path-monitor) の同朝 run を確認してください (#401)。")
    if missing:
        log(
            f"エラー: 前回まで積めていたシナリオのうち {', '.join(missing)} が"
            f"今朝は計測できていません (直近 {expected_window_hours:g} 時間の実績との比較)。"
        )
        log("  → 取れた計測は追記してよいが、この run は赤にすること (exit 5)。")
        log("     プローブ側 (golden-path-monitor) と封筒化・追記の経路を確認してください。")
        return EXIT_SCENARIO_SET_SHRUNK
    if empty:
        return EXIT_EMPTY_RECORD
    return EXIT_OK


def eval_step_directives(rc: int) -> dict:
    """本体の終了コードを、呼び出し側 (ux-eval.yml の eval step) の振る舞いへ変換する
    (純粋関数 / Codex P1 PR #462)。

    この変換を YAML の if 連鎖に埋めると、出力名 (`empty_record=true`) や条件式が
    退行しても定期 workflow を実行するまで検出できない (#440 / security-sweep の
    sast_annotation と同じ理由)。「exit 6 = payload を追記したうえで run を赤にする」
    の接続はここが持ち、test_ux_eval.py が固定する。

    返り値:
        annotation: step ログに出す ::error:: 行 ("" = 出さない)
        outputs:    $GITHUB_OUTPUT に書く行。追記 step の**後**に run を赤へ倒す
                    フラグ (ux-eval.yml の該当 step の if と名前を揃える)
        exit:       eval step の終了コード (0 = 追記へ進む)
    """
    if rc == EXIT_OK:
        return {"annotation": "", "outputs": [], "exit": 0}
    if rc == EXIT_NO_FRESH_RECORD:
        # 鮮度切れ / 記録なしは**この run の赤で表現する** (ADR 0037 D1)。
        # 黙って skip すると「プローブが止まったまま計測も静かに止まる」が再発する
        return {
            "annotation": (
                "::error::26 時間以内のプローブ記録がありません — "
                "golden-path-monitor か記録の追記が止まっています (詳細は上のログ)"
            ),
            "outputs": [],
            "exit": 1,
        }
    if rc == EXIT_ALREADY_EVALUATED:
        # 前日の記録しか無い朝 — 重複を積まず、入力が止まったことを赤で表現する
        return {
            "annotation": (
                "::error::鮮度内のプローブ記録はすべて評価済みです — "
                "今朝の新しい記録が来ていません (golden-path-monitor の停止か追記失敗。"
                "詳細は上のログ)"
            ),
            "outputs": [],
            "exit": 1,
        }
    if rc == EXIT_SCENARIO_SET_SHRUNK:
        # 前回まで積めていたシナリオの一部が今朝は計測できていない (#443)。
        # payload は出ているので取れた分は積み、追記の後の step で run を赤にする —
        # 「取れた計測を捨てない」と「減ったことを黙らせない」を両立させる
        return {
            "annotation": (
                "::error::前回まで積めていたシナリオの一部が今朝は計測できていません "
                "(詳細は上のログ) — 取れた分を追記したうえで run を赤にします"
            ),
            "outputs": ["scenario_set_shrunk=true"],
            "exit": 0,
        }
    if rc == EXIT_EMPTY_RECORD:
        # turns 0 件の空記録 (#401)。行は積む (時系列の欠落も情報) が、
        # 「計測が成功した」ことにはしない — 追記の後の step で run を赤にする
        return {
            "annotation": (
                "::error::turns 0 件の空記録があります — プローブが 1 往復も完了して"
                "いません (詳細は上のログ)。行を追記したうえで run を赤にします"
            ),
            "outputs": ["empty_record=true"],
            "exit": 0,
        }
    # 想定外 (1 = 前提不足 など) はそのまま即赤 — 解釈できない状態を緑で通さない
    return {"annotation": "", "outputs": [], "exit": rc}


def step_outcome(rc_text: str) -> int:
    """--step-outcome モード: 判定 (eval_step_directives) を実環境へ適用する。"""
    try:
        rc = int(rc_text)
    except ValueError:
        log(f"--step-outcome の引数が整数ではありません: {rc_text!r}")
        return EXIT_UNEXPECTED
    directives = eval_step_directives(rc)
    if directives["annotation"]:
        # annotation は stdout に出す — Actions が ::error:: を拾うのは step ログ
        print(directives["annotation"])
    if directives["outputs"]:
        output_path = os.environ.get("GITHUB_OUTPUT")
        if not output_path:
            # フラグを黙って落とすと「追記後に赤」の step が発火せず、
            # 空記録 (#401) や集合縮小 (#443) がまた緑で通る。書けないなら赤にする
            log(
                "GITHUB_OUTPUT が未設定のためフラグを書けません: "
                f"{directives['outputs']} — 「追記後に赤」が発火しないので、"
                "ここで赤にします。"
            )
            return EXIT_UNEXPECTED
        with open(output_path, "a", encoding="utf-8") as fh:
            for line in directives["outputs"]:
                fh.write(line + "\n")
    return directives["exit"]


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "--step-outcome":
        return step_outcome(argv[2])
    if len(argv) != 2:
        log(f"使い方: {Path(argv[0]).name} <data_dir> | --step-outcome <rc>")
        return EXIT_UNEXPECTED
    return run(Path(argv[1]))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
