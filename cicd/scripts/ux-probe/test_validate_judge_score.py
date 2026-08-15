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


def test_単体_通った採点の_stdout_は蓄積に渡せる_JSON_単体になる(tmp_path: Path) -> None:
    """**無いと何が静かに通るか**: stdout に診断文が 1 行でも混ざると、
    `post-judge-score.sh` がこの出力をそのまま `append.py` へ渡す経路が壊れ、
    その朝の採点が蓄積に入らない。診断は stderr、成果物だけを stdout という分担
    (PR #88 で実際に踏んだ) を、出力全体が単一 JSON として parse できることで押さえる。

    判定ロジックそのものの網は他のテストが張る (これは出力契約のテスト)。
    """
    result = _run(tmp_path, _report(ALL_TWO))
    assert result.returncode == EXIT_OK, result.stderr
    parsed = json.loads(result.stdout)  # 混入があればここで落ちる
    assert parsed["kind"] == "ux-judge-score"
    assert parsed["rubricVersion"] == "0.2"
    assert parsed["scores"] == ALL_TWO


def test_単体_rubricVersionと観点集合がズレた採点は弾かれる(tmp_path: Path) -> None:
    """**無いと何が静かに通るか**: `rubricVersion: "0.1"` と名乗りながら
    U1〜U7・14 点で採点したレポートが exit 0 で蓄積される。

    version は 0.1 と 0.2 の**断絶点を示す唯一のメタデータ** (max が 12 → 14 に変わる
    ので、版をまたいだ total の比較は禁止)。ここが実際の形式とズレたまま積まれると、
    後から版別に切り分ける手段が無くなり、トレンドの比較が黙って誤る。
    """
    old_version = _run(tmp_path, _report(ALL_TWO, rubricVersion="0.1"))
    assert old_version.returncode == EXIT_INVALID
    assert "U7" in old_version.stderr

    # 逆向き: 0.2 を名乗って U7 を落とすと「観点が欠けている」で弾かれる
    scores = {k: v for k, v in ALL_TWO.items() if k != "U7"}
    new_version = _run(tmp_path, _report(scores, rubricVersion="0.2"))
    assert new_version.returncode == EXIT_INVALID
    assert "U7" in new_version.stderr


def test_単体_未知のrubricVersionは受理しない(tmp_path: Path) -> None:
    """**無いと何が静かに通るか**: rubric を改定して version だけ上げ、
    `RUBRIC_VERSIONS` への追記を忘れた場合に、検証器が「知らない版」を
    現行形式として採点してしまう。知らない版は通さず落とす (静かに通さない)。
    """
    result = _run(tmp_path, _report(ALL_TWO, rubricVersion="0.3"))
    assert result.returncode == EXIT_INVALID
    assert "rubricVersion" in result.stderr


def test_単体_旧版_0_1_の採点は6観点のまま通る(tmp_path: Path) -> None:
    """**無いと何が静かに通るか**: 版ごとの観点集合を持たずに現行形式へ固定すると、
    0.1 で採点された過去のレポートが「U7 が無い」として一律に弾かれる。
    弾くこと自体は静かではないが、**0.1 の critical は U1〜U3 まで**という
    旧版の判定を失うと、過去データの再検証が現行の基準で上書きされてしまう。
    """
    old_scores = {"U1": 2, "U2": 2, "U3": 2, "U4": 2, "U5": 2, "U6": 2}
    ok = _run(tmp_path, _report(old_scores, rubricVersion="0.1"))
    assert ok.returncode == EXIT_OK, ok.stderr
    assert json.loads(ok.stdout)["max"] == 12

    # 0.1 では U7 が無いので、U1〜U3 だけが赤の致命観点
    red = _run(tmp_path, _report(dict(old_scores, U1=0), rubricVersion="0.1"))
    assert red.returncode == EXIT_INVALID
    assert "red" in red.stderr


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
    """**無いと何が静かに通るか**: 上の「green を弾く」だけだと、
    検証器を「致命観点が 0 なら常に弾く」に壊しても気づけない。そうなると
    **正しく red と判定した採点まで蓄積に入らなくなり、劣化の記録だけが欠ける**
    (トレンドは良い日だけが残って上向きに見える)。門が通すべきものを通すことを押さえる。
    """
    result = _run(tmp_path, _report(dict(ALL_TWO, **{perspective: 0}), verdict="red"))
    assert result.returncode == EXIT_OK, result.stderr


def test_単体_U4が0でも比率が高ければgreenのまま(tmp_path: Path) -> None:
    """赤にする観点は U1〜U3・U7 だけ — 網を広げすぎていないことも押さえる。"""
    result = _run(tmp_path, _report(dict(ALL_TWO, U4=0)))
    assert result.returncode == EXIT_OK, result.stderr


def test_単体_UNKNOWNは合計とmaxから除外され理由が要る(tmp_path: Path) -> None:
    """**無いと何が静かに通るか**: UNKNOWN を 0 点として合計に混ぜる実装に変わると、
    **判定材料が無かっただけの朝が「劣化した朝」として記録される** (rubric は UNKNOWN を
    合計から除外し max を減らすと定めている)。加えて理由の件数を数えないと、
    UNKNOWN が理由なしで積まれて PO 裁定キューが空回りする。
    """
    scores = dict(ALL_TWO, U3="UNKNOWN")
    ok = _run(tmp_path, _report(scores, unknowns=["U3: TTS 未観測で判定材料がない"]))
    assert ok.returncode == EXIT_OK, ok.stderr
    assert json.loads(ok.stdout)["max"] == 12

    ng = _run(tmp_path, _report(scores))  # unknowns が空
    assert ng.returncode == EXIT_INVALID
    assert "UNKNOWN" in ng.stderr


def test_単体_totalがscoresと合わない採点は弾かれる(tmp_path: Path) -> None:
    """**無いと何が静かに通るか**: judge が集計を誤った採点がそのまま積まれる。
    status ページのトレンドと M2 の起動判定が読むのは `total` / `max` なので、
    **観点別スコアは正しいのに折れ線だけが嘘をつく**状態になり、
    生の採点レポートを読み直すまで気づけない。
    """
    result = _run(tmp_path, _report(ALL_TWO, total=99))
    assert result.returncode == EXIT_INVALID
    assert "total" in result.stderr


def test_単体_採点ブロックが無いレポートは2で落ちる(tmp_path: Path) -> None:
    """**無いと何が静かに通るか**: 終了コード 2 (ブロック無し) と 3 (内容が不整合) の
    区別が消えると、「judge が出力ルールに従わなかった」のか「採点内容が壊れている」のか
    切り分けられなくなる。runbook はこの 2 と 3 を復旧手順の分岐に使っている。
    """
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
