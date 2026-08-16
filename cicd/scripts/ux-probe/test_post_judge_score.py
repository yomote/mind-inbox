"""[単体] post-judge-score.sh の失敗検知 — #466。

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


def _tree(tmp_path: Path, exit_code: int, stderr: str = "") -> tuple[Path, Path, Path]:
    """実スクリプトを、追記側をスタブに差し替えた一時ツリーで動かせるようにする。

    post-judge-score.sh は追記先を `$HERE/../ux-data/append-observation.sh` で
    解決するので、同じ相対配置を作れば実スクリプトをそのまま叩ける。

    **スタブは受け取った引数を記録する。** 引数を無視するスタブにすると、本体から
    `"$PAYLOAD"` を落とす変異を入れても全部緑のままで、「本物の経路を通した」と
    読めてしまう (PR #475 で Codex が実測した穴)。3 本目の戻り値がその記録先。
    """
    probe = tmp_path / "ux-probe"
    data = tmp_path / "ux-data"
    probe.mkdir()
    data.mkdir()
    shutil.copy(SCRIPT, probe / SCRIPT.name)
    shutil.copy(VALIDATOR, probe / VALIDATOR.name)

    argv_log = tmp_path / "argv.txt"
    appender = data / "append-observation.sh"
    appender.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{argv_log}"\n'
        + (f'echo "{stderr}" >&2\n' if stderr else "")
        + f"exit {exit_code}\n",
        encoding="utf-8",
    )
    appender.chmod(0o755)

    report = tmp_path / "judge.md"
    report.write_text(_report_text(), encoding="utf-8")
    return probe / SCRIPT.name, report, argv_log


def _run(script: Path, report: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), str(report)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_単体_追記が落ちた回は採点が載っていないことを名指しする(tmp_path) -> None:
    """#466 の本体。追記側が落ちた朝を「載った」と読ませない。

    無いと何が静かに通るか:
        追記側が落ちた回に**出るのは追記側自身の stderr 1 行だけ**になり、
        「採点は載っていません」という名指しが消える。#466 で運用者が取り違えたのは
        まさにここで、画面に残った文言から蓄積が載ったと読んだ。

    注意 (実測 2026-08-16): 呼び出し元には `set -euo pipefail` があるため、
    この 12 行が無くても **exit は非ゼロになり「追記しました」も出ない**。
    つまりこの 12 行が足しているのは終了コードではなく**診断の名指し**であり、
    このテストが固定しているのもそこ。`returncode != 0` だけを見る assert では
    12 行を畳んでも緑のままになる (PR #475 の代役レビューで判明)。
    """
    script, report, argv_log = _tree(tmp_path, exit_code=1, stderr="push に失敗しました")
    r = _run(script, report)

    assert r.returncode != 0, "追記に失敗したのに成功で終わっている"
    combined = r.stdout + r.stderr
    # この 1 行が消えると #466 の取り違えに戻る
    assert "採点は載っていません" in combined, combined
    # 成功表示は push が通った後だけ。落ちた回に出てはいけない
    assert "追記しました" not in combined, combined
    # 追記側は payload のパスを 1 個受け取って呼ばれること (受け渡しの固定)
    assert argv_log.read_text(encoding="utf-8").split() != [], "追記側が引数無しで呼ばれている"


def test_単体_追記が通ったときだけ成功を名乗る(tmp_path) -> None:
    """対照実験。上の assert が「常に失敗する」で緑になっていないことを示す。"""
    script, report, _ = _tree(tmp_path, exit_code=0)
    r = _run(script, report)

    assert r.returncode == 0, r.stderr
    combined = r.stdout + r.stderr
    assert "追記しました" in combined, combined
    assert "採点は載っていません" not in combined, combined


def test_単体_追記側は採点payloadのパスを受け取って呼ばれる(tmp_path) -> None:
    """本体から `"$PAYLOAD"` を落とす変異でこのテストが落ちる。

    無いと何が静かに通るか:
        引数を参照しないスタブだけで検証していると、本体が追記側を**引数無しで**
        呼ぶようになっても全テストが緑のまま通る (PR #475 で Codex が実測)。
        実経路では payload が渡らず必ず失敗するのに、CI は「本物の経路を通した」と読める。
    """
    script, report, argv_log = _tree(tmp_path, exit_code=0)
    r = _run(script, report)

    assert r.returncode == 0, r.stderr
    argv = [line for line in argv_log.read_text(encoding="utf-8").splitlines() if line]
    assert len(argv) == 1, f"追記側の引数がちょうど 1 個でない: {argv}"

    # 渡されたのは検証済み採点を書き出した実在の JSON であること
    payload = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    assert payload["kind"] == "ux-judge-score"
    assert payload["probeId"] == "ux-probe-test"
    assert payload["report"].startswith("採点レポート本文"), "レポート全文が同梱されていない"
    assert payload["recordedAt"], "recordedAt (鮮度・月振り分けの基準) が無い"


def test_単体_検証に落ちた採点は追記側を呼ばない(tmp_path) -> None:
    """壊れた採点が蓄積に入らないこと (スクリプト冒頭の門が生きていること)。

    無いと何が静かに通るか:
        検証の終了コードを取り違えると、集計の合わない採点が積まれて
        UX トレンドがそのぶん狂う。狂いは時系列にしか出ないので他に気づく手段が無い。
    """
    script, report, argv_log = _tree(tmp_path, exit_code=0)
    # total を scores と食い違わせる (rubric の整合が崩れた採点)
    report.write_text(
        _report_text().replace('"total": 14', '"total": 3'), encoding="utf-8"
    )

    r = _run(script, report)

    assert r.returncode != 0, "壊れた採点が通っている"
    assert not argv_log.exists(), "検証に落ちたのに追記側が呼ばれている"
