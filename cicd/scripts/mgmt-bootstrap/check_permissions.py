#!/usr/bin/env python3
"""GitHub App の installation token が返す権限集合を、想定と**完全一致**で照合する判定。

呼び出し元は bootstrap.sh の手順 6d (Key Vault へ pem を格納する**前**)。判定だけを
ここに持ち、シェル側は結果 (3 欄) を受け取って文面を組み立てるだけにしてある
(cicd/CLAUDE.md「判定ロジックをシェルや workflow の中に埋めない — 純粋関数に
切り出して pytest で押さえる」)。

## これが無いと何が静かに通るか

**過剰権限の App が「動くので」そのまま定着する。** App は手動フォームで作る (#497)
ので、権限が 2 つに絞れているかを機械が見る場所はこの installation token 応答しか
無い。「administration が write か」だけの**下限**チェックだと、Contents や Actions を
余分に付けた App でも満たしてしまい、その pem が Key Vault に格納されて以後の
plan/apply が過剰権限で回り続ける。

## なぜ 3 欄に分けるのか

「権限が違います」だけでは PO が直しようがない。**余分 / 値ちがい / 不足**は直し方が
それぞれ違う (権限を外す / レベルを変える / 権限を足す) ので、分類したまま名指しで
返す。3 欄すべてが空のときだけ一致。

⚠️ **「余分が無ければ OK」にしないこと。** 不足も値ちがいも落とすのが完全一致で、
   どれか 1 つでも見るのをやめると、そこだけ下限チェックに退行する。

## fail-closed の副作用 (認識したうえで選んでいる)

GitHub が将来 installation token 応答に暗黙の権限を足すと、この照合は「余分」として
PO を止める。過剰権限を素通りさせるより止まる方を選んだ — 何が余分かは名指しで
出るので、bootstrap.sh の `EXPECTED_PERMISSIONS` を根拠 (README.md の
「App 権限の最小化と根拠」) つきで更新すれば解ける。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

# シェルが 1 行を読み分けるための区切り。欄の中は複数権限を並べるので `,`、
# 欄と欄の区切りは権限名に現れない `|` を使う。
FIELD_SEPARATOR = "|"
ITEM_SEPARATOR = ","


@dataclass(frozen=True)
class PermissionReport:
    """想定との差分。3 つとも空なら完全一致。

    各要素は**そのまま PO の画面に出る文字列**にしてある (シェル側は連結するだけ)。
    """

    extra: tuple[str, ...]
    wrong: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def matches(self) -> bool:
        return not (self.extra or self.wrong or self.missing)

    def render(self) -> str:
        """シェルが読む 1 行。`余分|値ちがい|不足` の 3 欄 (空欄 = 問題なし)。

        ⚠️ この書式は bootstrap.sh 側の `${perm_report%%|*}` 系の切り出しと対になって
        いる。変えるなら両方を同じ PR で。
        """
        return FIELD_SEPARATOR.join(
            ITEM_SEPARATOR.join(field) for field in (self.extra, self.wrong, self.missing)
        )


def compare_permissions(
    expected: Mapping[str, str], got: Mapping[str, str]
) -> PermissionReport:
    """`got` が `expected` と完全一致するかを、余分 / 値ちがい / 不足に分類して返す。

    副作用なし・I/O なし。ここが判定の本体。
    """
    extra = tuple(sorted(f"{k}={v}" for k, v in got.items() if k not in expected))
    wrong = tuple(
        sorted(
            f"{k}={v} (期待 {expected[k]})"
            for k, v in got.items()
            if k in expected and v != expected[k]
        )
    )
    missing = tuple(sorted(f"{k}={expected[k]}" for k in expected if k not in got))
    return PermissionReport(extra=extra, wrong=wrong, missing=missing)


def parse_expected(pairs: Iterable[str]) -> dict[str, str]:
    """`name=level` の並びを辞書にする。`=` が無い引数は ValueError で落とす
    (握り潰して「想定なし = 何でも通る」にしない)。"""
    return dict(p.split("=", 1) for p in pairs)  # type: ignore[misc]


def permissions_from_token_response(path: str) -> dict[str, str]:
    """installation token の応答 JSON から `permissions` を読む。

    ⚠️ このファイルは token 本体を含む。値は返さず `permissions` だけを取り出す。
    JSON が壊れていれば例外で落ちる — 「読めなかった」を「権限なし」にしない。
    """
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("permissions", {})


def main(argv: list[str] | None = None) -> int:
    """CLI: `check_permissions.py <応答JSON> <name=level>...` → 3 欄を 1 行で stdout。

    ⚠️ **一致・不一致で終了コードを変えない。** 呼び出し元 (bootstrap.sh) は
    `set -euo pipefail` 下の `$(...)` で受けており、ここで非ゼロを返すと
    「何が余分か」を出す前に errexit で落ちて、PO 向けの文面が消える。
    判定結果は stdout の 3 欄で伝え、止めるかどうかはシェル側が決める。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    path, pairs = args[0], args[1:]
    report = compare_permissions(parse_expected(pairs), permissions_from_token_response(path))
    print(report.render())
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
