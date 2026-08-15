#!/usr/bin/env python3
"""UX judge レポートから `ux-judge-score` ブロックを取り出して検証する (#154 / ADR 0027 D1)。

採点の蓄積 (Issue #127) は**時系列データ**であり、壊れた 1 件が混ざると
トレンドの判断がそのぶん狂う。人が転記していたときは目視が検証だったので、
無人化するなら検証を機械にやらせないと「静かに壊れた採点が積み上がる」。

検証するのは形式だけではなく **total / max が scores と整合しているか**まで。
judge が集計を誤ると verdict の閾値判定 (M2 のトリガー) がそのまま狂うため。

使い方:
    validate-judge-score.py <judge レポートのパス>

終了コード:
    0 = 妥当。正規化した JSON を stdout に出力
    2 = ブロックが見つからない
    3 = 検証に失敗 (理由を stderr に出力)
"""

from __future__ import annotations

import json
import re
import sys

REQUIRED_KEYS = {
    "schemaVersion", "kind", "rubricVersion", "probeId", "scenarioId",
    "scoredAt", "scores", "total", "max", "verdict", "unknowns",
}
# 観点の真実は .github/claude/ux-rubric.md。**rubric と同じ PR でここを直す** —
# 片方だけだと judge は採点できるのに蓄積で弾かれる。
#
# **rubricVersion ごとに観点集合を持つ** (Codex P1 / PR #436)。1 つに固定すると、
# `rubricVersion: "0.1"` と書かれたまま U1〜U7・14 点のレポートが素通りし、
# **0.1 と 0.2 の断絶点を示すメタデータが静かに壊れる** — 蓄積は時系列データで、
# version が信用できなくなると版をまたいだ比較の禁止 (max 12 → 14) が守れない。
# 未知の version は受理しない (新版を足すときはここに 1 行足す)。
#   perspectives = その版の観点 / critical = 0 なら他がどうであれ赤にする観点
RUBRIC_VERSIONS = {
    "0.1": {
        "perspectives": ["U1", "U2", "U3", "U4", "U5", "U6"],
        "critical": ("U1", "U2", "U3"),
    },
    # 0.2: U7 (仮説の差し出し方) を追加 — 仮説の押し付け・危機領域への仮説は、
    # 合計が高くても会話として通してはいけないので critical に入れる (#432 PO 裁定 Q2)
    "0.2": {
        "perspectives": ["U1", "U2", "U3", "U4", "U5", "U6", "U7"],
        "critical": ("U1", "U2", "U3", "U7"),
    },
}
VALID_VERDICTS = {"green", "yellow", "red"}
UNKNOWN = "UNKNOWN"

# 行区切りに依存しない切り出し (runbook の jq と同じ方針)
BLOCK_RE = re.compile(r"```json\s*(?P<body>.*?)```", re.DOTALL)


def find_block(text: str) -> dict | None:
    for m in BLOCK_RE.finditer(text):
        try:
            obj = json.loads(m.group("body"))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("kind") == "ux-judge-score":
            return obj
    return None


