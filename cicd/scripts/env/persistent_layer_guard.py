#!/usr/bin/env python3
"""撤収 (cleanup-env.sh) が持続層に触れないようにするための判定。

ADR 0046 D1 / Issue #302。持続層 (Cosmos = ユーザーデータ / OpenAI = クォータ /
Key Vault = E2E trace の復号鍵 / バックアップ Storage = 復元元 / Log Analytics =
監査履歴) は環境の撤収で消してはいけない。層を RG で分けても、
**撤収スクリプトが「どの RG でも消せる」ままなら分断は宣言でしかない**ので、
ここで機械的に止める。

判定だけを純粋関数 (`decide`) に切り出してある (cicd/CLAUDE.md「判定ロジックを
シェルや workflow の中に埋めない」)。シェル側は az を叩いて材料を集めるだけ。

## 何を持続層と見なすか (3 段)

**型だけでは判定できない。** Key Vault は環境層にも居る (`bootstrap-core.bicep` の
SQL 管理者パスワード用 vault)、Storage も環境層に居る (Function App の実行 storage)、
Log Analytics も同様。型で一括りにすると、正当な環境層の撤収まで常に拒否されて
`ALLOW_PERSISTENT_DELETE=true` が常用になり、**ガードが意味を失う**。逆に型を外すと
バックアップ Storage が黙って消える。そこで判定を 3 段に分ける:

1. **層タグ** (`mindInboxLayer=persistent`) — `main-shared.bicep` が全リソースに刻む。
   **型を問わず持続層**。誤って環境層 RG へ shared を流した場合もここで捕まる。
2. **名前の名指し** (`--persistent-name`) — タグの無い移行前のリソースを守る逃げ道。
3. **型** — 環境層に「使い捨ての同型」が存在しない型だけ (Cosmos / Cognitive Services)。

タグも名前一致も無い「両層に出る型」(Key Vault / Storage / Log Analytics) は
**環境層として通すが、黙って通さない** — 何を環境層と見なしたかを notes に出す
(「取れなかったものを異常なしと書かない」の同類。判定の根拠を見えるようにする)。

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
from typing import Any

# 環境層に「使い捨ての同型」が存在しない = 型だけで持続層と断定してよいもの。
# キーは小文字化した ARM の type。値は「何を失うか」(拒否メッセージに出す)。
PERSISTENT_RESOURCE_TYPES: dict[str, str] = {
    "microsoft.documentdb/databaseaccounts": "Cosmos DB — 蓄積されたユーザーデータ (Problem / Mention)",
    "microsoft.cognitiveservices/accounts": "Cognitive Services — OpenAI のクォータ / Speech F0 枠 (1 サブスクに 1 つ)",
}

# 両層に出る型。**型だけでは持続層と断定できない**ので、層タグか名指しでのみ拾う。
# 値は「持続層側だったら何を失うか」。
LAYER_AMBIGUOUS_TYPES: dict[str, str] = {
    "microsoft.keyvault/vaults": "Key Vault — E2E trace の復号鍵 (非エクスポート / ADR 0045 D5)",
    "microsoft.storage/storageaccounts": "Storage — Cosmos バックアップの保管先 (ADR 0046 D9)",
    "microsoft.operationalinsights/workspaces": "Log Analytics — 監査ログの履歴 (ADR 0046 D1)",
}

# `main-shared.bicep` が刻む層タグ。ここが判定の一次ソース。
LAYER_TAG_KEY = "mindinboxlayer"
LAYER_TAG_PERSISTENT_VALUE = "persistent"

DEFAULT_PERSISTENT_RG = "rg-shared-mindbox"


@dataclass(frozen=True)
class ResourceRecord:
    """`az resource list` の 1 行。type だけでなく name / tags も持つ。"""

    type: str
    name: str = ""
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def type_key(self) -> str:
        return self.type.strip().lower()

    @property
    def name_key(self) -> str:
        return self.name.strip().lower()

    def label(self) -> str:
        return (
            f"{self.type.strip()}/{self.name.strip()}"
            if self.name.strip()
            else self.type.strip()
        )

    def layer_tag(self) -> str:
        """層タグの値 (小文字化)。Azure のタグキーは大小を区別しないので揃えて引く。"""
        for key, value in self.tags.items():
            if str(key).strip().lower() == LAYER_TAG_KEY:
                return str(value).strip().lower()
        return ""


def as_record(item: Any) -> ResourceRecord:
    """文字列 (type だけ) と dict (type/name/tags) の両方を受ける。

    `az resource list --query "[].type"` の素朴な出力でも動くようにしてある —
    材料の取り方が変わってもガードが黙って素通りしないため。
    """
    if isinstance(item, ResourceRecord):
        return item
    if isinstance(item, str):
        return ResourceRecord(type=item)
    if isinstance(item, dict):
        raw_tags = item.get("tags") or {}
        tags = (
            {str(k): str(v) for k, v in raw_tags.items()}
            if isinstance(raw_tags, dict)
            else {}
        )
        return ResourceRecord(
            type=str(item.get("type") or ""),
            name=str(item.get("name") or ""),
            tags=tags,
        )
    raise ValueError(f"resource は文字列か object であること: {item!r}")


@dataclass(frozen=True)
class Decision:
    """撤収してよいかの判定結果。

    code は人間向けメッセージではなく**分岐の識別子**。テストと呼び出し側はこれを見る。
    notes は拒否の理由ではなく「何をどう分類したか」の可視化。
    """

    allowed: bool
    code: str
    reason: str
    findings: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = field(default=())

    def render(self) -> str:
        head = "OK" if self.allowed else "REFUSED"
        lines = [f"[persistent-layer-guard] {head} ({self.code}): {self.reason}"]
        lines.extend(f"  - {f}" for f in self.findings)
        lines.extend(f"  # {n}" for n in self.notes)
        return "\n".join(lines)


def normalize_rg(name: str) -> str:
    """RG 名は Azure 側で大文字小文字を区別しないので、比較前に揃える。"""
    return name.strip().lower()


def find_persistent(
    resources: list[Any],
    persistent_names: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """RG の中身から持続層のリソースを拾う。

    返り値は (findings, notes):
      findings … 持続層と判定したもの (1 つでもあれば拒否側)
      notes    … 両層に出る型で、層タグも名指しも無かったもの (環境層として通す)

    重複は畳み、入力順を保つ。
    """
    wanted_names = {n.strip().lower() for n in (persistent_names or []) if n.strip()}
    findings: dict[str, str] = {}
    notes: dict[str, str] = {}

    for item in resources:
        record = as_record(item)
        key = f"{record.type_key}|{record.name_key}"

        if record.layer_tag() == LAYER_TAG_PERSISTENT_VALUE:
            reason = LAYER_AMBIGUOUS_TYPES.get(
                record.type_key
            ) or PERSISTENT_RESOURCE_TYPES.get(
                record.type_key, "持続層として宣言されたリソース"
            )
            findings.setdefault(key, f"{record.label()} — {reason} (層タグ)")
            continue

        if record.name_key and record.name_key in wanted_names:
            reason = LAYER_AMBIGUOUS_TYPES.get(
                record.type_key
            ) or PERSISTENT_RESOURCE_TYPES.get(
                record.type_key, "持続層として名指しされたリソース"
            )
            findings.setdefault(key, f"{record.label()} — {reason} (名指し)")
            continue

        type_label = PERSISTENT_RESOURCE_TYPES.get(record.type_key)
        if type_label is not None:
            findings.setdefault(key, f"{record.label()} — {type_label} (型)")
            continue

        ambiguous_label = LAYER_AMBIGUOUS_TYPES.get(record.type_key)
        if ambiguous_label is not None:
            notes.setdefault(
                key,
                f"{record.label()} は層タグ ({LAYER_TAG_KEY}=persistent) も名指しも無いため"
                "**環境層**として扱った"
                f" (持続層なら PERSISTENT_RESOURCE_NAMES に足すか shared を流し直してタグを刻むこと)",
            )
            continue

    return list(findings.values()), list(notes.values())


def decide(
    *,
    target_rg: str,
    persistent_rg: str = DEFAULT_PERSISTENT_RG,
    rg_exists: bool | None = True,
    resources: list[Any] | None = None,
    persistent_names: list[str] | None = None,
    allow_persistent: bool = False,
) -> Decision:
    """撤収してよいかを判定する。

    引数:
      target_rg:        撤収しようとしている RG。
      persistent_rg:    持続層の RG。
      rg_exists:        target_rg が実在するか。**確認そのものに失敗したときは None**
                        を渡すこと (False と区別する — False は「調べたら無かった」)。
      resources:        target_rg の中身 (str か {type,name,tags})。**取得に失敗した
                        ときは None** を渡すこと (空リストと区別する)。
      persistent_names: 層タグの無い持続層リソースの名指し。
      allow_persistent: 運用者が明示的に許可したか。

    判定の順序に意味がある。上から順に:
      1. 持続層 RG 自体   → **常に拒否**。override でも通さない (これが分断の実体)
      2. 存在を確かめられない → 拒否。「確かめられなかった」を「無い」に読み替えない
      3. RG が無い        → 消すものが無いので許可 (cleanup-env.sh の冪等性を壊さない)
      4. 中身が取れない   → 拒否。「確かめられなかった」を「持続層なし」に読み替えない
      5. 持続層が居る     → 拒否 (override 可)
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

    # 存在確認そのものが失敗したとき。**ここを「不在」に潰すと、中身 (inventory) を
    # 一度も検証しないままガードを通過し、後段の再確認が成功すれば削除に進んでしまう。**
    if rg_exists is None:
        if allow_persistent:
            return Decision(
                allowed=True,
                code="rg-existence-unknown-overridden",
                reason=(
                    f"{target_rg} が存在するかを確認できませんでしたが、"
                    "運用者が明示的に許可したため続行します。"
                ),
            )
        return Decision(
            allowed=False,
            code="rg-existence-unknown",
            reason=(
                f"{target_rg} が存在するかを確認できませんでした (az group exists が失敗)。"
                "**「確かめられなかった」を「RG が無い」と読み替えない**ため拒否します。"
                "az にログインしているか、サブスクリプションを読む権限があるかを確認してください。"
            ),
        )

    if not rg_exists:
        return Decision(
            allowed=True,
            code="rg-absent",
            reason=f"{target_rg} は存在しません (削除するものが無い)。",
        )

    if resources is None:
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

    findings, notes = find_persistent(resources, persistent_names)
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
                notes=tuple(notes),
            )
        return Decision(
            allowed=False,
            code="persistent-resources-present",
            reason=(
                f"{target_rg} に持続層のリソースが残っています。"
                "先に持続層 RG へ移してから撤収してください (Issue #302)。"
            ),
            findings=tuple(findings),
            notes=tuple(notes),
        )

    return Decision(
        allowed=True,
        code="ok",
        reason=f"{target_rg} に持続層のリソースはありません。",
        notes=tuple(notes),
    )


