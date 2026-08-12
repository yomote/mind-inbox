#!/usr/bin/env python3
"""GitHub 設定の宣言と現実の差分計算 (Issue #344) — **純関数だけ**。

分担 (review-gate / security-sweep と同じ): I/O は workflow と sync.py が持ち、
判定はここに閉じる。`gh api` を呼ぶコードはこのファイルに 1 行も無い。

ここが持つもの:
    - 宣言 (settings.yml を読んだ dict) の検証 — 未知キー / 書き漏らしを落とす
    - GitHub API の応答形 → 宣言と同じ形への正規化 (GET と PUT で形が違う)
    - 差分 (Finding) の算出と、差分が「強める / 弱める」のどちらかの判定
    - 適用計画 (Operation) の組み立て — **順序が安全性を持つ** (下記)
    - 計画のダイジェスト — 「PO が見た差分」と「実際に適用される差分」の同一性を機械で縛る
    - スナップショット (事実) とレポート (markdown) の描画

規律:
    - **取れなかったものを合格と書かない** — 読めなかった項目は「一致」にせず
      `unavailable` として残し、差分ゼロにも計画にも入れない (未検証として報告する)
    - **静かに弱めない** — 保護を外す方向の操作は必ず `weaken` として分類され、
      apply は明示の許可 (allow_weakening) が無い限り実行しない
    - **比較していない領域を毎回明示する** (NOT_COMPARED) — 「差分 0 = 全部宣言どおり」と
      読ませない (security-sweep の UNCOVERED と同じ規律)

順序の設計 (中途半端な適用で「弱いまま止まる」ことを防ぐ):
    1. **強める操作を先に、弱める操作を後に**やる。途中で落ちても、落ちた時点の
       リポジトリは「宣言より弱い状態」にはなっていない
    2. 強める操作の中では op_id の昇順 = API の依存順 (secret scanning →
       push protection / dependabot alerts → security updates)
    3. 弱める操作の中では op_id の降順 = 依存の逆順 (push protection を外してから
       secret scanning を外す)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1

# 差分の向き
STRENGTHEN = "strengthen"
WEAKEN = "weaken"

# 項目の極性: POSITIVE = 値が「大きい / true / enabled」ほど保護が強い、
# NEGATIVE = true にするほど緩む (allow_force_pushes 等)。
POSITIVE = "positive"
NEGATIVE = "negative"

ENABLED = "enabled"
DISABLED = "disabled"
CONFIGURED = "configured"
NOT_CONFIGURED = "not-configured"
NONE = "none"
# 宣言でだけ使える値: 「この項目はこの仕組みで管理しない」。
# 比較も適用もしない代わりに、**レポートに毎回名指しで出る** (黙って見ないのとは違う)。
# 使いどころは「この環境では原理的に読めない / 触れない」機能 (プランで使えない等)。
UNMANAGED = "unmanaged"

# この仕組みが比較していない領域。検査を足したらここから消す
# (security-sweep の UNCOVERED と同じ運用 — 「差分 0 = 安全」と読ませない)。
NOT_COMPARED = [
    "CodeQL の検出言語・クエリスイート・実行スケジュール "
    "(default setup の state だけを比較している。languages は GitHub 側が検出して増える)",
    "push 制限の中身 (誰・どのチーム・どの App を許すか)。"
    "public リポジトリの宣言に個人名を書かないため、**制限の有無だけ**を比較している",
    "ruleset (新しい方の仕組み)。このリポジトリは従来のブランチ保護を使っており、"
    "ruleset が別に存在してもここには出ない",
    "required_signatures (署名コミットの強制) / merge queue の設定 / "
    "リポジトリ全体のマージ方式 (squash のみ許可・auto-merge 許可など)。"
    "v1 の適用対象外 — 触らないので黙って変わることも無い",
    "Actions の権限設定 (GITHUB_TOKEN の既定権限 / 許可する action の範囲)・"
    "environments・secrets/variables の一覧。v1 の適用対象外",
    "collaborator と権限 (誰が admin か)。宣言に個人を書かない方針のため対象外",
]

# --- 宣言スキーマ -----------------------------------------------------------

SECURITY_FIELDS: dict[str, tuple[str, str]] = {
    # name: (choices, polarity)  ※ choices の先頭が「弱い」側
    "secret_scanning": ((DISABLED, ENABLED), POSITIVE),
    "secret_scanning_push_protection": ((DISABLED, ENABLED), POSITIVE),
    "dependabot_alerts": ((DISABLED, ENABLED), POSITIVE),
    "dependabot_security_updates": ((DISABLED, ENABLED), POSITIVE),
    "code_scanning_default_setup": ((NOT_CONFIGURED, CONFIGURED), POSITIVE),
}

# ブランチ保護の bool 項目 (PUT のトップレベルに送るもの) と極性。
BRANCH_BOOL_FIELDS: dict[str, str] = {
    "enforce_admins": POSITIVE,
    "required_linear_history": POSITIVE,
    "required_conversation_resolution": POSITIVE,
    "block_creations": POSITIVE,
    "lock_branch": POSITIVE,
    "allow_force_pushes": NEGATIVE,
    "allow_deletions": NEGATIVE,
    "allow_fork_syncing": NEGATIVE,
}

# null を取りうるグループ (「設定なし」と「設定あり」で意味が変わる)。
BRANCH_GROUPS: dict[str, tuple[str, ...]] = {
    "required_status_checks": ("strict", "contexts"),
    "required_pull_request_reviews": (
        "required_approving_review_count",
        "dismiss_stale_reviews",
        "require_code_owner_reviews",
        "require_last_push_approval",
    ),
}

# グループ内の項目の極性。
GROUP_FIELD_POLARITY: dict[str, str] = {
    "required_status_checks.strict": POSITIVE,
    "required_status_checks.contexts": POSITIVE,  # 集合。増える = 強める
    "required_pull_request_reviews.required_approving_review_count": POSITIVE,
    "required_pull_request_reviews.dismiss_stale_reviews": POSITIVE,
    "required_pull_request_reviews.require_code_owner_reviews": POSITIVE,
    "required_pull_request_reviews.require_last_push_approval": POSITIVE,
}

BRANCH_REQUIRED_KEYS = (
    frozenset(BRANCH_BOOL_FIELDS)
    | frozenset(BRANCH_GROUPS)
    | {"protected", "restrictions"}
)


class DeclarationError(ValueError):
    """宣言ファイルが期待した形をしていない (= 実行してはいけない)。"""


# --- 値の扱い ---------------------------------------------------------------


@dataclass(frozen=True)
class Unavailable:
    """読み取れなかった項目。**一致にも差分にもしない** (未検証として残す)。"""

    reason: str

    def as_json(self) -> dict[str, str]:
        return {"unavailable": self.reason}


def _rank(value: Any) -> float:
    """「保護の強さ」の順序。比較できない値は同値として扱う。"""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if value in (ENABLED, CONFIGURED):
        return 1.0
    if value in (DISABLED, NOT_CONFIGURED, NONE):
        return 0.0
    if value is None:
        return 0.0
    return 0.0


def direction_of(declared: Any, actual: Any, polarity: str) -> str:
    """宣言が現実より強いか弱いか。集合 (contexts) は包含で判定する。

    どちらとも言えない差 (集合の入れ替え・enum の横移動) は **weaken に倒す** —
    安全側に倒しておけば、少なくとも「気づかないうちに緩む」経路は塞がる。
    """
    if isinstance(declared, tuple) or isinstance(actual, tuple):
        d = set(declared or ())
        a = set(actual or ())
        if d > a:
            return STRENGTHEN
        return WEAKEN
    # グループの有無 (None ↔ "configured") も rank で扱える
    if _rank(declared) > _rank(actual):
        return STRENGTHEN if polarity == POSITIVE else WEAKEN
    if _rank(declared) < _rank(actual):
        return WEAKEN if polarity == POSITIVE else STRENGTHEN
    return WEAKEN


@dataclass(frozen=True)
class Finding:
    """1 項目の差分。"""

    path: str
    declared: Any
    actual: Any
    direction: str


@dataclass(frozen=True)
class Operation:
    """1 回の API 呼び出しで適用できる単位。

    op_id は**先頭の数字が実行順**。数字の意味:
        01 = ブランチ保護 (最も価値のある保護を先に立てる)
        10..14 = セキュリティ機能 (**この中は API の依存順**。
                 secret scanning → push protection / alerts → security updates)
    弱めるときはこの逆順に実行する (無効化は依存の逆順でないと API が拒む)。
    並べ替えの鍵であり、ダイジェストの一部でもあるので気軽に変えない。
    """

    op_id: str
    target: str  # "security.<name>" / "branch_protection.<branch>"
    risk: str  # STRENGTHEN / WEAKEN
    summary: str
    method: str
    endpoint: str
    payload: dict | None = None
    findings: tuple[Finding, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Report:
    matched: tuple[str, ...]
    findings: tuple[Finding, ...]
    unavailable: tuple[tuple[str, str], ...]  # (path, reason)
    unmanaged: tuple[str, ...] = ()  # 宣言が unmanaged と言っている項目

    @property
    def in_sync(self) -> bool:
        return not self.findings

    @property
    def complete(self) -> bool:
        """全項目を読めたか。読めなかった項目があるなら「一致」と言い切らない。"""
        return not self.unavailable


# --- 宣言の検証 -------------------------------------------------------------


def validate_declaration(doc: Any) -> dict:
    """settings.yml を読んだ dict を検証して返す。

    未知キー・書き漏らし・型違いはすべて例外にする。**黙って無視しない** —
    typo (`secret_scaning`) を無視すると「宣言したつもりの設定が一度も適用されない」
    という、最も気づきにくい壊れ方になる。
    """
    if not isinstance(doc, dict):
        raise DeclarationError("宣言の最上位が map ではありません")
    if doc.get("version") != SCHEMA_VERSION:
        raise DeclarationError(
            f"version は {SCHEMA_VERSION} のみ対応です (実際: {doc.get('version')!r})"
        )
    unknown = set(doc) - {"version", "security", "branch_protection"}
    if unknown:
        raise DeclarationError(f"未知のキー: {', '.join(sorted(unknown))}")

    security = doc.get("security")
    if not isinstance(security, dict):
        raise DeclarationError("security が map ではありません")
    missing = set(SECURITY_FIELDS) - set(security)
    if missing:
        raise DeclarationError(f"security の書き漏らし: {', '.join(sorted(missing))}")
    extra = set(security) - set(SECURITY_FIELDS)
    if extra:
        raise DeclarationError(f"security の未知のキー: {', '.join(sorted(extra))}")
    for name, (choices, _polarity) in SECURITY_FIELDS.items():
        if security[name] not in (*choices, UNMANAGED):
            raise DeclarationError(
                f"security.{name} は {' / '.join((*choices, UNMANAGED))} のいずれかです "
                f"(実際: {security[name]!r})"
            )

    branches = doc.get("branch_protection")
    if not isinstance(branches, dict) or not branches:
        raise DeclarationError("branch_protection が map ではないか空です")
    for branch, spec in branches.items():
        _validate_branch(branch, spec)
    return doc


def _validate_branch(branch: str, spec: Any) -> None:
    where = f"branch_protection.{branch}"
    if not isinstance(spec, dict):
        raise DeclarationError(f"{where} が map ではありません")
    if not isinstance(spec.get("protected"), bool):
        raise DeclarationError(f"{where}.protected は true / false です")
    if spec["protected"] is False:
        extra = set(spec) - {"protected"}
        if extra:
            raise DeclarationError(
                f"{where}: protected: false のときは他の項目を書けません "
                f"({', '.join(sorted(extra))})"
            )
        return

    missing = BRANCH_REQUIRED_KEYS - set(spec)
    if missing:
        # 「書いていない項目は比較されない」を許すと、**書き漏らした項目が
        # PUT の既定値に静かに戻る**。全項目の明示を要求する
        raise DeclarationError(f"{where} の書き漏らし: {', '.join(sorted(missing))}")
    extra = set(spec) - BRANCH_REQUIRED_KEYS
    if extra:
        raise DeclarationError(f"{where} の未知のキー: {', '.join(sorted(extra))}")

    for name in BRANCH_BOOL_FIELDS:
        if not isinstance(spec[name], bool):
            raise DeclarationError(f"{where}.{name} は true / false です")
    if spec["restrictions"] != NONE:
        # 宣言には「誰を許すか」を書かない (public な宣言に個人名を出さない) ため、
        # **制限がある状態を宣言から復元できない**。復元できないものを宣言させると
        # 「適用しても差分が消えない」状態が固定化するので、宣言できるのは none だけ。
        # 現実に制限があるなら weaken の差分として出る (= 許可なしには外れない)
        raise DeclarationError(
            f"{where}.restrictions に書けるのは {NONE} だけです "
            "(誰を許すかの値を public な宣言に書かないため、制限の新設はこの仕組みでは扱わない)"
        )

    for group, fields in BRANCH_GROUPS.items():
        value = spec[group]
        if value is None:
            continue
        if not isinstance(value, dict):
            raise DeclarationError(f"{where}.{group} は map か null です")
        missing_f = set(fields) - set(value)
        extra_f = set(value) - set(fields)
        if missing_f:
            raise DeclarationError(
                f"{where}.{group} の書き漏らし: {', '.join(sorted(missing_f))}"
            )
        if extra_f:
            raise DeclarationError(
                f"{where}.{group} の未知のキー: {', '.join(sorted(extra_f))}"
            )
    rsc = spec["required_status_checks"]
    if rsc is not None:
        if not isinstance(rsc["strict"], bool):
            raise DeclarationError(
                f"{where}.required_status_checks.strict は bool です"
            )
        contexts = rsc["contexts"]
        if not isinstance(contexts, list) or not all(
            isinstance(c, str) for c in contexts
        ):
            raise DeclarationError(
                f"{where}.required_status_checks.contexts は文字列の配列です"
            )
    rpr = spec["required_pull_request_reviews"]
    if rpr is not None:
        count = rpr["required_approving_review_count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise DeclarationError(
                f"{where}.required_pull_request_reviews."
                "required_approving_review_count は 0 以上の整数です"
            )
        for name in (
            "dismiss_stale_reviews",
            "require_code_owner_reviews",
            "require_last_push_approval",
        ):
            if not isinstance(rpr[name], bool):
                raise DeclarationError(
                    f"{where}.required_pull_request_reviews.{name} は true / false です"
                )


# --- 正規化 (GitHub の応答形 → 宣言と同じ形) --------------------------------


def normalize_branch_protection(raw: dict | None) -> dict:
    """GET /repos/{o}/{r}/branches/{b}/protection の応答を宣言の形にそろえる。

    raw=None は 404 (保護されていない) を表す。GET と PUT で形が違う
    (`enforce_admins` は GET だと `{"enabled": bool}`、contexts は新しい API だと
    `checks: [{context: ...}]`) ため、ここを間違えると **apply した直後の check が
    「まだ差分がある」と言い続ける / 逆に適用していないのに一致と出る**。
    """
    if raw is None:
        return {"protected": False}

    out: dict[str, Any] = {"protected": True}

    rsc = raw.get("required_status_checks")
    if rsc is None:
        out["required_status_checks"] = None
    else:
        if "checks" in rsc:
            contexts = [c.get("context", "") for c in rsc.get("checks") or []]
        else:
            contexts = list(rsc.get("contexts") or [])
        out["required_status_checks"] = {
            "strict": bool(rsc.get("strict", False)),
            "contexts": sorted(contexts),
        }

    rpr = raw.get("required_pull_request_reviews")
    if rpr is None:
        out["required_pull_request_reviews"] = None
    else:
        out["required_pull_request_reviews"] = {
            "required_approving_review_count": int(
                rpr.get("required_approving_review_count", 0)
            ),
            "dismiss_stale_reviews": bool(rpr.get("dismiss_stale_reviews", False)),
            "require_code_owner_reviews": bool(
                rpr.get("require_code_owner_reviews", False)
            ),
            "require_last_push_approval": bool(
                rpr.get("require_last_push_approval", False)
            ),
        }

    for name in BRANCH_BOOL_FIELDS:
        node = raw.get(name)
        out[name] = (
            bool(node.get("enabled", False)) if isinstance(node, dict) else bool(node)
        )

    # 誰を許すかの値は取り込まない (public な宣言・スナップショットに個人名を出さない)
    out["restrictions"] = CONFIGURED if raw.get("restrictions") else NONE
    return out


def normalize_security(
    repo: dict | None,
    vulnerability_alerts: bool | Unavailable,
    automated_security_fixes: dict | None | Unavailable,
    code_scanning_default_setup: dict | None | Unavailable,
) -> dict[str, Any]:
    """各 API の応答を security の宣言と同じ語彙にそろえる。

    読めなかったものは Unavailable のまま残す (0 件・disabled と偽らない)。
    """
    out: dict[str, Any] = {}

    if isinstance(repo, Unavailable):
        out["secret_scanning"] = repo
        out["secret_scanning_push_protection"] = repo
    else:
        analysis = (repo or {}).get("security_and_analysis") or {}
        for name in ("secret_scanning", "secret_scanning_push_protection"):
            status = (analysis.get(name) or {}).get("status")
            out[name] = ENABLED if status == ENABLED else DISABLED

    if isinstance(vulnerability_alerts, Unavailable):
        out["dependabot_alerts"] = vulnerability_alerts
    else:
        out["dependabot_alerts"] = ENABLED if vulnerability_alerts else DISABLED

    if isinstance(automated_security_fixes, Unavailable):
        out["dependabot_security_updates"] = automated_security_fixes
    else:
        enabled = bool((automated_security_fixes or {}).get("enabled"))
        out["dependabot_security_updates"] = ENABLED if enabled else DISABLED

    if isinstance(code_scanning_default_setup, Unavailable):
        out["code_scanning_default_setup"] = code_scanning_default_setup
    else:
        state = (code_scanning_default_setup or {}).get("state")
        out["code_scanning_default_setup"] = (
            CONFIGURED if state == CONFIGURED else NOT_CONFIGURED
        )
    return out


# --- 差分 -------------------------------------------------------------------


def _flatten_branch(spec: dict) -> dict[str, Any]:
    """ブランチ保護を「パス → 値」に潰す。contexts は tuple にして順序差を消す。"""
    if not spec.get("protected"):
        return {"protected": False}
    out: dict[str, Any] = {"protected": True}
    for group, fields in BRANCH_GROUPS.items():
        value = spec.get(group)
        if value is None:
            out[group] = None
            continue
        out[group] = CONFIGURED
        for name in fields:
            item = value[name]
            out[f"{group}.{name}"] = (
                tuple(sorted(item)) if isinstance(item, list) else item
            )
    for name in BRANCH_BOOL_FIELDS:
        out[name] = bool(spec[name])
    out["restrictions"] = spec["restrictions"]
    return out


def _polarity(path: str) -> str:
    leaf = path.split(".", 1)[-1] if "." in path else path
    if path in GROUP_FIELD_POLARITY:
        return GROUP_FIELD_POLARITY[path]
    if leaf in BRANCH_BOOL_FIELDS:
        return BRANCH_BOOL_FIELDS[leaf]
    return POSITIVE


def diff_settings(declaration: dict, actual: dict) -> Report:
    """宣言と現実 (正規化済み) を突き合わせる。

    actual の形は declaration と同じ:
        {"security": {name: value | Unavailable},
         "branch_protection": {branch: normalized_dict | Unavailable}}
    """
    matched: list[str] = []
    findings: list[Finding] = []
    unavailable: list[tuple[str, str]] = []
    unmanaged: list[str] = []

    for name, value in sorted(declaration["security"].items()):
        path = f"security.{name}"
        current = actual.get("security", {}).get(name, Unavailable("未取得"))
        if value == UNMANAGED:
            # 比較も適用もしない。ただし「見ていない」ことをレポートに毎回出す
            unmanaged.append(path)
            continue
        if isinstance(current, Unavailable):
            unavailable.append((path, current.reason))
            continue
        if current == value:
            matched.append(path)
            continue
        polarity = SECURITY_FIELDS[name][1]
        findings.append(
            Finding(path, value, current, direction_of(value, current, polarity))
        )

    for branch, spec in sorted(declaration["branch_protection"].items()):
        current = actual.get("branch_protection", {}).get(branch, Unavailable("未取得"))
        if isinstance(current, Unavailable):
            unavailable.append((f"branch_protection.{branch}", current.reason))
            continue
        declared_flat = _flatten_branch(spec)
        actual_flat = _flatten_branch(current)
        for key in declared_flat:
            path = f"branch_protection.{branch}.{key}"
            declared_value = declared_flat[key]
            actual_value = actual_flat.get(key)
            if key not in actual_flat:
                # 宣言側にしか無いパス (現実がグループ無効など)。グループ本体の
                # 差分として既に出ているので、ここでは重ねて出さない
                continue
            if declared_value == actual_value:
                matched.append(path)
                continue
            findings.append(
                Finding(
                    path,
                    declared_value,
                    actual_value,
                    direction_of(declared_value, actual_value, _polarity(key)),
                )
            )

    return Report(tuple(matched), tuple(findings), tuple(unavailable), tuple(unmanaged))


# --- 適用計画 ---------------------------------------------------------------

# op_id の先頭の数字が実行順 (Operation の docstring 参照)。
# 強めるときは昇順、弱めるときは降順で実行する。
BRANCH_OP_PREFIX = "01-branch-protection-"
SECURITY_OPS: dict[str, str] = {
    "secret_scanning": "10-security-secret-scanning",
    "secret_scanning_push_protection": "11-security-secret-scanning-push-protection",
    "dependabot_alerts": "12-security-dependabot-alerts",
    "dependabot_security_updates": "13-security-dependabot-security-updates",
    "code_scanning_default_setup": "14-security-code-scanning-default-setup",
}


def branch_protection_payload(spec: dict) -> dict:
    """PUT /repos/{o}/{r}/branches/{b}/protection の body。

    **PUT は送らなかった項目を既定値に戻す**。ここが送る項目の集合は
    「宣言で比較している項目の集合」と一致していなければならない
    (test_settings_diff.py がこの一致を固定している)。
    restrictions は「制限あり」を宣言から復元できない (誰を許すかを書かないため)
    ので、**制限を新設する適用はしない** — 現実に制限がある状態を宣言が none と
    言っているなら、それは weaken の差分として出て、許可なしには適用されない。
    """
    rsc = spec["required_status_checks"]
    rpr = spec["required_pull_request_reviews"]
    return {
        "required_status_checks": (
            None
            if rsc is None
            else {"strict": bool(rsc["strict"]), "contexts": sorted(rsc["contexts"])}
        ),
        "enforce_admins": bool(spec["enforce_admins"]),
        "required_pull_request_reviews": (
            None
            if rpr is None
            else {
                "required_approving_review_count": int(
                    rpr["required_approving_review_count"]
                ),
                "dismiss_stale_reviews": bool(rpr["dismiss_stale_reviews"]),
                "require_code_owner_reviews": bool(rpr["require_code_owner_reviews"]),
                "require_last_push_approval": bool(rpr["require_last_push_approval"]),
            }
        ),
        "restrictions": None,
        "required_linear_history": bool(spec["required_linear_history"]),
        "allow_force_pushes": bool(spec["allow_force_pushes"]),
        "allow_deletions": bool(spec["allow_deletions"]),
        "block_creations": bool(spec["block_creations"]),
        "required_conversation_resolution": bool(
            spec["required_conversation_resolution"]
        ),
        "lock_branch": bool(spec["lock_branch"]),
        "allow_fork_syncing": bool(spec["allow_fork_syncing"]),
    }


def _security_operation(
    name: str, declared: Any, findings: tuple[Finding, ...], repo: str
) -> Operation:
    op_id = SECURITY_OPS[name]
    risk = findings[0].direction
    want = declared in (ENABLED, CONFIGURED)
    target = f"security.{name}"
    if name in ("secret_scanning", "secret_scanning_push_protection"):
        return Operation(
            op_id,
            target,
            risk,
            f"{name} を {declared} にする",
            "PATCH",
            f"repos/{repo}",
            {"security_and_analysis": {name: {"status": declared}}},
            findings,
        )
    if name == "dependabot_alerts":
        return Operation(
            op_id,
            target,
            risk,
            f"Dependabot alerts を {declared} にする",
            "PUT" if want else "DELETE",
            f"repos/{repo}/vulnerability-alerts",
            None,
            findings,
        )
    if name == "dependabot_security_updates":
        return Operation(
            op_id,
            target,
            risk,
            f"Dependabot security updates を {declared} にする",
            "PUT" if want else "DELETE",
            f"repos/{repo}/automated-security-fixes",
            None,
            findings,
        )
    return Operation(
        op_id,
        target,
        risk,
        f"CodeQL default setup を {declared} にする",
        "PATCH",
        f"repos/{repo}/code-scanning/default-setup",
        {"state": CONFIGURED if want else NOT_CONFIGURED},
        findings,
    )


def build_plan(declaration: dict, report: Report, repo: str) -> tuple[Operation, ...]:
    """差分から適用計画を作る。**順序が安全性**を持つ (module docstring 参照)。"""
    by_path: dict[str, list[Finding]] = {}
    for finding in report.findings:
        by_path.setdefault(finding.path, []).append(finding)

    ops: list[Operation] = []

    for name in SECURITY_OPS:
        found = by_path.get(f"security.{name}")
        if not found:
            continue
        ops.append(
            _security_operation(name, declaration["security"][name], tuple(found), repo)
        )

    for branch, spec in sorted(declaration["branch_protection"].items()):
        prefix = f"branch_protection.{branch}."
        found = tuple(f for f in report.findings if f.path.startswith(prefix))
        if not found:
            continue
        # 1 ブランチ = 1 回の PUT (または DELETE)。**1 つでも弱める項目があれば
        # 全体を weaken として扱う** — PUT は分割できないので安全側に倒す
        risk = WEAKEN if any(f.direction == WEAKEN for f in found) else STRENGTHEN
        target = f"branch_protection.{branch}"
        if not spec["protected"]:
            ops.append(
                Operation(
                    f"{BRANCH_OP_PREFIX}{branch}",
                    target,
                    WEAKEN,
                    f"{branch} の保護を外す",
                    "DELETE",
                    f"repos/{repo}/branches/{branch}/protection",
                    None,
                    found,
                )
            )
            continue
        ops.append(
            Operation(
                f"{BRANCH_OP_PREFIX}{branch}",
                target,
                risk,
                f"{branch} のブランチ保護を宣言どおりにする ({len(found)} 項目)",
                "PUT",
                f"repos/{repo}/branches/{branch}/protection",
                branch_protection_payload(spec),
                found,
            )
        )

    return order_plan(ops)


def order_plan(ops: list[Operation]) -> tuple[Operation, ...]:
    """強める操作を先に (依存の昇順)、弱める操作を後に (依存の降順)。"""
    strengthen = sorted((o for o in ops if o.risk == STRENGTHEN), key=lambda o: o.op_id)
    weaken = sorted(
        (o for o in ops if o.risk == WEAKEN), key=lambda o: o.op_id, reverse=True
    )
    return tuple(strengthen + weaken)


def plan_digest(plan: tuple[Operation, ...]) -> str:
    """計画の同一性を表す短い文字列。

    apply は「check が出したダイジェスト」を入力に取り、**自分が今計算した計画の
    ダイジェストと一致しなければ実行しない**。これで「PO が見た差分」と「実際に
    適用される差分」がズレる経路 (宣言の書き換え・現実の変化) が機械で塞がる。
    """
    body = json.dumps(
        [
            {
                "op_id": o.op_id,
                "risk": o.risk,
                "method": o.method,
                "endpoint": o.endpoint,
                "payload": o.payload,
            }
            for o in plan
        ],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def weakening_operations(plan: tuple[Operation, ...]) -> tuple[Operation, ...]:
    return tuple(o for o in plan if o.risk == WEAKEN)


# --- 描画 -------------------------------------------------------------------


def _fmt(value: Any) -> str:
    if isinstance(value, Unavailable):
        return f"(未検証: {value.reason})"
    if isinstance(value, tuple):
        return ", ".join(f"`{v}`" for v in value) if value else "(なし)"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return f"`{value}`"


def render_snapshot(repo: str, actual: dict) -> str:
    """事実のスナップショット (データブランチに置く JSON)。

    **毎回同じキー順で出す** (sort_keys) — 順序が揺れると差分がノイズだらけになり、
    「いつ設定が変わったか」を git log で読む価値が消える。
    **時刻を入れない** — 入れると毎回コミットが立ち、変化した回だけが立つという
    性質 (= 履歴が変更の記録そのものになる) が壊れる。いつ点検したかは run 履歴が持つ。
    """

    def value_of(v: Any) -> Any:
        if isinstance(v, Unavailable):
            return v.as_json()
        return v

    doc = {
        "schemaVersion": SCHEMA_VERSION,
        "repository": repo,
        "security": {k: value_of(v) for k, v in actual.get("security", {}).items()},
        "branch_protection": {
            branch: value_of(value)
            for branch, value in actual.get("branch_protection", {}).items()
        },
    }
    return json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def render_report(
    repo: str,
    report: Report,
    plan: tuple[Operation, ...],
    mode: str,
    applied: tuple[str, ...] = (),
    failed: tuple[tuple[str, str], ...] = (),
    skipped: tuple[str, ...] = (),
) -> str:
    """人間が読むレポート (run summary / ドリフト Issue の本文)。"""
    lines: list[str] = []
    lines.append(f"## GitHub 設定の点検 ({repo} / mode: {mode})")
    lines.append("")
    lines.append(
        f"- 宣言どおり: **{len(report.matched)} 項目** / "
        f"差分: **{len(report.findings)} 項目** / "
        f"未検証: **{len(report.unavailable)} 項目** / "
        f"管理対象外: **{len(report.unmanaged)} 項目**"
    )
    lines.append("- 宣言 (意図): `cicd/github/settings.yml`")
    lines.append("- 観測 (事実): データブランチ `data/github-settings`")
    lines.append("")

    if report.findings:
        lines.append("### 差分 (宣言 ≠ 現実)")
        lines.append("")
        lines.append("| 項目 | 宣言 | 現実 | 向き |")
        lines.append("| --- | --- | --- | --- |")
        for f in sorted(report.findings, key=lambda x: x.path):
            arrow = "強める" if f.direction == STRENGTHEN else "**弱める**"
            lines.append(
                f"| `{f.path}` | {_fmt(f.declared)} | {_fmt(f.actual)} | {arrow} |"
            )
        lines.append("")
    else:
        lines.append("### 差分")
        lines.append("")
        lines.append("宣言と現実は一致しています。")
        lines.append("")

    if report.unavailable:
        lines.append("### 未検証 (読み取れなかった項目)")
        lines.append("")
        lines.append(
            "**「一致」とは書きません。** 権限・API の可用性を確認してください。"
        )
        lines.append("")
        for path, reason in sorted(report.unavailable):
            lines.append(f"- `{path}` — {reason}")
        lines.append("")

    if report.unmanaged:
        lines.append("### 管理対象外 (宣言が `unmanaged` と言っている項目)")
        lines.append("")
        lines.append(
            "**比較も適用もしていません。** 現実がどうなっていても差分は出ません。"
        )
        lines.append("")
        for path in sorted(report.unmanaged):
            lines.append(f"- `{path}`")
        lines.append("")

    if plan:
        lines.append("### 適用計画 (この順に実行する)")
        lines.append("")
        lines.append(f"ダイジェスト: **`{plan_digest(plan)}`**")
        lines.append("")
        lines.append("| # | 操作 | 向き | API |")
        lines.append("| --- | --- | --- | --- |")
        for i, op in enumerate(plan, 1):
            arrow = "強める" if op.risk == STRENGTHEN else "**弱める**"
            lines.append(
                f"| {i} | {op.summary} | {arrow} | `{op.method} {op.endpoint}` |"
            )
        lines.append("")
        weak = weakening_operations(plan)
        if weak:
            lines.append(
                f"> ⚠️ **保護を弱める操作が {len(weak)} 件あります。** "
                "apply は `allow_weakening` を明示しない限りこれを実行しません "
                "(宣言の書き間違い・改竄で保護が静かに外れるのを防ぐため)。"
            )
            lines.append("")

    if mode == "apply":
        lines.append("### 適用の結果")
        lines.append("")
        lines.append(
            f"- 適用済み: {len(applied)} 件 / 失敗: {len(failed)} 件 / 未適用: {len(skipped)} 件"
        )
        for op_id in applied:
            lines.append(f"  - ✅ 適用済み: `{op_id}`")
        for op_id, reason in failed:
            lines.append(f"  - ❌ 失敗: `{op_id}` — {reason}")
        for op_id in skipped:
            lines.append(f"  - ⏭️ 未適用: `{op_id}`")
        lines.append("")

    lines.append("### 比較していない領域")
    lines.append("")
    lines.append(
        "差分 0 は「全部宣言どおり」ではありません。この仕組みは次を見ていません:"
    )
    lines.append("")
    for item in NOT_COMPARED:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)
