"""[L1] inspect-probe-artifact の終了コードと stdout 契約を pin する。

この 2 つが狂うと、毎朝の無人採点 (#154 / ADR 0027 D1) が静かに壊れる:
- 終了コードは「その朝は採点しない」の判断そのもの。3 / 4 を 0 に丸めると、材料が無い朝に
  空の会話を採点した 0 点がスコアボード #127 に混ざり、M2 のトレンド判定がそのぶん狂う
- stdout は judge に渡すパスそのもの。診断文が 1 行でも混ざると呼び出し側が壊れる
  (PR #88 で実際に踏んだ)

ここで test しないこと:
- artifact のダウンロード (呼び出し側の責務。人間は gh、agent は GitHub MCP — #160)
- 採点レポートの中身の検証 (それは validate-judge-score.py)
- judge のスコアの妥当性 (rubric と PO の抜き打ち監査の領域)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "inspect-probe-artifact.py"


def _run(directory: Path | str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(directory)],
        capture_output=True,
        text=True,
        check=False,
    )


def _write(directory: Path, name: str, record: object) -> Path:
    path = directory / name
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return path


def _record(
    n_turns: int, scenario_id: str = "consultation-v1", probe_id: str = "probe-1"
) -> dict:
    return {
        "probeId": probe_id,
        "scenario": {"id": scenario_id, "plannedTurns": 4},
        "turns": [{"user": f"u{i}", "assistant": f"a{i}"} for i in range(n_turns)],
    }


def test_turns_ありなら_exit0_で_パスだけを_stdout_に出す(tmp_path: Path) -> None:
    expected = _write(tmp_path, "probe.json", _record(4))

    result = _run(tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip() == str(expected)
    # stdout はパス 1 行だけ — 診断が紛れ込んでいないこと
    assert result.stdout.strip().count("\n") == 0


def test_診断は_stderr_に出て_stdout_を汚さない(tmp_path: Path) -> None:
    _write(tmp_path, "probe.json", _record(2))

    result = _run(tmp_path)

    assert "turns=2/4" in result.stderr
    assert "probeId=probe-1" in result.stderr
    assert "turns=" not in result.stdout


def test_JSON_が無ければ_exit3_で_stdout_は空(tmp_path: Path) -> None:
    (tmp_path / "not-json.txt").write_text("noise", encoding="utf-8")

    result = _run(tmp_path)

    assert result.returncode == 3
    assert result.stdout.strip() == ""


def test_全シナリオのturns_が0件なら_exit4_で_stdout_は空(tmp_path: Path) -> None:
    _write(tmp_path, "probe.json", _record(0))

    result = _run(tmp_path)

    assert result.returncode == 4
    assert result.stdout.strip() == ""


def test_壊れた_JSON_は_exit1_で_turns0件_と区別する(tmp_path: Path) -> None:
    (tmp_path / "probe.json").write_text("{ broken", encoding="utf-8")

    result = _run(tmp_path)

    # 4 に丸めると「壊れている」が「材料なし」として静かに握り潰される
    assert result.returncode == 1
    assert result.stdout.strip() == ""


def test_同じシナリオが複数あれば_名前順の最後を採る(tmp_path: Path) -> None:
    _write(tmp_path, "probe-001.json", _record(4, probe_id="old"))
    latest = _write(tmp_path, "probe-002.json", _record(4, probe_id="new"))

    result = _run(tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip() == str(latest)
    assert "probeId=new" in result.stderr


def test_シナリオが複数あれば全部出す(tmp_path: Path) -> None:
    """無いと何が静かに通るか (#435):

    台本を 2 本に増やしても採点に回るのが 1 本だけになり、**もう片方は毎朝記録される
    のに一度も採点されない**。rubric 0.2 の U7 (仮説の押し付け) は否定局面シナリオ
    でしか発火しないので、観測器を足した意味がそのまま消える。
    """
    a = _write(tmp_path, "probe-push.json", _record(4, scenario_id="hypothesis-pushback-v1"))
    b = _write(tmp_path, "probe-work.json", _record(4, scenario_id="work-overwhelm-v1"))

    result = _run(tmp_path)

    assert result.returncode == 0
    # scenarioId 昇順 (パス順ではない) で安定させる
    assert result.stdout.strip().splitlines() == [str(a), str(b)]


def test_一部のシナリオが空でも残りを採点対象にする(tmp_path: Path) -> None:
    """途中で壊れたシナリオがあっても、取れている会話の採点まで捨てない。

    ただし外したことは stderr に残す (黙って 1 本に減ると欠測と区別できない)。
    """
    empty = _write(tmp_path, "probe-a.json", _record(0, scenario_id="broken-v1"))
    ok = _write(tmp_path, "probe-b.json", _record(4, scenario_id="work-overwhelm-v1"))

    result = _run(tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip().splitlines() == [str(ok)]
    assert str(empty) not in result.stdout
    assert "broken-v1" in result.stderr


def test_サブディレクトリ内の_JSON_も見つける(tmp_path: Path) -> None:
    nested = tmp_path / "ux-probe-123"
    nested.mkdir()
    expected = _write(nested, "probe.json", _record(4))

    result = _run(tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip() == str(expected)


@pytest.mark.parametrize("argv_extra", [[], ["a", "b"]])
def test_引数の数が違えば_exit1(tmp_path: Path, argv_extra: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *argv_extra],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1


def test_ディレクトリが無ければ_exit1(tmp_path: Path) -> None:
    result = _run(tmp_path / "missing")

    assert result.returncode == 1
