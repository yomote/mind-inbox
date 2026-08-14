#!/usr/bin/env python3
"""撤収 (cleanup-env.sh) が持続層に触れないようにするための判定。

ADR 0046 D1 / Issue #302。持続層 (Cosmos = ユーザーデータ / OpenAI = クォータ /
Key Vault = E2E trace の復号鍵) は環境の撤収で消してはいけない。層を RG で分けても、
**撤収スクリプトが「どの RG でも消せる」ままなら分断は宣言でしかない**ので、
ここで機械的に止める。

判定だけを純粋関数 (`decide`) に切り出してある (cicd/CLAUDE.md「判定ロジックを
シェルや workflow の中に埋めない」)。シェル側は az を叩いて材料を集めるだけ。

終了コード:
  0  削除してよい
  3  拒否 (呼び出し側は削除に進んではいけない)
  2  引数の誤り
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field

# 撤収で消えると取り返しがつかないリソース種別。
# キーは小文字化した ARM の type。値は「何を失うか」(拒否メッセージに出す)。
PERSISTENT_RESOURCE_TYPES: dict[str, str] = {
    "microsoft.documentdb/databaseaccounts": "Cosmos DB — 蓄積されたユーザーデータ (Problem / Mention)",
    "microsoft.cognitiveservices/accounts": "Cognitive Services — OpenAI のクォータ / Speech F0 枠 (1 サブスクに 1 つ)",
    "microsoft.keyvault/vaults": "Key Vault — E2E trace の復号鍵 (非エクスポート / ADR 0045 D5)",
}

DEFAULT_PERSISTENT_RG = "rg-shared-mindbox"


@dataclass(frozen=True)
class Decision:
    """撤収してよいかの判定結果。

    code は人間向けメッセージではなく**分岐の識別子**。テストと呼び出し側はこれを見る。
    """

    allowed: bool
    code: str
    reason: str
    findings: tuple[str, ...] = field(default=())

    def render(self) -> str:
        head = "OK" if self.allowed else "REFUSED"
        lines = [f"[persistent-layer-guard] {head} ({self.code}): {self.reason}"]
        lines.extend(f"  - {f}" for f in self.findings)
        return "\n".join(lines)


def normalize_rg(name: str) -> str:
    """RG 名は Azure 側で大文字小文字を区別しないので、比較前に揃える。"""
    return name.strip().lower()


def find_persistent(resource_types: list[str]) -> list[str]:
    """RG の中身から持続層のリソース種別を拾う (重複は畳む / 入力順を保つ)。"""
    seen: dict[str, str] = {}
    for raw in resource_types:
        key = raw.strip().lower()
        label = PERSISTENT_RESOURCE_TYPES.get(key)
        if label is not None and key not in seen:
            seen[key] = f"{raw.strip()} — {label}"
    return list(seen.values())


def decide(
    *,
    target_rg: str,
    persistent_rg: str = DEFAULT_PERSISTENT_RG,
    rg_exists: bool = True,
    resource_types: list[str] | None = None,
    allow_persistent: bool = False,
) -> Decision:
    """撤収してよいかを判定する。

    引数:
      target_rg:        撤収しようとしている RG。
      persistent_rg:    持続層の RG。
      rg_exists:        target_rg が実在するか。
      resource_types:   target_rg の中の ARM type 一覧。**取得に失敗したときは None**
                        を渡すこと (空リストと区別する — 空リストは「調べたら空だった」)。
      allow_persistent: 運用者が明示的に許可したか。

    判定の順序に意味がある。上から順に:
      1. 持続層 RG 自体 → **常に拒否**。override でも通さない (これが分断の実体)
      2. RG が無い       → 消すものが無いので許可 (cleanup-env.sh の冪等性を壊さない)
      3. 中身が取れない  → 拒否。「確かめられなかった」を「持続層なし」に読み替えない
      4. 持続層が居る    → 拒否 (override 可)
    """
    if normalize_rg(target_rg) == normalize_rg(persistent_rg):
        return Decision(
            allowed=False,
            code="target-is-persistent-rg",
            reason=(
                f"{target_rg} は持続層の RG です。撤収の対象は環境層だけで、"
                "持続層はどのフラグでも削除できません (ADR 0046 D1)。"
            ),
        )

    if not rg_exists:
        return Decision(
            allowed=True,
            code="rg-absent",
            reason=f"{target_rg} は存在しません (削除するものが無い)。",
        )

    if resource_types is None:
        if allow_persistent:
            return Decision(
                allowed=True,
                code="inventory-unavailable-overridden",
                reason=(
                    f"{target_rg} の中身を取得できませんでしたが、"
                    "運用者が明示的に許可したため続行します。"
                ),
            )
        return Decision(
            allowed=False,
            code="inventory-unavailable",
            reason=(
                f"{target_rg} の中身を取得できませんでした。**取れなかったことを"
                "「持続層は無い」と読み替えない**ため拒否します。az にログインしているか、"
                "RG を読む権限があるかを確認してください。"
            ),
        )

    findings = find_persistent(resource_types)
    if findings:
        if allow_persistent:
            return Decision(
                allowed=True,
                code="persistent-resources-present-overridden",
                reason=(
                    f"{target_rg} に持続層のリソースが居ますが、"
                    "運用者が明示的に許可したため続行します。**消えたものは戻りません。**"
                ),
                findings=tuple(findings),
            )
        return Decision(
            allowed=False,
            code="persistent-resources-present",
            reason=(
                f"{target_rg} に持続層のリソースが残っています。"
                "先に持続層 RG へ移してから撤収してください (Issue #302)。"
            ),
            findings=tuple(findings),
        )

    return Decision(
        allowed=True,
        code="ok",
        reason=f"{target_rg} に持続層のリソースはありません。",
    )


def _parse_inventory(raw: str) -> list[str]:
    """`az resource list --query "[].type" -o json` の出力を読む。"""
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError("inventory は JSON 配列であること")
    return [str(item) for item in parsed]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-rg", required=True, help="撤収しようとしている RG")
    parser.add_argument(
        "--persistent-rg",
        default=DEFAULT_PERSISTENT_RG,
        help=f"持続層の RG (既定: {DEFAULT_PERSISTENT_RG})",
    )
    parser.add_argument(
        "--rg-missing",
        action="store_true",
        help="target-rg が存在しない (削除するものが無い)",
    )
    parser.add_argument(
        "--inventory",
        help='`az resource list -g <rg> --query "[].type" -o json` の出力',
    )
    parser.add_argument(
        "--inventory-unavailable",
        action="store_true",
        help="中身の取得に失敗した (空とは区別する)",
    )
    parser.add_argument(
        "--allow-persistent",
        action="store_true",
        help="持続層が居ても続行する (取り返しがつかない)",
    )
    args = parser.parse_args(argv)

    if args.inventory is not None and args.inventory_unavailable:
        parser.error("--inventory と --inventory-unavailable は同時に指定できません")
    if (
        args.inventory is None
        and not args.inventory_unavailable
        and not args.rg_missing
    ):
        parser.error(
            "--inventory / --inventory-unavailable / --rg-missing のいずれかが必要です"
        )

    resource_types: list[str] | None
    if args.rg_missing or args.inventory_unavailable:
        resource_types = None
    else:
        try:
            resource_types = _parse_inventory(args.inventory)
        except (json.JSONDecodeError, ValueError) as exc:
            # 読めなかったものを空扱いにしない (それが「静かに全部消す」経路になる)。
            print(
                f"[persistent-layer-guard] inventory を読めませんでした: {exc}",
                file=sys.stderr,
            )
            resource_types = None

    decision = decide(
        target_rg=args.target_rg,
        persistent_rg=args.persistent_rg,
        rg_exists=not args.rg_missing,
        resource_types=resource_types,
        allow_persistent=args.allow_persistent,
    )
    print(decision.render(), file=sys.stderr)
    return 0 if decision.allowed else 3


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
