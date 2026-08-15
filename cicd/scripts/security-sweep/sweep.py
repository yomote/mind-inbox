#!/usr/bin/env python3
"""週次セキュリティスキャンの集計 (ADR 0038)。

分担: スキャナ (npm audit / pnpm audit / pip-audit / gitleaks) は workflow が回し、
このスクリプトは**出力の解釈だけ**をやる — severity 別の集計 / Issue 本文の生成 /
「回らなかったツール」の明示。判定ロジックを workflow の bash に埋めると
テストできず、「障害を隠す」「ノイズで埋める」の両方が静かに起きる
(review-gate / report-failure と同じ理由)。

規律 — **静かに嘘をつかない** (debt-check と同じ):
    - ツールの出力が読めなかったら、そのツールを「0 件」ではなく「実行できず」として
      報告する。取れなかったものを「合格」と書かない
    - カバーしていない領域 (実環境の設定監査 / 依存の悪意ある更新 …) は
      検出 0 件でも**毎回明示する**。「0 件 = 安全」と読ませない (silent caps 禁止)
    - **カバー外の宣言そのものが古びる**のも同じ嘘 — SAST は「未導入」ではなく
      別機構 (CodeQL default setup) が回している。決め打ちで「稼働中」と書くと
      止まっても気づけないので、稼働は毎回**実測を解釈して**出す (下の SAST_*)

使い方:
    sweep.py <reports_dir>
      reports_dir に各ツールの生 JSON (下の TOOLS のファイル名) を置いて呼ぶ。
      SAST の稼働実測 (code-scanning.json) も同じディレクトリから読む。
      stdout に JSON 1 個:
        {"total": int, "by_severity": {...}, "failed": [tool_id, ...],
         "issue_needed": bool, "tools": [...], "sast": {...}, "markdown": str}
      markdown は Issue 本文にそのまま使える形。診断は stderr。

終了コード: 0 = 集計できた (検出件数・ツール失敗は JSON で表す) / 1 = 前提不足
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# severity の表示順 (深刻な順)。"unknown" は pip-audit 用 — pip-audit の JSON は
# severity を含まない (アドバイザリ DB 側の属性で、出力に載らない)。
# 「不明」を moderate 等に**寄せて数えない** — 深刻度を偽装しないため独立の桁で出す。
SEVERITIES = ("critical", "high", "moderate", "low", "info", "unknown")

# 1 ツールあたり明細をここまで載せる。超過分は件数を明示して省略する
# (黙って切らない — silent caps 禁止)。
MAX_LINES_PER_TOOL = 50

# SAST の稼働実測。**この sweep は SAST を回さない**が、「未導入」でもない —
# 2026-08-12 18:42 UTC から CodeQL default setup が回っている
# (watchers.json の id 333008403 / Issue #408 §2)。ここで扱うのは
# `GET /repos/{owner}/{repo}/code-scanning/analyses?tool_name=CodeQL` の生出力で、
# workflow が取ってこのファイル名で置く。取れなければ「未検証: 理由」に落ちる —
# **文言で「稼働中」と決め打ちしない** (止まったときに嘘が緑色の顔をして残るため)。
SAST_REPORT_FILE = "code-scanning.json"
SAST_NAME = "SAST (CodeQL default setup)"
# 鮮度のしきい値は **watchers.json の同じ行と同値**を使う (下の sast_stale_hours)。
# 読めなかったときの既定値。**`cicd/scripts/status-page/watchers.json` の
# id 333008403 の `expect_hours` と揃えること** — 2 箇所で別々に育つと、状況ページは
# 「止まっている」なのに sweep は ✅ という食い違いが起きる。
SAST_STALE_HOURS_FALLBACK = 192
SAST_WATCHER_ID = "333008403"
SAST_WATCHERS_JSON = (
    Path(__file__).resolve().parents[1] / "status-page" / "watchers.json"
)
# workflow が実測 step ごと落ちた / 古い呼び出しでファイルが無い場合の既定。
# 「稼働中」でも「停止」でもなく**未検証**に倒す。
SAST_NOT_MEASURED = {
    "status": "unverified",
    "reason": f"{SAST_REPORT_FILE} が reports_dir に無い (実測 step が回っていない)",
}

# この sweep がカバーしていない領域。検出器を足したらここから消す (debt-check と同じ運用)。
UNCOVERED = [
    "SAST (コード自体の脆弱パターン — injection / SSRF 等) は **この sweep の対象外**。"
    f"回しているのは別機構の {SAST_NAME} で、稼働は上の「隣接する検査」欄に実測を出す "
    "(ツール選定の経緯は archive の旧 ADR にあり、現行ルールではない)",
    "実環境の設定監査 (Azure の EasyAuth / ingress / RBAC の実設定)。"
    "release-gate の security-reviewer と ops-inspect の領域 (ADR 0019)",
    "依存の悪意ある更新 (タイポスクワット / postinstall / lockfile への混入)。"
    "audit 系は既知 CVE の照合のみで、悪意の検出はしない (rubric S7 の目視領域)",
    "git 履歴に埋まった秘密情報 — gitleaks は HEAD スナップショットのみを見ている",
    "到達可能性・実害の判定 — 検出は「既知 CVE を含む版への依存」であり、"
    "悪用可能かの判断はしていない (dev 依存も同じ桁で数えている)。severity 判断は人が rubric でやる",
    "pip-audit は severity を出力しない — Python の findings は件数のみ (unknown 扱い)",
    "コンテナ image / ベースイメージの CVE (trivy 未導入)",
    "実行時の動的チェック (想定外の外部通信 / 認可の実測)。release-gate の領域",
]


class ParseError(ValueError):
    """ツール出力が期待した形をしていない (= そのツールは「実行できず」扱い)。"""


def _severity(raw: object) -> str:
    value = str(raw).lower()
    return value if value in SEVERITIES else "unknown"


def parse_npm_audit(data: object) -> list[dict]:
    """`npm audit --json` (auditReportVersion 2)。1 finding = 脆弱パッケージ 1 個。

    npm 自身の集計 (metadata.vulnerabilities) がパッケージ単位なので、それに合わせる。
    """
    if not isinstance(data, dict) or "vulnerabilities" not in data:
        raise ParseError("npm audit の JSON に vulnerabilities がありません")
    findings = []
    for name, entry in sorted(data["vulnerabilities"].items()):
        titles = [
            v["title"]
            for v in entry.get("via", [])
            if isinstance(v, dict) and v.get("title")
        ]
        via_packages = [v for v in entry.get("via", []) if isinstance(v, str)]
        detail = titles[0] if titles else f"経由: {', '.join(via_packages) or '不明'}"
        if entry.get("isDirect"):
            detail += " [直接依存]"
        findings.append(
            {
                "subject": name,
                "severity": _severity(entry.get("severity")),
                "detail": detail,
            }
        )
    return findings


def parse_pnpm_audit(data: object) -> list[dict]:
    """`pnpm audit --json` (classic advisories 形式)。1 finding = アドバイザリ 1 個。"""
    if not isinstance(data, dict) or "advisories" not in data:
        raise ParseError("pnpm audit の JSON に advisories がありません")
    return [
        {
            "subject": adv.get("module_name", "?"),
            "severity": _severity(adv.get("severity")),
            "detail": adv.get("title", ""),
        }
        for _, adv in sorted(data["advisories"].items())
    ]


def parse_pip_audit(data: object) -> list[dict]:
    """`pip-audit -f json`。1 finding = (パッケージ, 脆弱性 ID) 1 組。

    pip-audit の JSON は severity を含まないため全件 unknown — moderate 等に
    寄せて「深刻度が低そうに見える / 高そうに見える」の両方を避ける。
    """
    if not isinstance(data, dict) or "dependencies" not in data:
        raise ParseError("pip-audit の JSON に dependencies がありません")
    findings = []
    for dep in data["dependencies"]:
        for vuln in dep.get("vulns", []):
            fixes = ", ".join(vuln.get("fix_versions", [])) or "fix なし"
            findings.append(
                {
                    "subject": f"{dep.get('name')}=={dep.get('version')}",
                    "severity": "unknown",
                    "detail": f"{vuln.get('id')} (fix: {fixes})",
                }
            )
    return findings


def parse_gitleaks(data: object) -> list[dict]:
    """`gitleaks dir --report-format json`。leak は全件 critical (秘密のコミット = rubric S1)。"""
    if not isinstance(data, list):
        raise ParseError("gitleaks の JSON が配列ではありません")
    return [
        {
            "subject": f"{leak.get('File', '?')}:{leak.get('StartLine', '?')}",
            "severity": "critical",
            "detail": f"{leak.get('RuleID', '?')} — {leak.get('Description', '')}",
        }
        for leak in data
    ]


def parse_code_scanning(data: object) -> dict:
    """code scanning analyses API の出力から「最後に SAST が回った事実」を取り出す。

    3 通りに分ける (ここを 2 通りに畳むと嘘が生まれる):
        - 解析実績あり → いつ / どの ref / どのツールで回ったか
        - **空配列** = 解析実績ゼロ。「異常なし」ではなく **SAST が回っていない**
        - API を叩けなかった → workflow が `{"unavailable": 理由}` を置く → ParseError
          (= 未検証。取れなかったものを「稼働中」と書かない)
    """
    if isinstance(data, dict) and "unavailable" in data:
        raise ParseError(f"API から取得できず: {data['unavailable'] or '理由の記録なし'}")
    if not isinstance(data, list):
        raise ParseError("code scanning analyses の JSON が配列ではありません")
    if not data:
        return {"analyzed": False}
    latest = data[0]
    if not isinstance(latest, dict) or not latest.get("created_at"):
        raise ParseError("analyses の要素に created_at がありません")
    tool = latest.get("tool")
    return {
        "analyzed": True,
        "created_at": latest["created_at"],
        "ref": latest.get("ref", "?"),
        "commit_sha": str(latest.get("commit_sha", "?"))[:7],
        "tool": (tool.get("name") if isinstance(tool, dict) else None) or "?",
    }


def sast_stale_hours() -> int:
    """鮮度のしきい値を watchers.json (見張りの正典) から読む。読めなければ既定値。

    「解析が 1 件でもあれば ✅」だと、**192 日前の解析でも稼働中に見える**。
    しきい値をここに独自に持つと状況ページと食い違うので、同じ 1 個の宣言を読む。
    """
    try:
        watchers = json.loads(SAST_WATCHERS_JSON.read_text(encoding="utf-8"))
        for entry in watchers["workflows"]:
            if str(entry.get("id")) == SAST_WATCHER_ID:
                return int(entry["expect_hours"])
        raise KeyError(f"watchers.json に id {SAST_WATCHER_ID} が無い")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        # 握り潰しではない: 既定値に落ちたことを必ず stderr に出す。
        # 見えなくなるのは「watchers.json 側でしきい値が変わった」ことだけ。
        log(f"⚠️ SAST: watchers.json を読めず既定 {SAST_STALE_HOURS_FALLBACK}h を使う — {exc}")
        return SAST_STALE_HOURS_FALLBACK


def _parse_timestamp(raw: str) -> datetime:
    """API の ISO8601 (`...Z`) を aware な datetime にする。"""
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def judge_freshness(measured: dict, now: datetime, stale_hours: int) -> dict:
    """解析実績を「稼働 (verified)」と「停滞 (stale)」に分ける。

    **解析が存在すること = 回っていること、ではない。** default setup が無効化されても
    過去の解析は API に残り続けるので、しきい値を超えた古さは ✅ にしない。
    created_at が読めないときは ✅ でも 🔴 でもなく**未検証**に倒す (鮮度を判定できない)。
    """
    try:
        age = now - _parse_timestamp(measured["created_at"])
    except (KeyError, TypeError, ValueError) as exc:
        return {"status": "unverified", "reason": f"created_at を読めず鮮度を判定できず: {exc}"}
    status = "verified" if age <= timedelta(hours=stale_hours) else "stale"
    return {
        "status": status,
        "age_hours": round(age.total_seconds() / 3600, 1),
        "stale_hours": stale_hours,
        **measured,
    }


def sast_annotation(sast: dict) -> str:
    """Actions の annotation に出す 1 行 (verified なら空文字 = 出さない)。

    **どの状態を警告にするかの判定はここ (テスト済みの純関数) が持つ** — workflow の
    YAML に `if` を書くとテストできない (cicd/CLAUDE.md)。🔴 解析実績ゼロが緑 run の
    step summary にしか残らないと、⚠️ 未検証より重い状態が弱い経路になる。
    """
    if sast["status"] == "verified":
        return ""
    return f"::warning::{SAST_NAME}: {_cell(sast_line(sast))}"


def _cell(text: str) -> str:
    """表のセルに入れる 1 行。API のエラー文をそのまま入れると `|` や改行で表が壊れ、
    **理由が読めなくなる (= 未検証の理由を失う)** ので、ここで潰しておく。"""
    return " ".join(str(text).split()).replace("|", "\\|")


def sast_line(sast: dict) -> str:
    """隣接機構 (SAST) の 1 行。稼働 / 実績ゼロ / 未検証 を見分けられる形で出す。"""
    if sast["status"] == "verified":
        return (
            f"✅ 直近の解析 {sast['created_at']} "
            f"(ref `{sast['ref']}` / `{sast['commit_sha']}` / tool {sast['tool']})"
        )
    if sast["status"] == "stale":
        return (
            f"⚠️ **解析はあるが停滞** — 最終 {sast['created_at']} "
            f"({sast['age_hours']}h 前 / しきい値 {sast['stale_hours']}h)。"
            "無効化された可能性があります (過去の解析は API に残り続けます)"
        )
    if sast["status"] == "never_analyzed":
        return "🔴 **解析実績ゼロ** — 有効になっていても実際には SAST が回っていません"
    return (
        f"⚠️ **未検証**: {_cell(sast['reason'])} "
        "— 稼働している証拠も止まっている証拠も取れていません "
        "(状況ページ <https://yomote.github.io/mind-inbox/status/> の "
        f"「{SAST_NAME}」で確認する)"
    )


# (ファイル名, tool_id, 表示名, パーサ)。workflow 側の出力ファイル名と 1:1。
TOOLS = [
    ("npm-root.json", "npm-audit-root", "npm audit (root)", parse_npm_audit),
    ("pnpm-bff.json", "pnpm-audit-bff", "pnpm audit (apps/bff)", parse_pnpm_audit),
    (
        "pnpm-frontend.json",
        "pnpm-audit-frontend",
        "pnpm audit (apps/frontend)",
        parse_pnpm_audit,
    ),
    (
        "pip-audit-ai-agent.json",
        "pip-audit-ai-agent",
        "pip-audit (ai-agent — uv.lock の全依存)",
        parse_pip_audit,
    ),
    (
        "pip-audit-voicevox.json",
        "pip-audit-voicevox",
        "pip-audit (voicevox — requirements.txt の直接依存のみ)",
        parse_pip_audit,
    ),
    ("gitleaks.json", "gitleaks", "gitleaks (HEAD スナップショット)", parse_gitleaks),
]


def count_by_severity(tool_results: list[dict]) -> dict[str, int]:
    counts = {s: 0 for s in SEVERITIES}
    for tool in tool_results:
        for finding in tool.get("findings", []):
            counts[_severity(finding.get("severity"))] += 1
    return counts


def severity_line(by_severity: dict[str, int]) -> str:
    """`critical 1 · high 3 · 不明 42` の形。0 の桁は出さない (全部 0 なら '0 件')。"""
    label = {"unknown": "不明"}
    parts = [
        f"{label.get(s, s)} {by_severity[s]}" for s in SEVERITIES if by_severity.get(s)
    ]
    return " · ".join(parts) if parts else "0 件"


def aggregate(tool_results: list[dict], sast: dict | None = None) -> dict:
    """ツール別の結果 (ok / error + findings) を 1 個のレポートに畳む。

    ここが「しきい値」の正典: Issue を立てるのは **検出 1 件以上** (issue_needed)。
    severity で足切りしない — 到達可能性の判定はこの機構では**できない**ので、
    足切りは「静かに嘘をつく」side に倒れる。ノイズが問題になったら
    しきい値を上げるのではなく、判定できる材料 (直接依存か等) を足す。

    sast は隣接機構の実測 (省略 = 未検証)。**この sweep のツール失敗とは混ぜない** —
    `failed` は「この sweep が検査できなかった対象」の桁で、run を落とす判定に
    使われる。SAST は別 workflow の担当で、その生死は watchers.json / 状況ページが
    見張る (ここで run を落とすと責任の所在がぼやける)。レポート本文には毎回出す。
    """
    by_severity = count_by_severity(tool_results)
    total = sum(by_severity.values())
    failed = [t["id"] for t in tool_results if t["status"] != "ok"]
    report = {
        "total": total,
        "by_severity": by_severity,
        "severity_line": severity_line(by_severity),
        "failed": failed,
        "issue_needed": total > 0,
        "tools": tool_results,
        "sast": sast if sast is not None else dict(SAST_NOT_MEASURED),
    }
    # workflow はこれを**そのまま出す**だけ (判定は上の sast_annotation が持つ)。
    report["sast"]["annotation"] = sast_annotation(report["sast"])
    report["markdown"] = to_markdown(report)
    return report


def _tool_section(tool: dict) -> list[str]:
    lines = [f"### {tool['name']} — ", ""]
    if tool["status"] != "ok":
        lines[0] += "⚠️ 実行できず"
        lines.append(f"- 出力を読めませんでした: {tool.get('error', '不明')}")
        lines.append("- **0 件ではありません** — この対象は今回検査できていません")
    else:
        findings = tool["findings"]
        lines[0] += f"{len(findings)} 件"
        if not findings:
            lines.append("- なし")
        for finding in findings[:MAX_LINES_PER_TOOL]:
            lines.append(
                f"- **{finding['severity']}** `{finding['subject']}` — {finding['detail']}"
            )
        if len(findings) > MAX_LINES_PER_TOOL:
            lines.append(
                f"- …他 {len(findings) - MAX_LINES_PER_TOOL} 件 (全件は run の生 JSON を参照)"
            )
    lines.append("")
    return lines


def to_markdown(report: dict) -> str:
    """Issue 本文 / step summary 用。0 件でも uncovered とツール実行状況は必ず出す。"""
    lines = [
        f"## セキュリティスキャン結果 (合計 {report['total']} 件: {report['severity_line']})",
        "",
        "| ツール | 実行 | 検出 |",
        "| --- | --- | --- |",
    ]
    for tool in report["tools"]:
        if tool["status"] == "ok":
            lines.append(f"| {tool['name']} | ✅ | {len(tool['findings'])} |")
        else:
            lines.append(f"| {tool['name']} | ⚠️ 実行できず | — |")
    lines.append("")
    for tool in report["tools"]:
        lines.extend(_tool_section(tool))
    lines += [
        "## 隣接する検査 (この sweep の外で回っているもの)",
        "",
        "| 検査 | 担当 | 実測 |",
        "| --- | --- | --- |",
        f"| SAST (コードの脆弱パターン) | CodeQL default setup | {sast_line(report['sast'])} |",
        "",
        "## この sweep がカバーしていないもの",
        "",
        "**0 件 = 安全、ではありません。** 以下は機械スキャンの対象外です (ADR 0038):",
        "",
    ]
    lines.extend(f"- {item}" for item in UNCOVERED)
    lines += [
        "",
        "---",
        "",
        "_Generated by [Claude Code](https://claude.ai/code) — "
        "`.github/workflows/security-sweep.yml` / 集計: `cicd/scripts/security-sweep/sweep.py`_",
    ]
    return "\n".join(lines)


def log(message: str) -> None:
    print(message, file=sys.stderr)


def load_tool_results(reports_dir: Path) -> list[dict]:
    results = []
    for filename, tool_id, name, parser in TOOLS:
        result = {"id": tool_id, "name": name, "status": "ok", "findings": []}
        path = reports_dir / filename
        try:
            result["findings"] = parser(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ParseError) as exc:
            result["status"] = "error"
            result["error"] = str(exc)
            log(f"⚠️ {tool_id}: 出力を読めない — {exc}")
        results.append(result)
    return results


def load_sast_status(reports_dir: Path, now: datetime | None = None) -> dict:
    """SAST (隣接機構) の実測を読む。読めなかったら「未検証: 理由」に落とす。

    now は鮮度判定の基準時刻 (省略 = 現在)。テストが時計を固定するために受ける。
    """
    path = reports_dir / SAST_REPORT_FILE
    if not path.exists():
        log(f"⚠️ SAST: 実測ファイルが無い — {path}")
        return dict(SAST_NOT_MEASURED)
    try:
        measured = parse_code_scanning(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ParseError) as exc:
        log(f"⚠️ SAST: 稼働を確認できず — {exc}")
        return {"status": "unverified", "reason": str(exc)}
    if not measured.pop("analyzed"):
        log("🔴 SAST: 解析実績ゼロ (CodeQL の解析が 1 件も無い)")
        return {"status": "never_analyzed"}
    sast = judge_freshness(
        measured, now or datetime.now(timezone.utc), sast_stale_hours()
    )
    if sast["status"] != "verified":
        log(f"⚠️ SAST: {sast_line(sast)}")
    return sast


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        log(f"使い方: {Path(argv[0]).name} <reports_dir>")
        return 1
    reports_dir = Path(argv[1])
    if not reports_dir.is_dir():
        log(f"reports_dir がありません: {reports_dir}")
        return 1
    report = aggregate(load_tool_results(reports_dir), load_sast_status(reports_dir))
    log(
        f"検出 {report['total']} 件 ({report['severity_line']}) / "
        f"実行できず {len(report['failed'])} ツール / カバー外 {len(UNCOVERED)} 領域 (本文に明示) / "
        f"SAST {report['sast']['status']}"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
