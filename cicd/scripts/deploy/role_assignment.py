#!/usr/bin/env python3
"""既存ロール割り当ての「養子縁組」に要る名前解決 (#262)。

なぜ純粋関数に切り出すか:
    ロール割り当ての一意性は **名前ではなく principal+role+scope** で決まる。
    別名で同じ組み合わせを宣言すると ARM は `RoleAssignmentExists` で
    **デプロイ全体を落とす** — dev の auto-deploy が丸ごと止まり、
    実環境が古いまま放置される (#262 / 2026-08-11 は半日赤のまま)。
    しかも失敗の仕方が「az の 1 行が空文字を返すだけ」なので、
    シェルに埋めると *静かに* 養子縁組をスキップして毎回同じ壁にぶつかる
    (PR #278 の実装がまさにこれで、1 回だけ効いて翌回から効かなくなった)。
    ここは判定を全部テストできる形にして、シェルは az を叩くだけにする。

2 つの入口:
    pick        — `az role assignment list -o json` の出力から、
                  対象 (principal+role+scope) の既存割り当て名を選ぶ (**大文字小文字を無視**)。
                  ARM は scope / roleDefinitionId の綴りを呼び出し元の入力どおりに
                  保持したり正規化したりするため、素の `==` 比較は当てにできない。
    from-error  — 上が空振りしても、ARM のエラー本文が既存割り当ての ID を
                  教えてくれる。そこから名前を取って**やり直す**ための解析。
                  ARM はダッシュ無しの 32 桁で返すので GUID 形に戻す。

使い方 (どちらも stdin から読み、見つかれば名前を 1 行 stdout / 無ければ exit 1):
    az role assignment list --scope "$SCOPE" -o json \
      | python3 role_assignment.py pick --scope "$SCOPE" \
          --principal-id "$PID" --role "$ROLE_GUID"
    python3 role_assignment.py from-error < deploy-error.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# ARM の RoleAssignmentExists は既存割り当ての名前をダッシュ無し 32 桁 hex で返す。
# 例: "The role assignment already exists. The ID of the existing role assignment
#      is 314eb860f90c493baa0c55e4226c2d34."
_EXISTS_PATTERN = re.compile(
    r"role assignment already exists.*?is\s+([0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?"
    r"[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12})",
    re.IGNORECASE | re.DOTALL,
)


def normalize_guid(raw: str) -> str:
    """ダッシュ有無どちらの 32 桁 hex も 8-4-4-4-12 の GUID 形に揃える。

    ロール割り当ての **リソース名は GUID 形式でなければならない**ので、
    ARM が返すダッシュ無し表記のまま bicep へ渡すと今度は名前不正で落ちる。
    """
    hexed = raw.replace("-", "").lower()
    if len(hexed) != 32 or not all(c in "0123456789abcdef" for c in hexed):
        raise ValueError(f"GUID として解釈できない: {raw!r}")
    return f"{hexed[:8]}-{hexed[8:12]}-{hexed[12:16]}-{hexed[16:20]}-{hexed[20:]}"


def name_from_error(text: str) -> str | None:
    """ARM のエラー本文から既存ロール割り当ての名前を取る。見つからなければ None。

    デプロイ出力は JSON がそのまま 1 行で出たり、az のラッパ文言が前後に付いたりする。
    エラー本文の形に依存しすぎないよう、キーワード + GUID だけを見る。
    """
    match = _EXISTS_PATTERN.search(text)
    if match is None:
        return None
    return normalize_guid(match.group(1))


def pick_existing_name(
    assignments: list[dict],
    *,
    scope: str,
    principal_id: str,
    role_definition_guid: str,
) -> str | None:
    """(principal, role, scope) が一致する既存割り当ての名前を返す。無ければ None。

    比較を全部 casefold で行うのが肝。`az role assignment list --scope` は
    継承分 (RG / サブスクリプション) も返すので scope 一致で絞る必要があるが、
    ARM が返す scope の綴り (`resourceGroups` / `resourcegroups`、プロバイダ名の
    大小) は作られ方によって揺れる。**素の == で絞ると「有るのに見つからない」**
    という、いちばん厄介な形の空振りになる。
    """
    want_scope = scope.casefold()
    want_principal = principal_id.casefold()
    want_role = role_definition_guid.casefold()
    for item in assignments:
        if not isinstance(item, dict):
            continue
        if str(item.get("scope", "")).casefold() != want_scope:
            continue
        if str(item.get("principalId", "")).casefold() != want_principal:
            continue
        role_id = str(item.get("roleDefinitionId", "")).casefold()
        # roleDefinitionId はフル ARM ID。末尾の GUID だけを見る。
        if role_id.rsplit("/", 1)[-1] != want_role:
            continue
        name = str(item.get("name", ""))
        if name:
            return name
    return None


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    pick = sub.add_parser("pick", help="az role assignment list の JSON から名前を選ぶ")
    pick.add_argument("--scope", required=True)
    pick.add_argument("--principal-id", required=True)
    pick.add_argument("--role", required=True, help="ロール定義の GUID")

    sub.add_parser("from-error", help="ARM のエラー本文から既存割り当ての名前を取る")

    args = parser.parse_args(argv)
    text = sys.stdin.read()

    if args.command == "pick":
        try:
            parsed = json.loads(text) if text.strip() else []
        except json.JSONDecodeError:
            print(
                "::warning::role assignment 一覧を JSON として読めませんでした",
                file=sys.stderr,
            )
            return 1
        if not isinstance(parsed, list):
            return 1
        name = pick_existing_name(
            parsed,
            scope=args.scope,
            principal_id=args.principal_id,
            role_definition_guid=args.role,
        )
    else:
        try:
            name = name_from_error(text)
        except ValueError as exc:
            print(f"::warning::{exc}", file=sys.stderr)
            return 1

    if not name:
        return 1
    print(name)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
