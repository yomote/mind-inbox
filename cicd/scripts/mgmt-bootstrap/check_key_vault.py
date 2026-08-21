#!/usr/bin/env python3
"""Key Vault の**データプレーン**にまつわる 2 つの判定 (Issue #499 の発見 1 / 2)。

呼び出し元は bootstrap.sh の手順 5 (データプレーン権限の事前確認) と手順 6b (鍵が
エクスポート不可か)。判定だけをここに持ち、シェルは結果を受け取って文面を組み立てる
だけにしてある (cicd/CLAUDE.md「判定ロジックをシェルや workflow の中に埋めない —
純粋関数に切り出して pytest で押さえる」)。

## これが無いと何が静かに通るか

### 1. データプレーン権限の事前確認 (`data_plane_verdict`)

**「権限が無い」と「確かめられなかった」が混ざる。** Key Vault は RBAC 方式で、
control-plane のロール (Contributor / Owner) に data-plane は**含まれない**。実測
(PO / 2026-08-17) では `ForbiddenByRbac` (`Assignment: (not found)`) で手順 6b が
停止した。ここで「az が非ゼロ = 権限不足」と決めつけると、Vault 名の間違いや
ネットワーク断まで「ロールを付けろ」と案内してしまい、PO は付与しても直らない指示を
延々と踏む。逆に非ゼロを握り潰せば、**権限が無いまま「確認 OK」**になる。だから
権限エラー (forbidden) と**それ以外の失敗** (error) を分けて、error は「未検証」として
止める。

### 2. exportable の判定 (`key_export_verdict`)

**空を `false` と読み違える / `true` を見逃す。** 実応答では `exportable` は
`attributes` の下にあり、**エクスポート不可の鍵はこの属性を持たない** (省略が既定)。
つまり「空 = 正常」で、`[ "$x" = "false" ]` のような**一致で通す**判定は正常な鍵を
落とす (#499 発見 2 の実害)。逆に `!= "true"` の 1 行をシェルに書くと、`az` の tsv が
bool を `True` と大文字で返す版で**エクスポート可能な鍵を見逃す**。ここでは
大文字小文字を潰したうえで「明示的に true なら落とす / 解釈できない値も通さない」を
固定する。
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass

# シェルが 1 行を読み分けるための区切り (check_permissions.py と同じ約束)。
FIELD_SEPARATOR = "|"
ITEM_SEPARATOR = ","

# プローブ名 → (足りないときに付与すべきロール, 何のために要るか)。
# ここが「どの操作にどのロールが要るか」の正典で、シェルはロール名を知らない。
# - keys:    手順 6b の `az keyvault key show` (鍵メタデータの読み取り)。
#            Key Vault Reader の DataActions `Microsoft.KeyVault/vaults/*/read` で足りる
#            (鍵マテリアルは読めない = 最小)。
# - secrets: 手順 8 の `az keyvault secret show/set` (pem の格納)。
PROBE_ROLES: dict[str, tuple[str, str]] = {
    "keys": ("Key Vault Reader", "鍵メタデータの読み取り (検証 6b)"),
    "secrets": ("Key Vault Secrets Officer", "pem の格納 (手順 8)"),
}

# az が data-plane の RBAC 不足を返すときの目印 (小文字で照合)。
# 実測は `(Forbidden) ... Assignment: (not found)` / エラーコード `ForbiddenByRbac`。
# 旧来の access policy 方式の Vault では `does not have keys get permission` になる。
FORBIDDEN_MARKERS = (
    "forbiddenbyrbac",
    "forbidden",
    "not authorized",
    "unauthorized",
    "does not have",
)

# 1 行に載せる detail の上限。az のエラーは複数行で長いので、原因の頭だけを運ぶ
# (全文は握り潰さず、シェル側が az の stderr をそのまま出す)。
DETAIL_LIMIT = 300


@dataclass(frozen=True)
class DataPlaneReport:
    """データプレーン権限の事前確認の結果。

    status:
        ok        — 確かめた範囲では通る
        forbidden — 権限不足。`roles` を付与すれば直る
        error     — **確かめられなかった**。権限の話ではないので付与を案内しない
    """

    status: str
    roles: tuple[str, ...]
    detail: str

    def render(self) -> str:
        """シェルが読む 1 行。`status|roles|detail`。

        ⚠️ この書式は bootstrap.sh 側の `${dp_report%%|*}` 系の切り出しと対になって
        いる。変えるなら両方を同じ PR で。
        """
        return FIELD_SEPARATOR.join(
            (self.status, ITEM_SEPARATOR.join(self.roles), self.detail)
        )


def _one_line(text: str) -> str:
    """改行と区切り文字を潰して 1 行にする (シェルが 1 行として読むため)。

    握り潰しではない: 元の stderr はシェル側がそのまま画面に出しており、ここで作るのは
    「どのプローブが何で落ちたか」の要約だけ。
    """
    flat = " ".join(text.split()).replace(FIELD_SEPARATOR, "/")
    return flat[:DETAIL_LIMIT]


def classify_probe(returncode: int, stderr: str) -> str:
    """1 回のプローブの結果を `ok` / `forbidden` / `error` に分類する。

    副作用なし。`returncode == 0` なら stderr に何が出ていても ok
    (az は警告を stderr に書くため)。
    """
    if returncode == 0:
        return "ok"
    lowered = stderr.lower()
    if any(marker in lowered for marker in FORBIDDEN_MARKERS):
        return "forbidden"
    return "error"


def data_plane_verdict(results: Mapping[str, tuple[int, str]]) -> DataPlaneReport:
    """プローブ名 → (終了コード, stderr) から、止めるかどうかと付与すべきロールを決める。

    - どれか 1 つでも `error` (権限以外の失敗) なら **error を優先**する。「権限を付ければ
      直る」という誤った案内より、「確かめられなかった」を出す方が安全なため。
    - `forbidden` のプローブがあれば、そのプローブに対応するロールだけを名指しする
      (通っている方まで付与させない = 過剰権限を勧めない)。

    未知のプローブ名は KeyError で落とす — 綴り間違いを「該当なし = OK」にしない。
    """
    outcomes = {
        name: classify_probe(rc, stderr) for name, (rc, stderr) in results.items()
    }
    for name in outcomes:
        if name not in PROBE_ROLES:
            raise KeyError(f"未知のプローブ名: {name}")

    errored = [n for n, o in outcomes.items() if o == "error"]
    if errored:
        detail = _one_line(
            "; ".join(f"{n}: {results[n][1] or f'exit={results[n][0]}'}" for n in errored)
        )
        return DataPlaneReport(status="error", roles=(), detail=detail)

    forbidden = [n for n, o in outcomes.items() if o == "forbidden"]
    if forbidden:
        roles = tuple(dict.fromkeys(PROBE_ROLES[n][0] for n in sorted(forbidden)))
        detail = _one_line(
            "; ".join(f"{PROBE_ROLES[n][1]} が拒否されました" for n in sorted(forbidden))
        )
        return DataPlaneReport(status="forbidden", roles=roles, detail=detail)

    return DataPlaneReport(status="ok", roles=(), detail="")


def key_export_verdict(raw: str) -> str:
    """`az keyvault key show --query attributes.exportable -o tsv` の生値を判定する。

    戻り値:
        ok         — エクスポート不可 (空 / 未設定 / false)。**空が正常**であることが肝
        exportable — 明示的に true。設計が崩れている
        unknown    — どちらとも読めない値。ok にしない (未検証を成功と混ぜない)

    大文字小文字を潰すのは、az の tsv 出力が bool を `True` / `true` のどちらで返すかが
    CLI の版に依存するため (ローカルでは実 az を叩けないので**両方通す**)。
    """
    value = raw.strip().lower()
    if value in ("", "none", "null", "false"):
        return "ok"
    if value == "true":
        return "exportable"
    return "unknown"


def _parse_probe_args(args: list[str]) -> dict[str, tuple[int, str]]:
    """`<name> <rc> <stderrファイル>` の 3 つ組の並びを読む。

    数が合わなければ ValueError で落とす (足りない引数を「プローブなし = OK」にしない)。
    """
    if not args or len(args) % 3 != 0:
        raise ValueError(
            "data-plane の引数は <name> <rc> <stderrファイル> の 3 つ組で渡してください"
        )
    results: dict[str, tuple[int, str]] = {}
    for name, rc, err_path in zip(args[0::3], args[1::3], args[2::3]):
        with open(err_path, encoding="utf-8", errors="replace") as f:
            results[name] = (int(rc), f.read())
    return results


def main(argv: list[str] | None = None) -> int:
    """CLI。判定結果は stdout に 1 行で返し、**終了コードは判定で変えない**。

    ⚠️ 呼び出し元 (bootstrap.sh) は `set -euo pipefail` 下の `$(...)` で受けており、
    ここで非ゼロを返すと PO 向けの文面を出す前に errexit で落ちる
    (check_permissions.py と同じ約束)。止めるかどうかはシェルが決める。

        check_key_vault.py data-plane <name> <rc> <stderrファイル> ...  → status|roles|detail
        check_key_vault.py key-export <生値>                            → ok|exportable|unknown
    """
    args = list(sys.argv[1:] if argv is None else argv)
    mode, rest = args[0], args[1:]
    if mode == "data-plane":
        print(data_plane_verdict(_parse_probe_args(rest)).render())
    elif mode == "key-export":
        print(key_export_verdict(rest[0] if rest else ""))
    else:
        raise ValueError(f"未知のモード: {mode}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
