"""[L1] UX 機械計測の抽出 (ADR 0037 / 蓄積は ADR 0040 のデータブランチ)。

無いと何が静かに通るか:
    - 古い記録が「今日の計測」として積まれ、プローブが止まっているのに
      トレンドが伸び続ける (鮮度チェックの穴)
    - 欠測 (null) が 0ms として平均に混ざり、レイテンシが実際より良く見える
    - 封筒の形 (probe-record-comment.py envelope) が変わったとき、こちらだけ
      古いまま「記録なし」と誤報する — round-trip テストが実 module で結合を検証する
    - JSONL の 1 行が壊れただけで「記録なし」に見え、破損と停止の区別がつかない
"""

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ux_eval import (
    EXIT_ALREADY_EVALUATED,
    EXIT_NO_FRESH_RECORD,
    EXIT_OK,
    build_payload,
    evaluated_probe_run_ids,
    is_fresh,
    latest_record,
    measure,
    read_observations,
    run,
)

NOW = datetime(2026, 8, 10, 0, 0, 0, tzinfo=timezone.utc)


def _record(turns: list[dict] | None = None) -> dict:
    """真実 (ux-probe.spec.ts の ProbeRecord / #162 実コメントで確認済み) に沿った最小記録。"""
    return {
        "schemaVersion": 1,
        "kind": "ux-probe-conversation",
        "probeId": "ux-probe-2026-08-09T22-37-09-200Z",
        "startedAt": "2026-08-09T22:37:09.200Z",
        "environment": {
            "appUrl": "https://app.example",
            "bffUrl": "https://bff.example",
            "gitSha": "0e81517",
            "runId": "31339682965",
            "runUrl": "https://github.com/yomote/mind-inbox/actions/runs/31339682965",
        },
        "scenario": {"id": "work-overwhelm-v1", "description": "x", "plannedTurns": 4},
        "thresholds": {"warnReplyVisibleMs": 10000, "warnTtsSynthMs": 8000},
        "openerText": "こんにちは",
        "turns": turns if turns is not None else [_turn(1, 2000), _turn(2, 4000)],
        "summary": {
            "completedTurns": 2,
            "avgSendToReplyVisibleMs": 3000,
            "maxSendToReplyVisibleMs": 4000,
            "warningCount": 0,
            "firstTurnIncludesColdStart": True,
        },
    }


def _turn(index: int, visible_ms: int, **overrides) -> dict:
    turn = {
        "index": index,
        "userText": "u",
        "assistantText": "a",
        "timings": {
            "sentAt": "2026-08-09T22:37:11.975Z",
            "sendToTrpcResponseMs": visible_ms - 300,
            "sendToReplyVisibleMs": visible_ms,
            "replyVisibleToTtsRequestMs": 10,
            "ttsRequestToResponseMs": 200,
            "sendToTtsResponseMs": visible_ms + 210,
        },
        "ttsStatus": 200,
        "warnings": [],
    }
    turn.update(overrides)
    return turn


def _envelope(record: dict, recorded_at: str) -> dict:
    return {
        "kind": "ux-probe-record",
        "runId": record["environment"]["runId"],
        "scenarioId": record["scenario"]["id"],
        "plannedTurns": record["scenario"]["plannedTurns"],
        "completedTurns": len(record["turns"]),
        "probeId": record["probeId"],
        "recordedAt": recorded_at,
        "record": record,
    }