def _parse_inventory(raw: str) -> list[Any]:
    """`az resource list -g <rg> --query "[].{type:type,name:name,tags:tags}"` を読む。

    `[].type` 形式 (文字列の配列) も受ける — 材料の取り方が変わったときに
    「読めない」で落ちるほうが、黙って空扱いになるより安全なので型は緩めにしない。
    """
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError("inventory は JSON 配列であること")
    # ここで as_record を通しておく — 想定外の要素は例外にして、呼び出し側が
    # 「取得失敗 (None)」として扱う = 拒否側に倒れる。
    return [as_record(item) for item in parsed]


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
        "--rg-unknown",
        action="store_true",
        help="target-rg が存在するか確認できなかった (不在とは区別する)",
    )
    parser.add_argument(
        "--inventory",
        help='`az resource list -g <rg> --query "[].{type:type,name:name,tags:tags}" -o json` の出力',
    )
    parser.add_argument(
        "--inventory-unavailable",
        action="store_true",
        help="中身の取得に失敗した (空とは区別する)",
    )
    parser.add_argument(
        "--persistent-name",
        action="append",
        default=[],
        help="層タグの無い持続層リソースを名指しする (繰り返し可)",
    )
    parser.add_argument(
        "--allow-persistent",
        action="store_true",
        help="持続層が居ても続行する (取り返しがつかない)",
    )
    args = parser.parse_args(argv)

    if args.rg_missing and args.rg_unknown:
        parser.error("--rg-missing と --rg-unknown は同時に指定できません")
    if args.inventory is not None and args.inventory_unavailable:
        parser.error("--inventory と --inventory-unavailable は同時に指定できません")
    if (
        args.inventory is None
        and not args.inventory_unavailable
        and not args.rg_missing
        and not args.rg_unknown
    ):
        parser.error(
            "--inventory / --inventory-unavailable / --rg-missing / --rg-unknown "
            "のいずれかが必要です"
        )

    rg_exists: bool | None
    if args.rg_unknown:
        rg_exists = None
    else:
        rg_exists = not args.rg_missing

    resources: list[Any] | None
    if args.rg_missing or args.rg_unknown or args.inventory_unavailable:
        resources = None
    else:
        try:
            resources = _parse_inventory(args.inventory)
        except (json.JSONDecodeError, ValueError) as exc:
            # 読めなかったものを空扱いにしない (それが「静かに全部消す」経路になる)。
            print(
                f"[persistent-layer-guard] inventory を読めませんでした: {exc}",
                file=sys.stderr,
            )
            resources = None

    decision = decide(
        target_rg=args.target_rg,
        persistent_rg=args.persistent_rg,
        rg_exists=rg_exists,
        resources=resources,
        persistent_names=args.persistent_name,
        allow_persistent=args.allow_persistent,
    )
    print(decision.render(), file=sys.stderr)
    # 判定コードは **stdout** に出す。呼び出し側 (cleanup-env.sh) は許可/拒否の 2 値
    # だけでは足りない — 「不在だから通した」のか「中身を見て通した」のかで、
    # そのあと RG が現れたときの扱いが変わる。人間向けの本文は stderr のまま。
    print(decision.code)
    return 0 if decision.allowed else 3


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
