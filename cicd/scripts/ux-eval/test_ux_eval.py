"""[L1] UX 機械計測の抽出 (ADR 0037 / 蓄積は ADR 0041 のデータブランチ)。

無いと何が静かに通るか:
    - 古い記録が「今日の計測」として積まれ、プローブが止まっているのに
      トレンドが伸び続ける (鮮度チェックの穴)
    - turns 0 件の空記録が「成功した計測」として緑のまま時系列に入り、
      プローブ全滅の朝も「計測は回っている」に見える (#401 — 鮮度チェックは
      「記録は書かれたが中身が空」を素通りさせる)
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
    EXIT_EMPTY_RECORD,
    EXIT_NO_FRESH_RECORD,
    EXIT_OK,
    EXIT_SCENARIO_SET_SHRUNK,
    EXIT_UNEXPECTED,
    build_payload,
    eval_step_directives,
    evaluated_probe_keys,
    expected_scenario_ids,
    is_fresh,
    latest_records_by_scenario,
    main,
    measure,
    read_observations,
    run,
)

NOW = datetime(2026, 8, 10, 0, 0, 0, tzinfo=timezone.utc)


def _record(
    turns: list[dict] | None = None,
    scenario_id: str = "work-overwhelm-v1",
    probe_id: str = "ux-probe-2026-08-09T22-37-09-200Z",
    run_id: str = "31339682965",
) -> dict:
    """真実 (ux-probe.spec.ts の ProbeRecord / #162 実コメントで確認済み) に沿った最小記録。"""
    return {
        "schemaVersion": 1,
        "kind": "ux-probe-conversation",
        "probeId": probe_id,
        "startedAt": "2026-08-09T22:37:09.200Z",
        "environment": {
            "appUrl": "https://app.example",
            "bffUrl": "https://bff.example",
            "gitSha": "0e81517",
            "runId": run_id,
            "runUrl": f"https://github.com/yomote/mind-inbox/actions/runs/{run_id}",
        },
        "scenario": {"id": scenario_id, "description": "x", "plannedTurns": 4},
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

    found = latest_records_by_scenario(read_observations(data / "probes"))
    assert set(found) == {"work-overwhelm-v1"}
    envelope, recorded_at = found["work-overwhelm-v1"]
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
    found = latest_records_by_scenario(read_observations(data / "probes"))
    assert set(found) == {"work-overwhelm-v1"}
    assert found["work-overwhelm-v1"][1].isoformat() == "2026-08-09T22:00:00+00:00"


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
    # recordedAt は append.py (ADR 0041) の必須キー — 無いと追記が exit 1 で落ちる
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


def test_l1_評価済みキーの抽出はkindで絞りシナリオまで見る() -> None:
    keys = evaluated_probe_keys(
        [
            {"kind": "ux-eval-mech", "probeRunId": "111", "scenarioId": "work-overwhelm-v1"},
            {"kind": "ux-eval-mech", "probeRunId": "111", "scenarioId": "hypothesis-pushback-v1"},
            {"kind": "ux-judge-score", "probeRunId": "222", "scenarioId": "work-overwhelm-v1"},
            {"kind": "ux-eval-mech", "probeRunId": None, "scenarioId": "work-overwhelm-v1"},
            "not-a-dict",
        ]
    )
    # 同じ run でもシナリオが違えば別キー — ここが runId だけだと 2 本目が
    # 「評価済み」に化けて永久に積まれない (#435)
    assert keys == {("111", "work-overwhelm-v1"), ("111", "hypothesis-pushback-v1")}


def _two_scenario_probes(recorded_at: str, run_id: str = "31339682965") -> list[dict]:
    """同じ run から 2 シナリオぶんの記録が積まれた朝 (#435 以降の通常の朝)。"""
    return [
        _envelope(
            _record(scenario_id="work-overwhelm-v1", probe_id="p-work", run_id=run_id),
            recorded_at,
        ),
        _envelope(
            _record(
                scenario_id="hypothesis-pushback-v1", probe_id="p-push", run_id=run_id
            ),
            recorded_at,
        ),
    ]


def test_l1_シナリオごとに計測を出す(tmp_path, capsys) -> None:
    """無いと何が静かに通るか (#435):

    同じ run から 2 シナリオの記録が来る朝に、最新 1 件しか計測しないと
    **もう片方のシナリオは一度も蓄積されない**。run は緑のままなので、
    「否定局面シナリオを足したのに U7 のトレンドが 1 本も無い」ことに誰も気づけない。
    """
    data = _data_dir(tmp_path, probes=_two_scenario_probes("2026-08-09T22:37:35Z"))
    capsys.readouterr()
    assert run(data, now=NOW) == EXIT_OK

    payloads = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
    # scenarioId 昇順で安定させている (追記順が run ごとに揺れない)
    assert [p["scenarioId"] for p in payloads] == [
        "hypothesis-pushback-v1",
        "work-overwhelm-v1",
    ]
    assert {p["probeId"] for p in payloads} == {"p-push", "p-work"}
    assert all(p["kind"] == "ux-eval-mech" for p in payloads)


def test_l1_片方だけ評価済みなら残りを積む(tmp_path, capsys) -> None:
    """1 本目の追記後に落ちて再実行した朝 — 2 本目だけを積んで緑に戻れること。

    重複判定が runId だけだと、ここで 2 本目が「評価済み」に化けて永久に積まれない。
    また「評価済み」は縮小検知 (#443 / exit 5) の covered に数える — 数えないと、
    この再実行の朝が「1 本目が欠けた」と誤検知されて偽陽性の赤になる。
    """
    data = _data_dir(tmp_path, probes=_two_scenario_probes("2026-08-09T22:37:35Z"))
    capsys.readouterr()
    assert run(data, now=NOW) == EXIT_OK
    first = json.loads(capsys.readouterr().out.strip().splitlines()[0])
    assert first["scenarioId"] == "hypothesis-pushback-v1"

    (data / "evals").mkdir(parents=True, exist_ok=True)
    (data / "evals" / "2026-08.jsonl").write_text(
        json.dumps(first, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    assert run(data, now=NOW) == EXIT_OK
    remaining = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
    assert [p["scenarioId"] for p in remaining] == ["work-overwhelm-v1"]


def test_l1_前回積めたシナリオが欠けた朝は取れた分を出しつつ赤(tmp_path, capsys) -> None:
    """**#443 judge B での仕様変更**: 旧仕様 (#435 実装時) は「部分欠落でも exit 0 (緑)」
    だったが、「前回まで積めていたシナリオ集合より減ったら exit 5 (赤)」へ変えた。

    無いと何が静かに通るか:
        封筒化失敗の経路は monitor が warning + continue で緑のまま。ux-eval も取れた分
        だけで緑にすると、U7 の観測器 (hypothesis-pushback-v1) の蓄積だけが 10 日
        止まってもどこも赤くならない (PR #443 審査補強コメントの実測)。
        取れた計測は捨てない — payload は出力し、呼び出し側が追記してから run を落とす。
    """
    probes = [
        _envelope(
            _record(scenario_id="work-overwhelm-v1", probe_id="p-work"),
            "2026-08-09T22:37:35Z",
        ),
        _envelope(
            _record(scenario_id="hypothesis-pushback-v1", probe_id="p-push-old"),
            "2026-08-07T22:00:00Z",  # 鮮度切れ (前々日) — 新しい記録が来ていない
        ),
    ]
    # 前日までは両シナリオが積めていた (evals に直近の実績がある)
    evals = [
        {
            "kind": "ux-eval-mech",
            "probeRunId": "prev-run",
            "scenarioId": sid,
            "recordedAt": "2026-08-08T23:20:00Z",
        }
        for sid in ("work-overwhelm-v1", "hypothesis-pushback-v1")
    ]
    data = _data_dir(tmp_path, probes=probes, evals=evals)
    capsys.readouterr()
    assert run(data, now=NOW) == EXIT_SCENARIO_SET_SHRUNK
    captured = capsys.readouterr()
    payloads = [json.loads(l) for l in captured.out.strip().splitlines()]
    assert [p["scenarioId"] for p in payloads] == ["work-overwhelm-v1"]
    assert "hypothesis-pushback-v1" in captured.err


def test_l1_evalsに実績のないシナリオの欠落は赤にしない(tmp_path, capsys) -> None:
    """要求集合は evals の実績 (直近 168h) から作る — 台本を**足した初日** (まだ一度も
    積めていない) や、window を過ぎたシナリオ (退役済み) の不在で偽陽性の赤を出さない。

    無いと何が静かに通るか:
        要求集合を「過去に 1 度でも積んだ全シナリオ」にすると、退役させたシナリオが
        恒久的な赤になり、赤が日常化して本物の停止が埋もれる。
    """
    probes = [
        _envelope(
            _record(scenario_id="work-overwhelm-v1", probe_id="p-work"),
            "2026-08-09T22:37:35Z",
        ),
    ]
    evals = [
        {
            "kind": "ux-eval-mech",
            "probeRunId": "ancient-run",
            "scenarioId": "hypothesis-pushback-v1",
            "recordedAt": "2026-07-01T00:00:00Z",  # window (168h) の外 = 要求しない
        },
        # scenarioId の無い記録 (#435 以前の計測) も要求集合に数えない —
        # 数えると #435 導入直後の朝が恒常的な偽陽性の赤になる
        {
            "kind": "ux-eval-mech",
            "probeRunId": "legacy-run",
            "recordedAt": "2026-08-09T23:20:00Z",
        },
    ]
    data = _data_dir(tmp_path, probes=probes, evals=evals)
    capsys.readouterr()
    assert run(data, now=NOW) == EXIT_OK


def test_l1_要求集合の抽出はkindと鮮度とscenarioIdで絞る() -> None:
    """expected_scenario_ids (純粋関数) の境界 — ここが緩むと退役シナリオや移行データ
    まで「積め」と要求して偽陽性の赤になり、逆に絞りすぎると縮小検知が消える。"""
    observations = [
        {"kind": "ux-eval-mech", "scenarioId": "a", "recordedAt": "2026-08-09T00:00:00Z"},
        # window (168h) の外
        {"kind": "ux-eval-mech", "scenarioId": "b", "recordedAt": "2026-07-01T00:00:00Z"},
        # 別 kind (LLM 採点) は機械計測の実績ではない
        {"kind": "ux-judge-score", "scenarioId": "c", "recordedAt": "2026-08-09T00:00:00Z"},
        # scenarioId 無し (移行データ) は要求できない
        {"kind": "ux-eval-mech", "recordedAt": "2026-08-09T00:00:00Z"},
        "not-a-dict",
    ]
    assert expected_scenario_ids(observations, NOW, 168.0) == {"a"}


def test_l1_全シナリオが鮮度切れなら赤(tmp_path) -> None:
    data = _data_dir(tmp_path, probes=_two_scenario_probes("2026-08-07T22:00:00Z"))
    assert run(data, now=NOW) == EXIT_NO_FRESH_RECORD


def test_l1_全シナリオが評価済みなら赤(tmp_path, capsys) -> None:
    data = _data_dir(tmp_path, probes=_two_scenario_probes("2026-08-09T22:37:35Z"))
    capsys.readouterr()
    assert run(data, now=NOW) == EXIT_OK
    previous = capsys.readouterr().out.strip().splitlines()
    (data / "evals").mkdir(parents=True, exist_ok=True)
    (data / "evals" / "2026-08.jsonl").write_text(
        "".join(line + "\n" for line in previous), encoding="utf-8"
    )
    assert run(data, now=NOW) == EXIT_ALREADY_EVALUATED


def test_単体_turns0件の空記録は行を積みつつ赤(tmp_path, capsys) -> None:
    """#401 回帰: 2026-08-13 の実測 (probeId: ux-probe-2026-08-12T22-45-07-419Z /
    run 31648071011) — プローブが 1 往復も完了しないまま書いた記録で ux-eval が緑になった。

    無いと何が静かに通るか:
        鮮度チェックは「記録は書かれたが中身が空」を素通りさせる。プローブ全滅の朝も
        run が緑のままなので、ステータスページの UX トレンドは「計測は回っている」に
        見え、沈黙と正常が区別できない (monitor 赤 → ux-eval 緑が実際に起きた)。
    """
    empty = _envelope(
        _record(
            turns=[],
            probe_id="ux-probe-2026-08-12T22-45-07-419Z",
            run_id="31648071011",
        ),
        "2026-08-09T22:37:35Z",
    )
    data = _data_dir(tmp_path, probes=[empty])
    capsys.readouterr()
    assert run(data, now=NOW) == EXIT_EMPTY_RECORD
    captured = capsys.readouterr()
    # 行は積む (時系列の欠落も情報) — payload は出力したうえで赤にする
    payloads = [json.loads(l) for l in captured.out.strip().splitlines()]
    assert [p["metrics"]["completedTurns"] for p in payloads] == [0]
    assert payloads[0]["probeRunId"] == "31648071011"
    assert "turns 0 件" in captured.err


def test_単体_片方が空記録でも両方の行を積んで赤(tmp_path, capsys) -> None:
    """2 シナリオのうち 1 本だけ空だった朝 — 取れた計測は捨てず、run は赤にする。"""
    probes = [
        _envelope(
            _record(scenario_id="work-overwhelm-v1", probe_id="p-work"),
            "2026-08-09T22:37:35Z",
        ),
        _envelope(
            _record(
                turns=[], scenario_id="hypothesis-pushback-v1", probe_id="p-push"
            ),
            "2026-08-09T22:37:35Z",
        ),
    ]
    data = _data_dir(tmp_path, probes=probes)
    capsys.readouterr()
    assert run(data, now=NOW) == EXIT_EMPTY_RECORD
    captured = capsys.readouterr()
    payloads = [json.loads(l) for l in captured.out.strip().splitlines()]
    assert len(payloads) == 2
    # 空だったシナリオが名指しされること (どちらを調べればよいか分かる)
    assert "hypothesis-pushback-v1" in captured.err


def test_単体_空記録と集合縮小が同時の朝は縮小の赤を返しつつ両方をstderrに残す(
    tmp_path, capsys
) -> None:
    """終了コードは 1 つしか返せない — exit 5 を優先しても、空記録の存在が
    stderr から消えないこと (黙るともう片方の異常が見えなくなる)。"""
    probes = [
        _envelope(
            _record(turns=[], scenario_id="work-overwhelm-v1", probe_id="p-work"),
            "2026-08-09T22:37:35Z",
        ),
        # hypothesis-pushback-v1 は今朝の記録そのものが無い (縮小)
    ]
    evals = [
        {
            "kind": "ux-eval-mech",
            "probeRunId": "prev-run",
            "scenarioId": sid,
            "recordedAt": "2026-08-08T23:20:00Z",
        }
        for sid in ("work-overwhelm-v1", "hypothesis-pushback-v1")
    ]
    data = _data_dir(tmp_path, probes=probes, evals=evals)
    capsys.readouterr()
    assert run(data, now=NOW) == EXIT_SCENARIO_SET_SHRUNK
    err = capsys.readouterr().err
    assert "turns 0 件" in err
    assert "hypothesis-pushback-v1" in err


def test_単体_終了コードから呼び出し側の振る舞いへの変換(capsys) -> None:
    """exit 6 が「payload を追記したうえで run を赤にする」経路に接続される pin (#401)。

    無いと何が静かに通るか:
        この変換が YAML の if 連鎖にしかないと、出力名 (`empty_record=true`) や
        条件式が退行してもテストでは検出できず、定期 workflow が実際に空記録を
        踏む朝まで気づけない — #401 の穴が wiring の層で再発する。
    """
    assert eval_step_directives(EXIT_OK) == {"annotation": "", "outputs": [], "exit": 0}

    empty = eval_step_directives(EXIT_EMPTY_RECORD)
    assert empty["outputs"] == ["empty_record=true"]  # ux-eval.yml の if と同名
    assert empty["exit"] == 0  # 追記 step へ進む — 赤は追記の**後** (行は積む)
    assert empty["annotation"].startswith("::error::")

    shrunk = eval_step_directives(EXIT_SCENARIO_SET_SHRUNK)
    assert shrunk["outputs"] == ["scenario_set_shrunk=true"]
    assert shrunk["exit"] == 0
    assert shrunk["annotation"].startswith("::error::")

    # 入力停止系は追記へ進まず即赤
    for rc in (EXIT_NO_FRESH_RECORD, EXIT_ALREADY_EVALUATED):
        directives = eval_step_directives(rc)
        assert directives["exit"] == 1
        assert directives["outputs"] == []
        assert directives["annotation"].startswith("::error::")

    # 想定外の終了コードを緑に丸めない
    assert eval_step_directives(EXIT_UNEXPECTED)["exit"] == EXIT_UNEXPECTED


def test_単体_step_outcomeはフラグをGITHUB_OUTPUTへ書き注釈をstdoutに出す(
    tmp_path, capsys, monkeypatch
) -> None:
    """--step-outcome の実環境適用 (CLI 経由) — 判定と反映の結合を実物で確かめる。"""
    gh_output = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_output))
    assert main(["ux_eval.py", "--step-outcome", str(EXIT_EMPTY_RECORD)]) == 0
    assert gh_output.read_text(encoding="utf-8") == "empty_record=true\n"
    # annotation は stdout — Actions が ::error:: を拾うのは step ログ
    assert capsys.readouterr().out.startswith("::error::")


def test_単体_step_outcomeはGITHUB_OUTPUT未設定ならフラグを落とさず赤(
    capsys, monkeypatch
) -> None:
    """無いと何が静かに通るか:
        フラグを黙って落として exit 0 を返すと、「追記後に赤」の step が発火せず、
        空記録 (#401) や集合縮小 (#443) がまた緑で通る — 書けないなら赤にする。
    """
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert main(["ux_eval.py", "--step-outcome", str(EXIT_EMPTY_RECORD)]) != 0
    assert "GITHUB_OUTPUT" in capsys.readouterr().err
