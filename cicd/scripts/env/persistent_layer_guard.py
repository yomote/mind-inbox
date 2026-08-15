#!/usr/bin/env python3
"""撤収 (cleanup-env.sh) が「消してはいけないもの」に触れないようにするための判定。

ADR 0056 / Issue #302 (層の定義は ADR 0056。ADR 0046 D1 は supersede 済み / 2026-08-15 発効)。
**ファイル名 (`persistent_layer_guard.py`) は初版の
「持続層」という呼び方が残ったもの**で、中身は下の 2 種類を守るガード。名前を
変えると呼び出し側 (cleanup-env.sh) と Runbook の参照が同時に動くので据え置いてある。

## 何を止めるのか (2 種類あり、意味が違う)

**1. 管理系 (management) — 恒久的なルール。**
システムを運用するためのもの (Key Vault / Log Analytics / バックアップ Storage / 予算)。
アプリの生死と無関係なので、環境を作り直しても壊さない。**管理系 RG
(`rg-mgmt-mindbox`) はどのフラグでも削除できない** — これが層分断の実体。

**2. 復元が実証されていないデータ — 暫定的なルール。**
Cosmos はアプリ系 (`rg-{env}-mind-inbox`) に居るのが**正しい姿**で、PO 設計では
「守る」のではなく「バックアップから戻せるようにする」(#302 の 2026-08-12 コメント /
2026-08-14 の PO 裁定)。ただし **ADR 0018「復元したことのないバックアップは
バックアップではない」**により、空の Cosmos への復元を 1 回通すまでは、
データを裸で消せる状態にしない。そこで**バックアップ + 復元の実証 (ADR 0046 D9) が
済むまでの暫定措置として、Cosmos が居る RG の撤収を拒否する**。

  ⚠️ **実証が済んだらこの分岐は残さない。** `DATA_BEARING_RESOURCE_TYPES` による
  一律拒否をやめ、「**直近のバックアップが十分に新しいなら通す**」(鮮度の確認) に
  差し替える。差し替えないまま実証だけ済ませると、週次プロビジョンテスト
  (ADR 0046 D9) が毎回 override を必要とし、逃げ道が常用になってガードが死ぬ。
  手順は docs/runbooks/mgmt-layer-apply.md の「撤収ガードとの関係」に書いてある。

**OpenAI / Speech は止めない。** アプリそのものであり、データを持たない (PO 整理:
Speech は実質ロスなし / OpenAI はクォータ取り直しの不確実性のみ)。ただし
**黙って通しはしない** — 何を「失ってよい」と判断したかを notes に出す。

判定だけを純粋関数 (`decide`) に切り出してある (cicd/CLAUDE.md「判定ロジックを
シェルや workflow の中に埋めない」)。シェル側は az を叩いて材料を集めるだけ。

## 何を管理系と見なすか (2 段)

**型だけでは判定できない。** Key Vault はアプリ系にも居る (`bootstrap-core.bicep` の
SQL 管理者パスワード用 vault)、Storage もアプリ系に居る (Function App の実行 storage)、
Log Analytics も同様。型で一括りにすると、正当なアプリ系の撤収まで常に拒否されて
`ALLOW_PROTECTED_DELETE=true` が常用になり、**ガードが意味を失う**。逆に型を外すと
バックアップ Storage が黙って消える。そこで:

1. **層タグ** (`mindInboxLayer=management`) — `main-mgmt.bicep` が全リソースに刻む。
   **型を問わず管理系**。誤ってアプリ系 RG へ mgmt を流した場合もここで捕まる。
2. **名前の名指し** (`--protected-name`) — タグの無い移行前のリソースを守る逃げ道。

どちらにも当たらない「両層に出る型」(Key Vault / Storage / Log Analytics) は
**アプリ系として通すが、黙って通さない** — 何をアプリ系と見なしたかを notes に出す
(「取れなかったものを異常なしと書かない」の同類。判定の根拠を見えるようにする)。

## 1 回の判定では足りないもの (状態遷移)

撤収は破壊系の手前で**何度も**判定する (TOCTOU)。このとき「前回は不在だった RG が
今回は存在する」= **このスクリプトの外で誰かが作り直した RG** で、中身を一度も
検証していないので触ってはいけない。これは 1 回分の材料では判定できないので、
**過去の判定コードを `previous_codes` で渡す**。呼び出し側は判定コードを溜めて
渡すだけで、比較はここでやる (シェルの `if` に判定を置かない / cicd/CLAUDE.md)。

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
from collections.abc import Iterable
from typing import Any

# **暫定**: アプリ系に居るのが正しいが、中身を復元できると実証するまで撤収を止める型。
# キーは小文字化した ARM の type。値は「何を失うか / なぜ暫定か」(拒否メッセージに出す)。
# ADR 0046 D9 の復元実証が済んだら、この一律拒否をバックアップ鮮度の確認に差し替える。
DATA_BEARING_RESOURCE_TYPES: dict[str, str] = {
    "microsoft.documentdb/databaseaccounts": (
        "Cosmos DB — 蓄積されたユーザーデータ (Problem / Mention)。"
        "アプリ系に居るのは正しいが、バックアップからの復元をまだ 1 回も通していない "
        "(ADR 0046 D9 / ADR 0018) ので、暫定的に撤収を止める"
    ),
}

# 両層に出る型。**型だけでは管理系と断定できない**ので、層タグか名指しでのみ拾う。
# 値は「管理系側だったら何を失うか」。
LAYER_AMBIGUOUS_TYPES: dict[str, str] = {
    "microsoft.keyvault/vaults": "Key Vault — E2E trace の復号鍵 (非エクスポート / ADR 0045 D5)",
    "microsoft.storage/storageaccounts": "Storage — Cosmos バックアップの保管先 (ADR 0046 D9)",
    "microsoft.operationalinsights/workspaces": "Log Analytics — 監査ログの履歴 (ADR 0056 D1)",
}

# **アプリ系と判断して撤収を止めない型**。止めないが黙りもしない — 撤収で何を
# 失う (かもしれない) かを notes に出す。ここを空にすると「止めないという判断」が
# 記録から消え、後から「見落としだったのか判断だったのか」が分からなくなる。
APP_LAYER_NOTABLE_TYPES: dict[str, str] = {
    "microsoft.cognitiveservices/accounts": (
        "OpenAI / Speech — アプリそのものなのでアプリ系 (#302 の PO 整理)。"
        "データは持たないが、再作成でクォータ / F0 枠 (1 サブスクに 1 つ) を"
        "取り直せるかは未検証"
    ),
}

# `main-mgmt.bicep` が刻む層タグ。ここが判定の一次ソース。
LAYER_TAG_KEY = "mindinboxlayer"
LAYER_TAG_MANAGEMENT_VALUE = "management"

# **恒久的に撤収できない RG。既定名はどんな設定でも保護対象から外れない。**
#
# ここを「呼び出し側が渡した 1 個の名前」にしていると、`MGMT_RG` を別名に向けるだけで
# 管理系 RG そのものが `target-is-management-rg` に落ちなくなり、層タグの findings も
# `ALLOW_PROTECTED_DELETE=true` で override されて `az group delete` が通る
# (2026-08-14 の内部 judge が実測。逃げ道の無いはずの保護が可変値 1 つに依存していた)。
# **`--mgmt-rg` は「足す」だけで、この既定名を置き換えられない。**
DEFAULT_MANAGEMENT_RG = "rg-mgmt-mindbox"

# findings の種類。**管理系 (恒久) と データ (暫定) を混ぜない** — 混ぜると、
# 復元実証が済んだときにどちらを緩めてよいのかがコードから読めなくなる。
KIND_MANAGEMENT = "management"
KIND_DATA = "data"

# 判定コードのうち、呼び出し側と状態遷移の判定で参照するもの。**文字列リテラルを
# シェル側に散らさない**ため定数にしてある (散らすと、片方だけ変えても誰も気づけない)。
CODE_RG_ABSENT = "rg-absent"
CODE_RG_REAPPEARED = "rg-reappeared-after-absent"


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
        lines = [f"[layer-guard] {head} ({self.code}): {self.reason}"]
        lines.extend(f"  - {f}" for f in self.findings)
        lines.extend(f"  # {n}" for n in self.notes)
        return "\n".join(lines)


def normalize_rg(name: str) -> str:
    """RG 名は Azure 側で大文字小文字を区別しないので、比較前に揃える。"""
    return name.strip().lower()


def management_rg_set(extra: Iterable[str] | str | None = None) -> frozenset[str]:
    """恒久的に撤収できない RG の集合。**既定名を必ず含む (追加専用)**。

    無いと: 呼び出し側が `--mgmt-rg` を別名に向けた瞬間、`rg-mgmt-mindbox` が
    「ただの RG」に落ちる。層タグの findings は override で通るので、
    「どのフラグでも消せない」という唯一逃げ道の無い保護が消える。
    """
    names = {normalize_rg(DEFAULT_MANAGEMENT_RG)}
    if extra is None:
        return frozenset(names)
    if isinstance(extra, str):
        extra = [extra]
    for name in extra:
        normalized = normalize_rg(str(name))
        if normalized:
            names.add(normalized)
    return frozenset(names)


def classify(
    resources: list[Any],
    protected_names: list[str] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """RG の中身を「管理系 / 復元未実証データ / アプリ系」に分ける。

    返り値は (management, data, notes):
      management … 管理系と判定したもの (恒久的に撤収を止める)
      data       … 復元を実証していないデータを持つもの (暫定的に撤収を止める)
      notes      … アプリ系として通したが、何をそう見なしたかを残すもの

    重複は畳み、入力順を保つ。
    """
    wanted_names = {n.strip().lower() for n in (protected_names or []) if n.strip()}
    management: dict[str, str] = {}
    data: dict[str, str] = {}
    notes: dict[str, str] = {}

    for item in resources:
        record = as_record(item)
        key = f"{record.type_key}|{record.name_key}"

        # 1. 層タグ — 型を問わず管理系。
        if record.layer_tag() == LAYER_TAG_MANAGEMENT_VALUE:
            reason = LAYER_AMBIGUOUS_TYPES.get(
                record.type_key, "管理系として宣言されたリソース"
            )
            management.setdefault(key, f"{record.label()} — {reason} (層タグ)")
            continue

        # 2. 名指し — 層タグを刻む前 (mgmt bicep 未適用) のリソースを守る逃げ道。
        if record.name_key and record.name_key in wanted_names:
            reason = LAYER_AMBIGUOUS_TYPES.get(
                record.type_key, "管理系として名指しされたリソース"
            )
            management.setdefault(key, f"{record.label()} — {reason} (名指し)")
            continue

        # 3. 復元を実証していないデータ (暫定)。**管理系ではない** — アプリ系に
        #    居るのが正しく、ADR 0046 D9 が済んだら鮮度の確認に差し替える分岐。
        data_label = DATA_BEARING_RESOURCE_TYPES.get(record.type_key)
        if data_label is not None:
            data.setdefault(key, f"{record.label()} — {data_label} (型 / 暫定)")
            continue

        ambiguous_label = LAYER_AMBIGUOUS_TYPES.get(record.type_key)
        if ambiguous_label is not None:
            notes.setdefault(
                key,
                f"{record.label()} は層タグ ({LAYER_TAG_KEY}={LAYER_TAG_MANAGEMENT_VALUE}) も"
                "名指しも無いため**アプリ系**として扱った"
                " (管理系なら PROTECTED_RESOURCE_NAMES に足すか mgmt を流し直してタグを刻むこと)",
            )
            continue

        app_label = APP_LAYER_NOTABLE_TYPES.get(record.type_key)
        if app_label is not None:
            notes.setdefault(key, f"{record.label()} は撤収で消える — {app_label}")
            continue

    return list(management.values()), list(data.values()), list(notes.values())


def _decide_for_current_state(
    *,
    target_rg: str,
    extra_management_rgs: Iterable[str] | str | None = None,
    rg_exists: bool | None = True,
    resources: list[Any] | None = None,
    deleted_resources: list[Any] | None = None,
    protected_names: list[str] | None = None,
    allow_protected: bool = False,
) -> Decision:
    """**今この瞬間の材料だけ**で撤収してよいかを判定する。

    過去の判定 (状態遷移) は見ない。それは `decide` が重ねる。

    引数:
      target_rg:        撤収しようとしている RG。
      extra_management_rgs: 管理系として**追加で**守る RG 名。既定名
                        (`rg-mgmt-mindbox`) は常に守られ、ここで外せない。
      rg_exists:        target_rg が実在するか。**確認そのものに失敗したときは None**
                        を渡すこと (False と区別する — False は「調べたら無かった」)。
      resources:        target_rg の中身 (str か {type,name,tags})。**取得に失敗した
                        ときは None** を渡すこと (空リストと区別する)。
      deleted_resources: **soft-delete 済み**のリソース。purge (= 唯一の復旧手段を
                        恒久的に消す処理) を有効にしたときだけ渡す。取得に失敗した
                        ときは None。
      protected_names:  層タグの無い管理系リソースの名指し。
      allow_protected:  運用者が明示的に許可したか。

    判定の順序に意味がある。上から順に:
      1. 管理系 RG 自体   → **常に拒否**。override でも通さない (これが層分断の実体)
      2. soft-delete 済みの保護対象 → 拒否 (override 可)。**RG の存在とは独立**に見る —
         purge は RG が消えたあとに走るので、ここを後ろに置くと 4 で素通りする
      3. 存在を確かめられない → 拒否。「確かめられなかった」を「無い」に読み替えない
      4. RG が無い        → 消すものが無いので許可 (cleanup-env.sh の冪等性を壊さない)
      5. 中身が取れない   → 拒否。「確かめられなかった」を「保護対象なし」に読み替えない
      6. 管理系が居る     → 拒否 (override 可) / 恒久
      7. 復元未実証のデータが居る → 拒否 (override 可) / **暫定** (ADR 0046 D9 まで)
    """
    protected_rgs = management_rg_set(extra_management_rgs)
    if normalize_rg(target_rg) in protected_rgs:
        return Decision(
            allowed=False,
            code="target-is-management-rg",
            reason=(
                f"{target_rg} は管理系の RG です。撤収の対象はアプリ系だけで、"
                "管理系はどのフラグでも削除できません (ADR 0056 D1)。"
            ),
            notes=(
                "恒久的に撤収できない RG: "
                + " / ".join(sorted(protected_rgs))
                + f" (既定 {DEFAULT_MANAGEMENT_RG} は --mgmt-rg では外せない)",
            ),
        )

    # soft-delete 済みの保護対象。**`az resource list` には出ない**ので、live の判定
    # だけでは purge を止められない。RG の存在確認より前に見るのは、purge が
    # 「RG を消したあと」に走る処理だから — 後ろに置くと rg-absent で素通りする。
    if deleted_resources is not None:
        del_mgmt, del_data, del_notes = classify(deleted_resources, protected_names)
        deleted_findings = [*del_mgmt, *del_data]
        if deleted_findings:
            if allow_protected:
                return Decision(
                    allowed=True,
                    code="protected-soft-deleted-overridden",
                    reason=(
                        "soft-delete 済みの保護対象を purge しようとしていますが、"
                        "運用者が明示的に許可したため続行します。"
                        "**purge した soft-delete は二度と戻りません。**"
                    ),
                    findings=tuple(deleted_findings),
                    notes=tuple(del_notes),
                )
            return Decision(
                allowed=False,
                code="protected-soft-deleted-present",
                reason=(
                    "purge の対象に管理系 / 復元未実証データの soft-delete が含まれています。"
                    "**purge は唯一の復旧手段を恒久的に消す**ので拒否します。"
                    "衝突していない種類の purge フラグを外すか、本当に捨てるなら "
                    "ALLOW_PROTECTED_DELETE=true を明示してください。"
                ),
                findings=tuple(deleted_findings),
                notes=tuple(del_notes),
            )

    # 存在確認そのものが失敗したとき。**ここを「不在」に潰すと、中身 (inventory) を
    # 一度も検証しないままガードを通過し、後段の再確認が成功すれば削除に進んでしまう。**
    if rg_exists is None:
        if allow_protected:
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
            code=CODE_RG_ABSENT,
            reason=f"{target_rg} は存在しません (削除するものが無い)。",
        )

    if resources is None:
        if allow_protected:
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
                "「保護対象は無い」と読み替えない**ため拒否します。az にログインしているか、"
                "RG を読む権限があるかを確認してください。"
            ),
        )

    management, data, notes = classify(resources, protected_names)

    # 管理系が先。**恒久のルールと暫定のルールを混ぜない** — 拒否コードが違えば、
    # 「復元実証が済んだら消える拒否」なのかがログだけで分かる。
    if management:
        if allow_protected:
            return Decision(
                allowed=True,
                code="management-resources-present-overridden",
                reason=(
                    f"{target_rg} に管理系のリソースが居ますが、"
                    "運用者が明示的に許可したため続行します。**消えたものは戻りません。**"
                ),
                findings=tuple(management),
                notes=tuple(notes),
            )
        return Decision(
            allowed=False,
            code="management-resources-present",
            reason=(
                f"{target_rg} に管理系 (運用のためのもの) のリソースが残っています。"
                f"先に管理系 RG ({DEFAULT_MANAGEMENT_RG}) へ移してから撤収してください "
                "(Issue #302)。"
            ),
            findings=tuple(management),
            notes=tuple(notes),
        )

    if data:
        if allow_protected:
            return Decision(
                allowed=True,
                code="data-restore-unproven-overridden",
                reason=(
                    f"{target_rg} に復元を実証していないデータが居ますが、"
                    "運用者が明示的に許可したため続行します。**消えたものは戻りません。**"
                ),
                findings=tuple(data),
                notes=tuple(notes),
            )
        return Decision(
            allowed=False,
            code="data-restore-unproven",
            reason=(
                f"{target_rg} にユーザーデータを持つリソースが居ます。**これはアプリ系の"
                "正しい姿**ですが、バックアップからの復元をまだ 1 回も通していないため "
                "(ADR 0046 D9 / ADR 0018)、暫定的に撤収を拒否します。"
                "復元を 1 回通したらこの拒否はバックアップ鮮度の確認に差し替えます "
                "(docs/runbooks/mgmt-layer-apply.md)。"
            ),
            findings=tuple(data),
            notes=tuple(notes),
        )

    return Decision(
        allowed=True,
        code="ok",
        reason=f"{target_rg} に管理系 / 復元未実証データのリソースはありません。",
        notes=tuple(notes),
    )


def saw_rg_absent(previous_codes: list[str] | None) -> bool:
    """過去の判定に「RG は不在」が含まれるか。

    呼び出し側は判定コードを溜めて渡すだけで、この比較には関与しない
    (ここを呼び出し側の `if` に置くと、壊してもテストが落ちない)。
    """
    return any(str(code).strip() == CODE_RG_ABSENT for code in (previous_codes or []))


def decide(
    *,
    target_rg: str,
    extra_management_rgs: Iterable[str] | str | None = None,
    rg_exists: bool | None = True,
    resources: list[Any] | None = None,
    deleted_resources: list[Any] | None = None,
    protected_names: list[str] | None = None,
    allow_protected: bool = False,
    previous_codes: list[str] | None = None,
) -> Decision:
    """今の材料での判定に、**過去の判定との遷移**を重ねて最終判定を出す。

    `previous_codes` は同じ撤収実行の中でこれより前に出た判定コード (古い順)。

    重ねる遷移は 1 つだけ:

      **「不在」で通したあとに RG が不在でなくなった → 拒否。**

    一度 `rg-absent` で通っている = **その RG の中身を一度も検証していない**。
    そのあと RG が現れたら、それは撤収対象ではなく別の誰かが (provision などで)
    作った RG なので、中身が空に見えても消してはいけない。
    **これは `allow_protected` でも通さない** — override は「保護対象を承知で捨てる」
    ためのもので、「他人の RG を無検証で消す」ためのものではない。

    遷移の判定に**判定コードの不一致ではなく `rg_exists` を使う**のは、purge が
    RG 削除の後に走るため: そこでは前回も今回も RG は不在で、コードだけを見比べると
    (soft-delete 側の判定コードが返るだけで) 現れてもいない RG を「再出現」と誤認する。
    見たいのは「不在 → 不在でなくなった」という**存在の遷移**そのもの。
    """
    decision = _decide_for_current_state(
        target_rg=target_rg,
        extra_management_rgs=extra_management_rgs,
        rg_exists=rg_exists,
        resources=resources,
        deleted_resources=deleted_resources,
        protected_names=protected_names,
        allow_protected=allow_protected,
    )

    # 今の材料で既に拒否なら、そちらの理由を残す (どちらにせよ削除には進まない)。
    if not decision.allowed:
        return decision

    # rg_exists is False = 今も不在 (遷移なし)。None = 確かめられなかった —
    # **「確かめられなかった」を「まだ不在のはず」に読み替えない**ので拒否側に倒す。
    if not saw_rg_absent(previous_codes) or rg_exists is False:
        return decision

    return Decision(
        allowed=False,
        code=CODE_RG_REAPPEARED,
        reason=(
            f"{target_rg} はこの実行の中で一度「不在」と判定されましたが、"
            "今は不在ではありません。**不在として通した RG は中身を検証していない**ので、"
            "撤収の途中で作り直された RG (並行 provision など) を無検証で消さないよう拒否します。"
            "まだ消したいなら、このスクリプトを最初から流し直してください "
            "(ALLOW_PROTECTED_DELETE では通りません)。"
        ),
        findings=decision.findings,
        notes=(
            *decision.notes,
            f"今の材料だけなら {decision.code} で通っていた判定です",
        ),
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
        "--mgmt-rg",
        action="append",
        default=[],
        help=(
            f"管理系として**追加で**守る RG (繰り返し可)。既定の {DEFAULT_MANAGEMENT_RG} は "
            "常に守られ、このフラグでは外せない"
        ),
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
        "--deleted-inventory",
        action="append",
        default=[],
        help="soft-delete 済みリソースの一覧 (JSON 配列 / 繰り返し可)。purge を有効にしたときだけ渡す",
    )
    parser.add_argument(
        "--deleted-inventory-unavailable",
        action="store_true",
        help="soft-delete 済み一覧の取得に失敗した (空とは区別する)",
    )
    parser.add_argument(
        "--protected-name",
        action="append",
        default=[],
        help="層タグの無い管理系リソースを名指しする (繰り返し可)",
    )
    parser.add_argument(
        "--previous-code",
        action="append",
        default=[],
        help=(
            "同じ撤収実行の中でこれより前に出た判定コード (古い順 / 繰り返し可)。"
            "呼び出し側は溜めて渡すだけでよく、遷移の判定はこちらでする"
        ),
    )
    parser.add_argument(
        "--allow-protected",
        action="store_true",
        help="管理系 / 復元未実証データが居ても続行する (取り返しがつかない)",
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
                f"[layer-guard] inventory を読めませんでした: {exc}",
                file=sys.stderr,
            )
            resources = None

    # soft-delete 済み。**取得に失敗したら空ではなく「保護対象かもしれない」側に倒す** —
    # 取れなかったものを「保護対象は無い」と読み替えると、purge が黙って通る。
    deleted_resources: list[Any] | None
    if args.deleted_inventory_unavailable:
        deleted_resources = [
            ResourceRecord(
                type="(unknown)",
                name="soft-delete 一覧を取得できませんでした",
                tags={LAYER_TAG_KEY: LAYER_TAG_MANAGEMENT_VALUE},
            )
        ]
    elif args.deleted_inventory:
        deleted_resources = []
        for raw in args.deleted_inventory:
            try:
                deleted_resources.extend(_parse_inventory(raw))
            except (json.JSONDecodeError, ValueError) as exc:
                print(
                    f"[layer-guard] deleted-inventory を読めませんでした: {exc}",
                    file=sys.stderr,
                )
                deleted_resources.append(
                    ResourceRecord(
                        type="(unparseable)",
                        name="soft-delete 一覧を読めませんでした",
                        tags={LAYER_TAG_KEY: LAYER_TAG_MANAGEMENT_VALUE},
                    )
                )
    else:
        # purge を有効にしていない = soft-delete には触らないので見る必要が無い。
        deleted_resources = None

    decision = decide(
        target_rg=args.target_rg,
        extra_management_rgs=args.mgmt_rg,
        rg_exists=rg_exists,
        resources=resources,
        deleted_resources=deleted_resources,
        protected_names=args.protected_name,
        allow_protected=args.allow_protected,
        previous_codes=args.previous_code,
    )
    print(decision.render(), file=sys.stderr)
    # 判定コードは **stdout** に出す。呼び出し側 (cleanup-env.sh) は許可/拒否の 2 値
    # だけでは足りない — 「不在だから通した」のか「中身を見て通した」のかで、
    # そのあと RG が現れたときの扱いが変わる。人間向けの本文は stderr のまま。
    print(decision.code)
    return 0 if decision.allowed else 3


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
