"""[L1] UX 機械計測の抽出 (ADR 0037)。

無いと何が静かに通るか:
    - 古い記録が「今日の計測」として積まれ、プローブが止まっているのに
      トレンドが伸び続ける (鮮度チェックの穴)
    - 欠測 (null) が 0ms として平均に混ざり、レイテンシが実際より良く見える
    - 封筒の形 (probe-record-comment.py) が変わったとき、こちらだけ古いまま
      「記録なし」と誤報する — round-trip テストが実 module で結合を検証する
"""

import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from ux_eval import (
    EXIT_NO_FRESH_RECORD,
    EXIT_OK,
    build_comment,
    is_fresh,
    latest_record,
    measure,
    parse_probe_comment,
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


def _envelope_comment(record: dict, created_at: str) -> dict:
    envelope = {
        "kind": "ux-probe-record",
        "runId": record["environment"]["runId"],
        "scenarioId": record["scenario"]["id"],
        "plannedTurns": record["scenario"]["plannedTurns"],
        "completedTurns": len(record["turns"]),
        "probeId": record["probeId"],
        "record": record,
    }
    body = "\n".join(["## UX プローブ記録", "", "```json", json.dumps(envelope), "```"])
    return {"body": body, "created_at": created_at}


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


def test_l1_封筒の真実とのラウンドトリップ(tmp_path, capsys, monkeypatch) -> None:
    """probe-record-comment.py format の実出力を、こちらの parse が読めること。

    片方だけ直して封筒の形がずれたとき、この結合テストだけが気づける。
    バッククォート入りの応答 (\\u0060 置換が入るケース) を含めて往復を確かめる。
    """
    module = _load_probe_record_comment_module()
    record = _record(
        turns=[_turn(1, 2000, assistantText="コード例は `let x = 1` です")]
    )
    probe_json = tmp_path / "probe.json"
    probe_json.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    assert module.format_comment(probe_json, "999", "https://example.test/run") == 0
    body = capsys.readouterr().out

    envelope = parse_probe_comment(body)
    assert envelope is not None
    assert (
        envelope["record"]["turns"][0]["assistantText"] == "コード例は `let x = 1` です"
    )


def test_l1_最新の記録を作成時刻で選ぶ_記録以外のコメントは無視する() -> None:
    old = _envelope_comment(_record(), "2026-08-08T22:00:00Z")
    new = _envelope_comment(_record(), "2026-08-09T22:00:00Z")
    noise = {"body": "人間の雑談 (JSON なし)", "created_at": "2026-08-09T23:00:00Z"}
    # 並び順に依存しないこと (新しい方を前に置いても正しく選ぶ)
    found = latest_record([new, noise, old])
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


def test_l1_出力コメントは機械可読なJSONブロックを持つ() -> None:
    record = _record()
    envelope = {
        "kind": "ux-probe-record",
        "runId": "31339682965",
        "scenarioId": "work-overwhelm-v1",
        "probeId": record["probeId"],
        "record": record,
    }
    created = datetime(2026, 8, 9, 22, 37, 35, tzinfo=timezone.utc)
    body = build_comment(
        envelope, created, measure(record), NOW, "123", "https://x/123"
    )

    blocks = re.findall(r"```json\s*(.*?)```", body, re.DOTALL)
    assert len(blocks) == 1
    out = json.loads(blocks[0])
    assert out["kind"] == "ux-eval-mech"
    assert out["probeRunId"] == "31339682965"
    assert out["recordCommentCreatedAt"] == "2026-08-09T22:37:35Z"
    assert out["metrics"]["completedTurns"] == 2
    # 会話本文 (自由文) を持ち込まない — フェンス破壊と肥大化の予防
    assert "userText" not in blocks[0]
    assert "assistantText" not in blocks[0]


def test_l1_cli_鮮度切れは赤_鮮度内は本文を出す(tmp_path, capsys) -> None:
    stale = _envelope_comment(_record(), "2026-08-07T22:00:00Z")
    path = tmp_path / "comments.json"
    path.write_text(json.dumps([stale]), encoding="utf-8")
    assert run(path, now=NOW) == EXIT_NO_FRESH_RECORD

    fresh = _envelope_comment(_record(), "2026-08-09T22:37:35Z")
    path.write_text(json.dumps([stale, fresh]), encoding="utf-8")
    capsys.readouterr()
    assert run(path, now=NOW) == EXIT_OK
    out = capsys.readouterr().out
    assert "ux-eval-mech" in out


def test_l1_cli_記録ゼロは赤(tmp_path) -> None:
    path = tmp_path / "comments.json"
    path.write_text(
        json.dumps([{"body": "記録なし", "created_at": "2026-08-09T22:00:00Z"}])
    )
    assert run(path, now=NOW) == EXIT_NO_FRESH_RECORD
