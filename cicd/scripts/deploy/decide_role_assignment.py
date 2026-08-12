#!/usr/bin/env python3
"""ai-agent MI → Cognitive Services OpenAI User の割り当てを bicep にどう宣言させるか決める (#262 / #277)。

なぜ純粋関数に切り出すか:
    ARM のロール割り当ての一意性は **名前ではなく principal+role+scope** で決まる。
    同じ三つ組を**別名**で作ろうとすると `RoleAssignmentExists` で
    デプロイ全体が落ちる。つまりこの判定を 1 回間違えるだけで
    「dev に何もデプロイできない」状態が続く (実際 2026-08-11 に発生)。

    PR #278 はこの判定をシェルに直書きし、3 つの az 参照をすべて
    `2>/dev/null || true` で潰していた。その結果 **「既存が無い」と
    「参照に失敗した」が区別できず**、参照が一度失敗すると黙って guid() 名に
    戻って次の bicep 適用が落ちる。run 31481914784 (成功) と
    31500334037 / 31500900952 (RoleAssignmentExists) の差がこれ。
    「静かに間違う」典型なので、テストできる形に出す
    (CLAUDE.md「状態・副作用を持つ新モジュールはテストファーストで切る」)。

使い方:
    python3 decide_role_assignment.py <ca_status> <openai_status> <role_status> <role_name>
      → "<action>\\t<detail>" を stdout に 1 行

    status は呼び出し元 (provision.sh) が az の rc / stderr から分類した 3 値:
      ok     … 引けた (値は引数で渡る)
      absent … 対象リソースがまだ無い (= 既存の割り当てもありえない)
      error  … 引けなかった (権限・throttling・一過性障害など)

action:
    create-new … 既存が無い。bicep に guid() 名で新規作成させる (パラメータを渡さない)
    adopt      … 既存があった。その名前を bicep に渡し no-op 更新にする (養子縁組)
    skip       … **分からなかった**。推測せず、この run では bicep に宣言させない。
                 incremental デプロイなので既存の割り当ては消えず、
                 deploy-ai-agent.sh の冪等な付与が引き続き実体を保証する
"""

from __future__ import annotations

import re
import sys

CREATE_NEW, ADOPT, SKIP = "create-new", "adopt", "skip"

OK, ABSENT, ERROR = "ok", "absent", "error"

# ロール割り当ての名前は GUID。az が想定外の文字列 (エラーメッセージ断片など) を
# 返したときに、それを resource 名として bicep へ流し込まないための最後の関門。
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def decide(
    ca_status: str,
    openai_status: str,
    role_status: str,
    role_name: str = "",
) -> tuple[str, str]:
    """(action, detail) を返す。

    ca_status:     ai-agent Container App の principalId 参照の結果
    openai_status: Azure OpenAI アカウント (scope) 参照の結果
    role_status:   その scope のロール割り当て一覧の参照結果
                   (上の 2 つが ok でないときは参照していないので absent を渡す)
    role_name:     role_status == ok のとき、見つかった既存割り当ての名前 (無ければ空)
    """
    ca = ca_status.strip().lower()
    oai = openai_status.strip().lower()
    role = role_status.strip().lower()
    name = role_name.strip()

    # 1) 参照できなかったものが 1 つでもあれば、何も推測しない。
    #    ここを create-new に丸めるのが #278 の穴だった (= RoleAssignmentExists)。
    for label, status in (
        ("ai-agent Container App", ca),
        ("Azure OpenAI アカウント", oai),
    ):
        if status == ERROR:
            return SKIP, f"{label} を参照できなかった"
        if status not in (OK, ABSENT):
            return SKIP, f"{label} の参照結果が不明 ({status!r})"

    # 2) 対象がまだ無いなら、既存の割り当てもありえない → bicep が guid() 名で作る。
    #    (初回デプロイ / OpenAI アカウントごと新規作成される場合)
    if ca == ABSENT or oai == ABSENT:
        return CREATE_NEW, "対象リソースが未作成 (既存の割り当てはありえない)"

    if role == ERROR:
        return SKIP, "ロール割り当ての一覧を参照できなかった"
    if role not in (OK, ABSENT):
        return SKIP, f"ロール割り当ての参照結果が不明 ({role!r})"

    # 3) 一覧は引けた。空なら既存なし。
    if role == ABSENT or not name:
        return CREATE_NEW, "同じ principal+role+scope の既存割り当ては無い"

    # 4) 名前が GUID でないなら、それは az の出力ではなく事故。名前として使わない。
    if not _GUID.match(name):
        return SKIP, f"既存割り当ての名前が GUID ではない ({name!r})"

    return ADOPT, name


def main() -> int:
    if len(sys.argv) not in (4, 5):
        print(f"{SKIP}\t引数の数が不正")
        return 2
    action, detail = decide(
        ca_status=sys.argv[1],
        openai_status=sys.argv[2],
        role_status=sys.argv[3],
        role_name=sys.argv[4] if len(sys.argv) == 5 else "",
    )
    print(f"{action}\t{detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
