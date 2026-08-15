#!/usr/bin/env python3
"""ダウンロード済みの UX プローブ artifact を検査し、採点対象の記録 JSON を選ぶ。

`fetch-latest-probe.sh` から「gh に依存しない後半」だけを切り出したもの (#160 選択肢 B)。
agent セッションには GitHub 直接 API の経路が無く gh が使えないため、artifact の取得だけを
呼び出し側 (人間なら gh、agent なら GitHub MCP) に任せ、**取得後の判断はここに一本化する**。
これで人間の経路と agent の経路が同じ判断を共有できる。

**シナリオごとに 1 本ずつ出す** (#435 でプローブの台本が 2 本になった)。1 本だけ返すと、
採点はその 1 シナリオぶんしか行われず、**もう片方は毎朝記録されるのに一度も採点されない**
(rubric 0.2 の U7 はまさに否定局面シナリオでしか発火しないので、観測器を足した意味が消える)。

標準出力に**記録 JSON のパスだけ**を 1 行 1 本で出す (採点セッションが 1 本ずつ judge に
渡せるように)。診断メッセージはすべて stderr へ — 混ぜると呼び出し側が壊れる
(PR #88 で踏んだ実例)。

使い方:
    cicd/scripts/ux-probe/inspect-probe-artifact.py <artifact を展開したディレクトリ>

終了コード:
    0 = 採点できる記録 JSON がある (パスを stdout に 1 行 1 本で出力)
    3 = JSON が無い (プローブ手前で fail した / 全体スキップ)
    4 = 記録はあるが全シナリオで turns が 0 件 (採点する材料がない)
    1 = 前提不足・想定外 (ディレクトリが無い / JSON が壊れている)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_NO_RECORD = 3
EXIT_NO_TURNS = 4


def log(message: str) -> None:
    print(message, file=sys.stderr)


def inspect(directory: Path) -> int:
    if not directory.is_dir():
        log(f"ディレクトリがありません: {directory}")
        return EXIT_UNEXPECTED

    candidates = sorted(p for p in directory.rglob("*.json") if p.is_file())
    if not candidates:
        log(f"artifact に JSON がありません: {directory}")
        log("  → プローブ手前 (curl 版 golden-path / 結線カナリア) で fail したか、")
        log("     AZURE_* variables 未設定で全体がスキップされた可能性があります。")
        log(
            "  → run の step summary / NG 行でホップを特定してください (プローブ自体の問題ではない)。"
        )
        return EXIT_NO_RECORD

    # シナリオごとに 1 本。同じシナリオが複数あれば名前順の最後 (= 最新) を採る
    # (シェル版の `find | sort | tail -1` と同じ規則を、シナリオ単位に適用する)
    by_scenario: dict[str, tuple[Path, dict, dict]] = {}
    for probe_json in candidates:
        try:
            record = json.loads(probe_json.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            # ここを 4 (turns 0 件) に丸めない。「壊れている」と「空だった」は原因も対処も違い、
            # 混ぜると壊れた記録が「材料なし」として静かに握り潰される。
            log(f"記録 JSON を読めません: {probe_json}: {exc}")
            return EXIT_UNEXPECTED

        if not isinstance(record, dict):
            log(f"記録 JSON がオブジェクトではありません: {probe_json}")
            return EXIT_UNEXPECTED

        scenario = record.get("scenario") or {}
        if not isinstance(scenario, dict):
            scenario = {}
        by_scenario[str(scenario.get("id", "?"))] = (probe_json, record, scenario)

    selected: list[Path] = []
    empty: list[str] = []
    for scenario_id, (probe_json, record, scenario) in sorted(by_scenario.items()):
        turns = record.get("turns") or []
        n_turns = len(turns) if isinstance(turns, list) else 0

        # 完了往復数を診断として出す。turns が計画未満でも採点は可能なので落とさない
        # (runbook「記録はあるが turns が 4 件未満」— 壊れる直前までは残っている)。
        log(
            f"scenario={scenario_id} "
            f"turns={n_turns}/{scenario.get('plannedTurns', '?')} "
            f"probeId={record.get('probeId', '?')}"
        )
        log(f"記録: {probe_json}")

        if n_turns == 0:
            empty.append(scenario_id)
            continue
        selected.append(probe_json)

    if not selected:
        log("すべてのシナリオで turns が 0 件です — 採点する材料がありません。")
        return EXIT_NO_TURNS

    if empty:
        # 一部だけ空でも残りは採点できる。ただし黙って減らさない —
        # 「そのシナリオは採点されなかった」ことが分からないと、次の朝の欠測と区別できない
        log(
            f"注意: turns が 0 件のシナリオを採点対象から外しました: {', '.join(empty)}"
        )

    for probe_json in selected:
        print(probe_json)
    return EXIT_OK


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        log(f"使い方: {Path(argv[0]).name} <artifact を展開したディレクトリ>")
        return EXIT_UNEXPECTED
    return inspect(Path(argv[1]))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
