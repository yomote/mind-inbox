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
    - カバーしていない領域 (SAST / 実環境の設定監査 / 依存の悪意ある更新 …) は
      検出 0 件でも**毎回明示する**。「0 件 = 安全」と読ませない (silent caps 禁止)

使い方:
    sweep.py <reports_dir>
      reports_dir に各ツールの生 JSON (下の TOOLS のファイル名) を置いて呼ぶ。
      stdout に JSON 1 個:
        {"total": int, "by_severity": {...}, "failed": [tool_id, ...],
         "issue_needed": bool, "tools": [...], "markdown": str}
      markdown は Issue 本文にそのまま使える形。診断は stderr。

終了コード: 0 = 集計できた (検出件数・ツール失敗は JSON で表す) / 1 = 前提不足
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# severity の表示順 (深刻な順)。"unknown" は pip-audit 用 — pip-audit の JSON は
# severity を含まない (アドバイザリ DB 側の属性で、出力に載らない)。
# 「不明」を moderate 等に**寄せて数えない** — 深刻度を偽装しないため独立の桁で出す。
SEVERITIES = ("critical", "high", "moderate", "low", "info", "unknown")

# 1 ツールあたり明細をここまで載せる。超過分は件数を明示して省略する
# (黙って切らない — silent caps 禁止)。
MAX_LINES_PER_TOOL = 50

# この sweep がカバーしていない領域。検出器を足したらここから消す (debt-check と同じ運用)。
UNCOVERED = [
    "SAST (コード自体の脆弱パターン — injection / SSRF 等)。semgrep / bandit / CodeQL 未導入",
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


# (ファイル名, tool_id, 表示名, パーサ)。workflow 側の出力ファイル名と 1:1。
TOOLS = [
    ("npm-root.json", "npm-audit-root", "npm audit (root)", parse_npm_audit),
    ("npm-bff.json", "npm-audit-bff", "npm audit (apps/bff)", parse_npm_audit),
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


def aggregate(tool_results: list[dict]) -> dict:
    """ツール別の結果 (ok / error + findings) を 1 個のレポートに畳む。

    ここが「しきい値」の正典: Issue を立てるのは **検出 1 件以上** (issue_needed)。
    severity で足切りしない — 到達可能性の判定はこの機構では**できない**ので、
    足切りは「静かに嘘をつく」side に倒れる。ノイズが問題になったら
    しきい値を上げるのではなく、判定できる材料 (直接依存か等) を足す。
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
    }
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


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        log(f"使い方: {Path(argv[0]).name} <reports_dir>")
        return 1
    reports_dir = Path(argv[1])
    if not reports_dir.is_dir():
        log(f"reports_dir がありません: {reports_dir}")
        return 1
    report = aggregate(load_tool_results(reports_dir))
    log(
        f"検出 {report['total']} 件 ({report['severity_line']}) / "
        f"実行できず {len(report['failed'])} ツール / カバー外 {len(UNCOVERED)} 領域 (本文に明示)"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
