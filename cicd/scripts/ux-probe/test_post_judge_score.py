"""[L1] post-judge-score.sh の失敗検知 — #466。

**無いと何が静かに通るか**: このスクリプトは UX 採点をデータブランチへ載せる
最後の 1 本で、#466 の実測ログ (「追記しました」の後に husky で落ちた) を出した
当の経路。にもかかわらず**これを実行するテストが 1 本も無かった** (PR #475 の
代役レビューで判明)。テストが無いと、追記の失敗を報せる 12 行を誰かが後で畳んでも
CI は緑のままで、採点が載っていない朝を「載った」と読む形に戻る。

ここで test しないこと:
- 採点の妥当性・検証ロジック (validate-judge-score.py 側の test が持つ)
- git の運搬そのもの (cicd/scripts/ux-data/test_ux_data.py が持つ)
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "post-judge-score.sh"
VALIDATOR = HERE / "validate-judge-score.py"

ALL_TWO = {"U1": 2, "U2": 2, "U3": 2, "U4": 2, "U5": 2, "U6": 2, "U7": 2}


def _report_text() -> str:
    payload = {
        "schemaVersion": 1,
        "kind": "ux-judge-score",
        "rubricVersion": "0.2",
        "probeId": "ux-probe-test",
        "scenarioId": "work-overwhelm-v1",
        "probeRunUrl": "https://example.invalid/run/1",
        "scoredAt": "2026-08-15T00:00:00Z",
        "scores": ALL_TWO,
        "total": 14,
        "max": 14,
        "verdict": "green",
        "unknowns": [],
    }
    return (
        "採点レポート本文\n\n```json\n"
        + json.dumps(payload, ensure_ascii=False)
        + "\n```\n"
    )


def _tree(tmp_path: Path, appender_body: str) -> tuple[Path, Path]:
    """実スクリプトを、追記側をスタブに差し替えた一時ツリーで動かせるようにする。

    post-judge-score.sh は追記先を `$HERE/../ux-data/append-observation.sh` で
    解決するので、同じ相対配置を作れば実スクリプトをそのまま叩ける。
    """
    probe = tmp_path / "ux-probe"
    data = tmp_path / "ux-data"
    probe.mkdir()
    data.mkdir()
    shutil.copy(SCRIPT, probe / SCRIPT.name)
    shutil.copy(VALIDATOR, probe / VALIDATOR.name)
    appender = data / "append-observation.sh"
    appender.write_text(appender_body, encoding="utf-8")
    appender.chmod(0o755)

    report = tmp_path / "judge.md"
    report.write_text(_report_text(), encoding="utf-8")
    return probe / SCRIPT.name, report


def _run(script: Path, report: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), str(report)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_l1_追記が落ちたら失敗として終わり成功を名乗らない(tmp_path) -> None:
    """#466 の本体。追記側が落ちた朝を「載った」と読ませない。

    無いと何が静かに通るか:
        append-observation.sh が push できずに終わっても、呼び出し元が
        終了コードを見ていなければ最後の「追記しました」だけが画面に残る。
        運用者は採点が蓄積に載ったと読み、実際のデータブランチは前日のまま。
        失敗検知の 12 行を畳むとこのテストが落ちる。
    """
    script, report = _tree(
        tmp_path,
        '#!/bin/sh\necho "push が競合しました" >&2\nexit 1\n',
    )
    r = _run(script, report)

    assert r.returncode != 0, "追記に失敗したのに成功で終わっている"
    combined = r.stdout + r.stderr
    assert "採点は載っていません" in combined, combined
    # 成功表示は push が通った後だけ。落ちた回に出てはいけない
    assert "追記しました" not in combined, combined


def test_l1_追記が通ったときだけ成功を名乗る(tmp_path) -> None:
    """対照実験。上の assert が「常に失敗する」で緑になっていないことを示す。"""
    script, report = _tree(tmp_path, "#!/bin/sh\nexit 0\n")
    r = _run(script, report)

    assert r.returncode == 0, r.stderr
    combined = r.stdout + r.stderr
    assert "追記しました" in combined, combined
    assert "採点は載っていません" not in combined, combined


def test_l1_検証に落ちた採点は追記側を呼ばない(tmp_path) -> None:
    """壊れた採点が蓄積に入らないこと (スクリプト冒頭の門が生きていること)。

    無いと何が静かに通るか:
        検証の終了コードを取り違えると、集計の合わない採点が積まれて
        UX トレンドがそのぶん狂う。狂いは時系列にしか出ないので他に気づく手段が無い。
    """
    marker = tmp_path / "呼ばれた.txt"
    script, report = _tree(
        tmp_path,
        f'#!/bin/sh\ntouch "{marker}"\nexit 0\n',
    )
    # total を scores と食い違わせる (rubric の整合が崩れた採点)
    report.write_text(
        _report_text().replace('"total": 14', '"total": 3'), encoding="utf-8"
    )

    r = _run(script, report)

    assert r.returncode != 0, "壊れた採点が通っている"
    assert not marker.exists(), "検証に落ちたのに追記側が呼ばれている"
