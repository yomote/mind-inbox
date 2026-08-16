#!/usr/bin/env python3
"""UX 観測 1 件をデータブランチの月別 JSONL に追記する (ADR 0041)。

蓄積の形 (ここが真実):
    データブランチ `data/ux-observations` に 1 行 = 1 観測の JSONL。
    - probes/YYYY-MM.jsonl … kind: "ux-probe-record" (golden-path-monitor)
    - evals/YYYY-MM.jsonl  … kind: "ux-eval-mech" (ux-eval.yml) / "ux-judge-score" (PM tick)
    全行が共通で持つもの: `kind` (振り分けキー) と `recordedAt` (ISO8601 UTC —
    鮮度判定と月振り分けの基準。Issue コメント時代の created_at に相当)。

このスクリプトは**ファイル操作だけ**を担う。git (fetch / commit / push / orphan 作成) は
append-observation.sh、payload の中身の妥当性 (採点の整合など) は呼び出し側の責務
(validate-judge-score.py 等)。

同一観測の重複は追記せずスキップする (exit 0 / stderr に warning)。移行スクリプトの
再実行や workflow の再試行で同じ観測が二重に積まれると、トレンドの警告数・件数が
静かに水増しされるため。同一性は (kind, probeId, runId/probeRunId,
evaluatedAt/scoredAt/startedAt) で判定する — 同じプローブの再採点 (scoredAt が違う) は
別観測として残る。

使い方:
    append.py <data_dir> <payload.json>
      data_dir     = データブランチの checkout (probes/ evals/ をこの下に作る)
      payload.json = 1 観測の JSON オブジェクト (kind と recordedAt が必須)

stdout には追記したファイルの相対パスだけを出す (診断は stderr — PR #88 の規律)。
**このスクリプトの成功は「ローカルの JSONL に書けた」までを意味する。** 蓄積が
データブランチに載ったことを名乗れるのは push 後の append-observation.sh だけ (#466)。

終了コード:
    0 = 追記した (または同一観測につきスキップ)
    1 = 前提不足・想定外 (payload が壊れている / kind 不明 / recordedAt 不正)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# kind → 置き場所。増やすときはこの表と ADR 0041 D2 を一緒に更新する
KIND_DIRS = {
    "ux-probe-record": "probes",
    "ux-eval-mech": "evals",
    "ux-judge-score": "evals",
}

EXIT_OK = 0
EXIT_UNEXPECTED = 1


def log(message: str) -> None:
    print(message, file=sys.stderr)


def parse_ts(ts: object) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def identity_key(payload: dict) -> tuple:
    """観測の同一性キー (純粋関数)。再採点 (scoredAt 違い) は別観測として残す。"""
    record = payload.get("record")
    started = record.get("startedAt") if isinstance(record, dict) else None
    return (
        payload.get("kind"),
        payload.get("probeId"),
        payload.get("runId") or payload.get("probeRunId"),
        payload.get("evaluatedAt") or payload.get("scoredAt") or started,
    )


def target_path(data_dir: Path, payload: dict) -> Path | None:
    """payload の置き場所 (月別 JSONL) を決める。決められなければ None。"""
    kind = payload.get("kind")
    subdir = KIND_DIRS.get(kind) if isinstance(kind, str) else None
    if subdir is None:
        log(f"kind が不明です: {kind!r} (既知: {sorted(KIND_DIRS)})")
        return None
    recorded = parse_ts(payload.get("recordedAt"))
    if recorded is None:
        log(f"recordedAt が ISO8601 ではありません: {payload.get('recordedAt')!r}")
        return None
    if recorded.tzinfo is None:
        log(f"recordedAt にタイムゾーンがありません: {payload.get('recordedAt')!r}")
        return None
    return data_dir / subdir / f"{recorded:%Y-%m}.jsonl"


def existing_keys(target: Path) -> set[tuple]:
    """追記先ディレクトリの既存観測から同一性キーを集める。

    月境界をまたいだ重複 (移行と実運用で recordedAt の基準が僅かにずれる等) も
    弾けるよう、同じ subdir の全ファイルを見る。壊れた行は読み飛ばす (stderr)。
    """
    keys: set[tuple] = set()
    subdir = target.parent
    if not subdir.is_dir():
        return keys
    for path in sorted(subdir.glob("*.jsonl")):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                log(f"壊れた行を読み飛ばします: {path}:{lineno}")
                continue
            if isinstance(obj, dict):
                keys.add(identity_key(obj))
    return keys


def append(data_dir: Path, payload_path: Path) -> int:
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log(f"payload を読めません: {payload_path}: {exc}")
        return EXIT_UNEXPECTED
    if not isinstance(payload, dict):
        log(f"payload がオブジェクトではありません: {payload_path}")
        return EXIT_UNEXPECTED

    target = target_path(data_dir, payload)
    if target is None:
        return EXIT_UNEXPECTED

    if identity_key(payload) in existing_keys(target):
        log(
            f"同一観測が既にあります — 追記をスキップします "
            f"(kind={payload.get('kind')} probeId={payload.get('probeId')!r})"
        )
        print(target.relative_to(data_dir))
        return EXIT_OK

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    # 「追記しました」と書かない (#466) — この時点では作業用 worktree のファイルに
    # 書いただけで、commit / push はまだ。ここで完了を名乗ると、後段が落ちたときに
    # 失敗が成功に見える (蓄積が載っていないのに載ったと読める)。
    # 蓄積が確定したことを名乗ってよいのは push 後の append-observation.sh だけ。
    log(
        f"ローカルに書きました (commit/push はこの後): {target} "
        f"(kind={payload.get('kind')})"
    )
    print(target.relative_to(data_dir))
    return EXIT_OK


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        log(f"使い方: {Path(argv[0]).name} <data_dir> <payload.json>")
        return EXIT_UNEXPECTED
    return append(Path(argv[1]), Path(argv[2]))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