def validate(obj: dict) -> list[str]:
    """問題点のリストを返す (空なら妥当)。"""
    errors: list[str] = []

    missing = REQUIRED_KEYS - set(obj)
    if missing:
        errors.append(f"必須キーが欠けています: {sorted(missing)}")

    if obj.get("schemaVersion") != 1:
        errors.append(f"schemaVersion が 1 ではありません: {obj.get('schemaVersion')!r}")

    # 採点形式は rubricVersion で決まる。ここを見ずに 1 つの形式へ固定すると、
    # version が実際の形式とズレたレポートが素通りして断絶点の記録が壊れる
    rubric_version = obj.get("rubricVersion")
    spec = RUBRIC_VERSIONS.get(rubric_version) if isinstance(rubric_version, str) else None
    if spec is None:
        errors.append(
            f"rubricVersion が未知です: {rubric_version!r} / "
            f"既知={sorted(RUBRIC_VERSIONS)} — .github/claude/ux-rubric.md を改定したなら "
            "RUBRIC_VERSIONS にその版の観点集合を足してください"
        )
        return errors
    perspectives = spec["perspectives"]
    critical = spec["critical"]

    scores = obj.get("scores")
    if not isinstance(scores, dict):
        errors.append("scores が object ではありません")
        return errors

    missing_p = [p for p in perspectives if p not in scores]
    if missing_p:
        errors.append(f"観点が欠けています (rubricVersion {rubric_version}): {missing_p}")

    # この版に無い観点が混ざっていたら弾く — 「0.1 と名乗って 0.2 の形式で採点する」を
    # total/max の偶然の一致に頼らず落とす
    extra_p = [p for p in scores if p not in perspectives]
    if extra_p:
        errors.append(
            f"rubricVersion {rubric_version} に無い観点が入っています: {sorted(extra_p)} / "
            f"この版の観点={perspectives}"
        )

    numeric: list[int] = []
    for p in perspectives:
        if p not in scores:
            continue
        v = scores[p]
        if v == UNKNOWN:
            continue
        if not isinstance(v, int) or isinstance(v, bool) or v not in (0, 1, 2):
            errors.append(f"{p} のスコアが 0/1/2/UNKNOWN ではありません: {v!r}")
            continue
        numeric.append(v)

    # 集計の整合 — ここが狂うと M2 のトリガー判定がそのまま狂う
    expected_total = sum(numeric)
    expected_max = 2 * len(numeric)
    if obj.get("total") != expected_total:
        errors.append(f"total が scores と不整合: total={obj.get('total')!r} / 実際の合計={expected_total}")
    if obj.get("max") != expected_max:
        errors.append(
            f"max が scores と不整合: max={obj.get('max')!r} / "
            f"UNKNOWN を除いた観点数 {len(numeric)} × 2 = {expected_max}"
        )

    verdict = obj.get("verdict")
    if verdict not in VALID_VERDICTS:
        errors.append(f"verdict が {sorted(VALID_VERDICTS)} のいずれでもありません: {verdict!r}")
    elif expected_max > 0:
        # rubric の閾値: >=0.75 緑 / 0.5-0.75 黄 / <0.5 または critical のいずれかが 0 で赤
        ratio = expected_total / expected_max
        critical_zero = any(scores.get(p) == 0 for p in critical)
        if critical_zero:
            expected_verdict = "red"
        elif ratio >= 0.75:
            expected_verdict = "green"
        elif ratio >= 0.5:
            expected_verdict = "yellow"
        else:
            expected_verdict = "red"
        if verdict != expected_verdict:
            errors.append(
                f"verdict が rubric の閾値と不整合: verdict={verdict!r} / "
                f"期待={expected_verdict!r} (比率 {ratio:.2f}, "
                f"{'/'.join(critical)} に 0 が{'ある' if critical_zero else 'ない'})"
            )

    unknowns = obj.get("unknowns")
    if not isinstance(unknowns, list):
        errors.append("unknowns が配列ではありません")
    else:
        declared = {p for p in perspectives if scores.get(p) == UNKNOWN}
        # rubric: UNKNOWN にした理由を必ず添える
        if len(declared) != len(unknowns):
            errors.append(
                f"UNKNOWN の数と理由の数が一致しません: scores 側 {len(declared)} 件 "
                f"({sorted(declared)}) / unknowns 側 {len(unknowns)} 件"
            )

    for key in ("probeId", "scenarioId", "scoredAt"):
        if not isinstance(obj.get(key), str) or not obj.get(key, "").strip():
            errors.append(f"{key} が空です")

    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    text = open(sys.argv[1], encoding="utf-8").read()
    obj = find_block(text)
    if obj is None:
        print("ux-judge-score の JSON ブロックが見つかりません。", file=sys.stderr)
        print("judge が rubric の出力ルール 3 に従っていない可能性があります。", file=sys.stderr)
        return 2

    errors = validate(obj)
    if errors:
        print("採点ブロックの検証に失敗しました — 蓄積に投稿しません:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 3

    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
