"""[L1] プローブ記録の封筒 (format ↔ extract) が往復で壊れないことを pin する。

投稿側 (golden-path-monitor) と読み取り側 (毎朝の採点 Routine) は別プロセス・別日に動くので、
封筒の形が片側だけ変わっても**その場では誰も気づかない**。翌朝の採点が静かに止まるだけ。
往復テストがその乖離をここで止める。

- 往復で record が変質すると、judge が採点するのは実際の会話ではない別物になる
- kind マーカーの取り違えは、無関係なコメントを記録として食わせる
- 上限超過を検出できないと、途中で切れた JSON が投稿され、原因追跡できないまま採点が止まる

ここで test しないこと:
- 採点する材料があるかの判断 (turns 0 件など) — それは inspect-probe-artifact.py
- Issue への投稿そのもの (GitHub の責務。workflow 側は gh、agent 側は MCP)
- 採点レポートの検証 — それは validate-judge-score.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "probe-record-comment.py"


def _run(
    args: list[str], env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    import os

    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _record(n_turns: int = 4) -> dict:
    return {
        "probeId": "probe-1",
        "scenario": {"id": "consultation-v1", "plannedTurns": 4},
        "turns": [
            {"index": i, "user": f"質問{i}", "assistant": f"応答{i}", "warnings": []}
            for i in range(n_turns)
        ],
    }


def _write_record(tmp_path: Path, record: object) -> Path:
    path = tmp_path / "probe.json"
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return path


def test_envelope_は_recordedAt_付きの_1行JSON_を出す(tmp_path: Path) -> None:
    """データブランチ追記 (ADR 0040) の出口。recordedAt が無いと ux_eval の
    鮮度判定と append.py の月振り分けが働かず、記録が静かに読み飛ばされる。"""
    original = _record()
    src = _write_record(tmp_path, original)

    result = _run(["envelope", str(src)], {"GITHUB_RUN_ID": "31459063773"})

    assert result.returncode == 0
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1  # JSONL に 1 行で入る形
    envelope = json.loads(lines[0])
    assert envelope["kind"] == "ux-probe-record"
    assert envelope["runId"] == "31459063773"
    assert envelope["record"] == original
    from datetime import datetime

    parsed = datetime.fromisoformat(envelope["recordedAt"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_format_して_extract_すると_元の_record_に戻る(tmp_path: Path) -> None:
    original = _record()
    src = _write_record(tmp_path, original)

    formatted = _run(["format", str(src)], {"GITHUB_RUN_ID": "12345"})
    assert formatted.returncode == 0

    comment = tmp_path / "comment.md"
    comment.write_text(formatted.stdout, encoding="utf-8")
    out_dir = tmp_path / "out"

    extracted = _run(["extract", str(comment), str(out_dir)])
    assert extracted.returncode == 0
    assert extracted.stdout.strip() == str(out_dir)

    restored = json.loads((out_dir / "probe.json").read_text(encoding="utf-8"))
    assert restored == original


def test_format_の本文に_run_と_往復数_が人間向けに載る(tmp_path: Path) -> None:
    src = _write_record(tmp_path, _record(n_turns=2))

    result = _run(["format", str(src)], {"GITHUB_RUN_ID": "999"})

    assert result.returncode == 0
    assert "999" in result.stdout
    assert "完了 2/4 往復" in result.stdout
    assert "consultation-v1" in result.stdout


def test_封筒に_kind_と要約が入る(tmp_path: Path) -> None:
    src = _write_record(tmp_path, _record())

    result = _run(["format", str(src)], {"GITHUB_RUN_ID": "777"})

    block = result.stdout.split("```json")[1].split("```")[0]
    envelope = json.loads(block)
    assert envelope["kind"] == "ux-probe-record"
    assert envelope["runId"] == "777"
    assert envelope["completedTurns"] == 4
    assert envelope["scenarioId"] == "consultation-v1"


def test_大きすぎる記録は_exit5_で投稿本文を出さない(tmp_path: Path) -> None:
    huge = _record()
    huge["turns"][0]["assistant"] = "あ" * 80000
    src = _write_record(tmp_path, huge)

    result = _run(["format", str(src)], {"GITHUB_RUN_ID": "1"})

    # 切り詰めて投稿すると「壊れた記録」として弾かれ、原因が追えなくなる
    assert result.returncode == 5
    assert result.stdout.strip() == ""


def test_壊れた記録_JSON_は_exit1(tmp_path: Path) -> None:
    src = tmp_path / "probe.json"
    src.write_text("{ broken", encoding="utf-8")

    result = _run(["format", str(src)], {"GITHUB_RUN_ID": "1"})

    assert result.returncode == 1
    assert result.stdout.strip() == ""


def test_kind_の無い_JSON_ブロックだけのコメントは_exit2(tmp_path: Path) -> None:
    comment = tmp_path / "comment.md"
    comment.write_text(
        'ふつうの会話です\n\n```json\n{"kind": "ux-judge-score", "total": 12}\n```\n',
        encoding="utf-8",
    )

    result = _run(["extract", str(comment), str(tmp_path / "out")])

    assert result.returncode == 2


def test_他の_JSON_ブロックが混ざっていても_ux_probe_record_を選ぶ(
    tmp_path: Path,
) -> None:
    original = _record()
    src = _write_record(tmp_path, original)
    formatted = _run(["format", str(src)], {"GITHUB_RUN_ID": "42"})

    comment = tmp_path / "comment.md"
    comment.write_text(
        '```json\n{"kind": "ux-judge-score"}\n```\n\n'
        + formatted.stdout
        + "\n\n```json\nnot json at all {\n```\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"

    result = _run(["extract", str(comment), str(out_dir)])

    assert result.returncode == 0
    restored = json.loads((out_dir / "probe.json").read_text(encoding="utf-8"))
    assert restored == original


def test_応答文にコードフェンスが含まれても往復する(tmp_path: Path) -> None:
    # 実 AI の応答が Markdown のコード例を含むと ``` がフェンス内に現れ、そこで切れた
    # 壊れた JSON が投稿される。読み取り側では「記録ブロックが無い」に見えるため、
    # 記録があったのに材料なしとして静かに握り潰される (PR #163 レビュー指摘)
    original = _record(n_turns=1)
    original["turns"][0]["assistant"] = (
        "たとえばこう書けます:\n```python\nprint('hi')\n```\nどうでしょう `code` も含みます"
    )
    src = _write_record(tmp_path, original)

    formatted = _run(["format", str(src)], {"GITHUB_RUN_ID": "555"})
    assert formatted.returncode == 0

    # フェンスの中にバッククォートが 1 つも残っていないこと (早期終了の芽を断つ)
    block = formatted.stdout.split("```json")[1].rsplit("```", 1)[0]
    assert "`" not in block

    comment = tmp_path / "comment.md"
    comment.write_text(formatted.stdout, encoding="utf-8")
    out_dir = tmp_path / "out"

    extracted = _run(["extract", str(comment), str(out_dir)])
    assert extracted.returncode == 0

    restored = json.loads((out_dir / "probe.json").read_text(encoding="utf-8"))
    assert restored == original
    assert "```python" in restored["turns"][0]["assistant"]


def test_引数が足りなければ_exit1(tmp_path: Path) -> None:
    assert _run(["format"]).returncode == 1
    assert _run(["extract", str(tmp_path)]).returncode == 1
    assert _run([]).returncode == 1