def _data_dir(
    tmp_path: Path,
    probes: list[dict] | None = None,
    evals: list[dict] | None = None,
    probe_raw_lines: list[str] | None = None,
) -> Path:
    data = tmp_path / "data"
    lines = [json.dumps(o, ensure_ascii=False) for o in probes or []]
    lines += probe_raw_lines or []
    if lines:
        (data / "probes").mkdir(parents=True, exist_ok=True)
        (data / "probes" / "2026-08.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    if evals is not None:
        (data / "evals").mkdir(parents=True, exist_ok=True)
        (data / "evals" / "2026-08.jsonl").write_text(
            "".join(json.dumps(o, ensure_ascii=False) + "\n" for o in evals),
            encoding="utf-8",
        )
    data.mkdir(parents=True, exist_ok=True)
    return data


def _load_probe_record_comment_module():
    """封筒の真実 (cicd/scripts/ux-probe/probe-record-comment.py) を実 module として読む。"""
    path = (
        Path(__file__).resolve().parent.parent / "ux-probe" / "probe-record-comment.py"
    )
    spec = importlib.util.spec_from_file_location("probe_record_comment", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["probe_record_comment"] = module
    spec.loader.exec_module(module)
    return module


def test_l1_封筒の真実とのラウンドトリップ(tmp_path, capsys) -> None:
    """probe-record-comment.py envelope の実出力を、こちらが最新記録として読めること。

    片方だけ直して封筒の形がずれたとき、この結合テストだけが気づける。
    バッククォート入りの応答 (Issue コメント時代はフェンス破壊対策が要ったケース) を
    含めて、record が変質しないことまで確かめる。
    """
    module = _load_probe_record_comment_module()
    record = _record(
        turns=[_turn(1, 2000, assistantText="コード例は `let x = 1` です")]
    )
    probe_json = tmp_path / "probe.json"
    probe_json.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    assert module.envelope_line(probe_json, "999", now=NOW) == 0
    line = capsys.readouterr().out.strip()
    assert "\n" not in line  # JSONL に 1 行で入る形

    data = tmp_path / "data"
    (data / "probes").mkdir(parents=True)
    (data / "probes" / "2026-08.jsonl").write_text(line + "\n", encoding="utf-8")

    found = latest_record(read_observations(data / "probes"))
    assert found is not None
    envelope, recorded_at = found
    assert recorded_at == NOW
    assert (
        envelope["record"]["turns"][0]["assistantText"] == "コード例は `let x = 1` です"
    )


def test_l1_最新の記録をrecordedAtで選ぶ_記録以外や壊れた行は無視する(tmp_path) -> None:
    old = _envelope(_record(), "2026-08-08T22:00:00Z")
    new = _envelope(_record(), "2026-08-09T22:00:00Z")
    judge = {"kind": "ux-judge-score", "recordedAt": "2026-08-09T23:00:00Z"}
    # 並び順に依存しないこと (新しい方を前に置いても正しく選ぶ) + 壊れた行で全滅しないこと
    data = _data_dir(
        tmp_path, probes=[new, judge, old], probe_raw_lines=["{壊れた行"]
    )
    found = latest_record(read_observations(data / "probes"))
    assert found is not None
    assert found[1].isoformat() == "2026-08-09T22:00:00+00:00"


def test_l1_鮮度判定() -> None:
    fresh = datetime(2026, 8, 9, 22, 37, 0, tzinfo=timezone.utc)  # 1.4 時間前
    stale = datetime(2026, 8, 8, 21, 0, 0, tzinfo=timezone.utc)  # 27 時間前
    assert is_fresh(fresh, NOW, 26)
    assert not is_fresh(stale, NOW, 26)


def test_l1_欠測を0msとして平均に混ぜない() -> None:
    """TTS 未観測 (null) の往復があるとき、avg が実際より良く見えてはいけない。"""
    turns = [_turn(1, 2000), _turn(2, 4000)]
    turns[1]["timings"]["ttsRequestToResponseMs"] = None
    turns[1]["timings"]["sendToTtsResponseMs"] = None
    metrics = measure(_record(turns=turns))
    tts = metrics["latency"]["ttsRequestToResponseMs"]
    assert tts["samples"] == 1
    assert tts["missing"] == 1
    assert tts["avgMs"] == 200  # null が 0 として混ざると 100 になってしまう
    visible = metrics["latency"]["sendToReplyVisibleMs"]
    assert visible == {
        "samples": 2,
        "missing": 0,
        "minMs": 2000,
        "avgMs": 3000,
        "maxMs": 4000,
    }


def test_l1_警告分類とTTSエラーを数える() -> None:
    turns = [
        _turn(1, 12000, warnings=[{"category": "latency", "message": "x"}]),
        _turn(
            2,
            2000,
            ttsStatus=503,
            warnings=[
                {"category": "functional", "message": "y"},
                {"category": "novel-category", "message": "z"},
            ],
        ),
    ]
    metrics = measure(_record(turns=turns))
    # 未知カテゴリを latency/functional に丸めない (rubric U6 の数えを狂わせない)
    assert metrics["warnings"] == {"latency": 1, "functional": 1, "other": 1}
    assert metrics["ttsErrorTurns"] == 1
    assert metrics["completedTurns"] == 2
    assert metrics["plannedTurns"] == 4


def test_l1_turns0件でも計測は落ちない() -> None:
    """壊れた朝の記録 (turns 0 件) も「0 件だった」というデータとして積める。"""
    metrics = measure(_record(turns=[]))
    assert metrics["completedTurns"] == 0
    assert metrics["latency"]["sendToReplyVisibleMs"]["samples"] == 0
    assert metrics["firstTurnSendToReplyVisibleMs"] is None


def test_l1_payloadは会話本文を含まず追記の必須キーを持つ() -> None:
    record = _record()
    envelope = _envelope(record, "2026-08-09T22:37:35Z")
    created = datetime(2026, 8, 9, 22, 37, 35, tzinfo=timezone.utc)
    payload = build_payload(
        envelope, created, measure(record), NOW, "123", "https://x/123"
    )

    assert payload["kind"] == "ux-eval-mech"
    assert payload["probeRunId"] == "31339682965"
    assert payload["probeRecordedAt"] == "2026-08-09T22:37:35Z"
    # recordedAt は append.py (ADR 0040) の必須キー — 無いと追記が exit 1 で落ちる
    assert payload["recordedAt"] == NOW.isoformat().replace("+00:00", "Z")
    assert payload["metrics"]["completedTurns"] == 2
    # 会話本文 (自由文) を持ち込まない — 肥大化とトレンド描画の混入を防ぐ
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "userText" not in dumped
    assert "assistantText" not in dumped


def test_l1_cli_鮮度切れは赤_鮮度内はpayloadを1行で出す(tmp_path, capsys) -> None:
    stale = _envelope(_record(), "2026-08-07T22:00:00Z")
    data = _data_dir(tmp_path, probes=[stale])
    assert run(data, now=NOW) == EXIT_NO_FRESH_RECORD

    fresh = _envelope(_record(), "2026-08-09T22:37:35Z")
    data = _data_dir(tmp_path / "b", probes=[stale, fresh])
    capsys.readouterr()
    assert run(data, now=NOW) == EXIT_OK
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["kind"] == "ux-eval-mech"


def test_l1_cli_記録ゼロは赤(tmp_path) -> None:
    data = _data_dir(
        tmp_path, evals=[{"kind": "ux-eval-mech", "probeRunId": "1"}]
    )
    assert run(data, now=NOW) == EXIT_NO_FRESH_RECORD


def test_l1_評価済みの記録は再評価せず赤にする(tmp_path, capsys) -> None:
    """前日の記録が鮮度 26h 内に残る朝 (今朝のプローブ欠落) の検出。

    無いと何が静かに通るか:
        golden-path-monitor (07:00 JST) の記録は翌朝の ux-eval (08:20 JST) 時点で
        約 25 時間前 — 鮮度 26h を通る。今朝の記録が欠けても前日分で緑になり、
        同じ計測が重複して積まれ、入力停止の検出が 1 日遅れる (Codex P1)。
    """
    fresh = _envelope(_record(), "2026-08-09T22:37:35Z")
    data = _data_dir(tmp_path, probes=[fresh])

    # 前回 run の出力そのもの (build_payload) を評価済みとして evals に置く —
    # 出力形式と重複判定の結合を実物で検証する (どちらか片方だけ直すと落ちる)
    capsys.readouterr()
    assert run(data, now=NOW) == EXIT_OK
    previous = json.loads(capsys.readouterr().out.strip())
    (data / "evals").mkdir(parents=True, exist_ok=True)
    (data / "evals" / "2026-08.jsonl").write_text(
        json.dumps(previous, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    assert run(data, now=NOW) == EXIT_ALREADY_EVALUATED


def test_l1_未評価の記録はevalsがあっても計測する(tmp_path, capsys) -> None:
    fresh = _envelope(_record(), "2026-08-09T22:37:35Z")
    # 別 runId の評価済みと、LLM 採点 (別 kind) — どちらも今回の記録の評価には数えない
    data = _data_dir(
        tmp_path,
        probes=[fresh],
        evals=[
            {"kind": "ux-eval-mech", "probeRunId": "99999999999"},
            {"kind": "ux-judge-score", "probeRunId": _record()["environment"]["runId"]},
        ],
    )
    capsys.readouterr()
    assert run(data, now=NOW) == EXIT_OK
    assert "ux-eval-mech" in capsys.readouterr().out


def test_l1_評価済みidの抽出はkindで絞る() -> None:
    ids = evaluated_probe_run_ids(
        [
            {"kind": "ux-eval-mech", "probeRunId": "111"},
            {"kind": "ux-judge-score", "probeRunId": "222"},
            {"kind": "ux-eval-mech", "probeRunId": None},
            "not-a-dict",
        ]
    )
    assert ids == {"111"}
