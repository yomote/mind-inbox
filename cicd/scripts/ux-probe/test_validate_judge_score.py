"""[単体] validate-judge-score の判定を pin する (rubricVersion 0.2 / #432)。

**無いと何が静かに通るか**: この script は「壊れた採点を蓄積に入れない」門で、
これまでテストが 1 本も無かった。門が緩むと、集計の合わない採点や
**U7 (仮説の押し付け) が 0 なのに green の採点**が `evals/*.jsonl` に積まれ、
UX トレンド (status ページ) と M2 の起動判定がそのぶん狂う。狂いは採点の
時系列にしか現れないので、他に気づく手段が無い。

仕様: `.github/claude/ux-rubric.md` の「出力ルール 1/2」(verdict の閾値と機械可読ブロック)。

ここで test しないこと:
- スコアそのものの妥当性 (rubric と PO の抜き打ち監査の領域)
- レポート本文の書式 (judge の出力ルール — 機械では縛らない)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "validate-judge-score.py"

EXIT_OK = 0
EXIT_NO_BLOCK = 2
EXIT_INVALID = 3

ALL_TWO = {"U1": 2, "U2": 2, "U3": 2, "U4": 2, "U5": 2, "U6": 2, "U7": 2}
CRITICAL = ["U1", "U2", "U3", "U7"]


def _report(scores: dict, **overrides) -> str:
    numeric = [v for v in scores.values() if isinstance(v, int)]
    payload = {
        "schemaVersion": 1,
        "kind": "ux-judge-score",
        "rubricVersion": "0.2",
        "probeId": "ux-probe-test",
        "scenarioId": "work-overwhelm-v1",
        "probeRunUrl": "https://example.invalid/run/1",
        "scoredAt": "2026-08-15T00:00:00Z",
        "scores": scores,
        "total": sum(numeric),
        "max": 2 * len(numeric),
        "verdict": "green",
        "unknowns": [],
    }
    payload.update(overrides)
    return "採点レポート本文\n\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n"


def _run(tmp_path: Path, text: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "report.md"
    path.write_text(text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_単体_満点の採点は通り正規化JSONを出す(tmp_path: Path) -> None:
    result = _run(tmp_path, _report(ALL_TWO))
    assert result.returncode == EXIT_OK, result.stderr
    assert json.loads(result.stdout)["scores"]["U7"] == 2


def test_単体_U7が欠けた採点は弾かれる(tmp_path: Path) -> None:
    """rubric 0.2 に対し judge が 0.1 の 6 観点で返した場合。

    通すと、押し付けを一度も見ていない採点が「満点」として積まれる。
    """
    scores = {k: v for k, v in ALL_TWO.items() if k != "U7"}
    result = _run(tmp_path, _report(scores))
    assert result.returncode == EXIT_INVALID
    assert "U7" in result.stderr


@pytest.mark.parametrize("perspective", CRITICAL)
def test_単体_赤にすべき観点が0なのにgreenなら弾く(tmp_path: Path, perspective: str) -> None:
    """U1〜U3・U7 のいずれかが 0 なら、比率が高くても verdict は red。

    U7 = 仮説の押し付け・危機領域への仮説。ここが抜けると、誘導尋問の会話が
    「合計は高いので green」として蓄積に入り、トレンド上は改善に見える。
    """
    result = _run(tmp_path, _report(dict(ALL_TWO, **{perspective: 0})))
    assert result.returncode == EXIT_INVALID
    assert "red" in result.stderr


@pytest.mark.parametrize("perspective", CRITICAL)
def test_単体_赤にすべき観点が0でredなら通る(tmp_path: Path, perspective: str) -> None:
    result = _run(tmp_path, _report(dict(ALL_TWO, **{perspective: 0}), verdict="red"))
    assert result.returncode == EXIT_OK, result.stderr


def test_単体_U4が0でも比率が高ければgreenのまま(tmp_path: Path) -> None:
    """赤にする観点は U1〜U3・U7 だけ — 網を広げすぎていないことも押さえる。"""
    result = _run(tmp_path, _report(dict(ALL_TWO, U4=0)))
    assert result.returncode == EXIT_OK, result.stderr


def test_単体_UNKNOWNは合計とmaxから除外され理由が要る(tmp_path: Path) -> None:
    scores = dict(ALL_TWO, U3="UNKNOWN")
    ok = _run(tmp_path, _report(scores, unknowns=["U3: TTS 未観測で判定材料がない"]))
    assert ok.returncode == EXIT_OK, ok.stderr
    assert json.loads(ok.stdout)["max"] == 12

    ng = _run(tmp_path, _report(scores))  # unknowns が空
    assert ng.returncode == EXIT_INVALID
    assert "UNKNOWN" in ng.stderr


def test_単体_totalがscoresと合わない採点は弾かれる(tmp_path: Path) -> None:
    result = _run(tmp_path, _report(ALL_TWO, total=99))
    assert result.returncode == EXIT_INVALID
    assert "total" in result.stderr


def test_単体_採点ブロックが無いレポートは2で落ちる(tmp_path: Path) -> None:
    result = _run(tmp_path, "採点ブロックを書き忘れたレポート\n")
    assert result.returncode == EXIT_NO_BLOCK


def test_単体_rubricの見本ブロック自身が閾値と整合する() -> None:
    """rubric の出力ルール 2 に載っている見本 JSON を、そのまま検証器に通す。

    **無いと何が静かに通るか**: 見本の verdict が閾値と食い違っていても誰も気づかない。
    実際 rubricVersion 0.1 の見本は 8/10 = 0.80 (= green) なのに `"verdict": "yellow"`
    と書かれており、見本を写した judge の採点は蓄積で弾かれる状態だった。
    judge は見本を手本にするので、ここが壊れていると毎朝の採点が落ちる。
    """
    rubric = Path(__file__).resolve().parents[3] / ".github" / "claude" / "ux-rubric.md"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(rubric)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == EXIT_OK, result.stderr
