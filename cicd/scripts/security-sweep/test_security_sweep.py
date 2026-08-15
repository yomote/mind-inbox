"""[L1] 週次セキュリティスキャンの集計 (ADR 0038)。

無いと何が静かに通るか:
    - ツール出力が読めなかったとき「0 件」として緑になり、**検査していない対象が
      検査済みの顔をする** (取れなかったものを合格と書かない、の退行)
    - severity の数え間違い / pip-audit の severity 無しを moderate 等に寄せる改変が
      通り、Issue タイトルの深刻度が実態とズレる
    - 0 件のときにカバー外領域の明示が落ち、「0 件 = 安全」という嘘が緑色の顔をして通る
    - SAST の欄が、**稼働 / 解析実績ゼロ / 取得失敗**のどれでも同じ顔になる
      (403 やネットワーク失敗を「稼働中」と読ませる嘘。Issue #408 の再発)
"""

import json
from pathlib import Path

from sweep import (
    ParseError,
    aggregate,
    load_sast_status,
    load_tool_results,
    parse_code_scanning,
    parse_gitleaks,
    parse_npm_audit,
    parse_pip_audit,
    parse_pnpm_audit,
    sast_line,
    severity_line,
)

CODE_SCANNING_SAMPLE = [
    {
        "id": 91,
        "ref": "refs/heads/main",
        "commit_sha": "0123456789abcdef0123456789abcdef01234567",
        "created_at": "2026-08-14T01:48:12Z",
        "tool": {"name": "CodeQL", "version": "2.20.0"},
    }
]

NPM_SAMPLE = {
    "auditReportVersion": 2,
    "vulnerabilities": {
        "shell-quote": {
            "name": "shell-quote",
            "severity": "critical",
            "isDirect": False,
            "via": [{"title": "command injection", "severity": "critical"}],
        },
        "markdownlint-cli2": {
            "name": "markdownlint-cli2",
            "severity": "moderate",
            "isDirect": True,
            "via": ["markdown-it"],
        },
    },
    "metadata": {"vulnerabilities": {"critical": 1, "moderate": 1, "total": 2}},
}

PNPM_SAMPLE = {
    "actions": [],
    "advisories": {
        "1097679": {
            "module_name": "vite",
            "severity": "high",
            "title": "Vite dev server arbitrary file read",
        }
    },
    "metadata": {"vulnerabilities": {"high": 1}},
}

PIP_SAMPLE = {
    "dependencies": [
        {"name": "a2a-sdk", "version": "1.1.2", "vulns": []},
        {
            "name": "aiohttp",
            "version": "3.13.5",
            "vulns": [
                {"id": "PYSEC-2026-237", "fix_versions": ["3.14.0"]},
                {"id": "PYSEC-2026-2104", "fix_versions": []},
            ],
        },
    ],
    "fixes": [],
}

GITLEAKS_SAMPLE = [
    {
        "RuleID": "aws-access-key-id",
        "Description": "AWS Access Key",
        "File": "cicd/x.sh",
        "StartLine": 12,
    }
]


def test_l1_npm_auditはパッケージ単位で数えdirectを注記する() -> None:
    findings = parse_npm_audit(NPM_SAMPLE)
    assert [(f["subject"], f["severity"]) for f in findings] == [
        ("markdownlint-cli2", "moderate"),
        ("shell-quote", "critical"),
    ]
    assert "[直接依存]" in findings[0]["detail"]
    assert "command injection" in findings[1]["detail"]


def test_l1_pnpm_auditはアドバイザリ単位で数える() -> None:
    findings = parse_pnpm_audit(PNPM_SAMPLE)
    assert findings == [
        {
            "subject": "vite",
            "severity": "high",
            "detail": "Vite dev server arbitrary file read",
        }
    ]


def test_l1_pip_auditはseverityを持たないのでunknownで数える() -> None:
    """pip-audit の JSON に severity は無い。moderate 等へ寄せると深刻度を偽装する。"""
    findings = parse_pip_audit(PIP_SAMPLE)
    assert len(findings) == 2  # (パッケージ, 脆弱性 ID) 単位
    assert all(f["severity"] == "unknown" for f in findings)
    assert findings[0]["subject"] == "aiohttp==3.13.5"
    assert "PYSEC-2026-237" in findings[0]["detail"]
    assert "fix: 3.14.0" in findings[0]["detail"]


def test_l1_gitleaksのleakは全件critical() -> None:
    findings = parse_gitleaks(GITLEAKS_SAMPLE)
    assert findings[0]["severity"] == "critical"
    assert findings[0]["subject"] == "cicd/x.sh:12"


def test_l1_期待した形でない出力はParseErrorになり0件扱いされない() -> None:
    for parser, bad in [
        (parse_npm_audit, {"error": {"code": "ENOTFOUND"}}),
        (parse_pnpm_audit, {"vulnerabilities": {}}),
        (parse_pip_audit, []),
        (parse_gitleaks, {"leaks": []}),
    ]:
        try:
            parser(bad)
            raise AssertionError(f"{parser.__name__} が壊れた出力を黙って通した")
        except ParseError:
            pass


def _tool(tool_id: str, findings: list[dict], status: str = "ok") -> dict:
    return {"id": tool_id, "name": tool_id, "status": status, "findings": findings}


def test_l1_severity集計と表示は0の桁を出さない() -> None:
    report = aggregate(
        [
            _tool("a", [{"subject": "x", "severity": "critical", "detail": ""}]),
            _tool(
                "b",
                [
                    {"subject": "y", "severity": "unknown", "detail": ""},
                    {"subject": "z", "severity": "unknown", "detail": ""},
                ],
            ),
        ]
    )
    assert report["total"] == 3
    assert report["by_severity"]["critical"] == 1
    assert report["by_severity"]["unknown"] == 2
    assert report["severity_line"] == "critical 1 · 不明 2"
    assert report["issue_needed"]
    assert severity_line({s: 0 for s in ("critical", "high")}) == "0 件"


def test_l1_0件ならissue不要だがカバー外領域は必ず明示する() -> None:
    """silent caps 禁止の本丸。0 件レポートが「安全宣言」に見えたら負け。"""
    report = aggregate([_tool("a", []), _tool("b", [])])
    assert report["total"] == 0
    assert not report["issue_needed"]
    assert "カバーしていない" in report["markdown"]
    assert "0 件 = 安全、ではありません" in report["markdown"]


def test_l1_実行できなかったツールは0件ではなく実行できずとして出る() -> None:
    """取れなかったものを「合格」と書かない (review-gate と同じ規律)。"""
    broken = _tool("gitleaks", [], status="error")
    broken["error"] = "JSON が読めない"
    report = aggregate([_tool("a", []), broken])
    assert report["failed"] == ["gitleaks"]
    md = report["markdown"]
    assert "実行できず" in md
    assert "この対象は今回検査できていません" in md


def test_l1_明細の省略は黙って切らず残件数を明示する() -> None:
    many = [
        {"subject": f"pkg{i}", "severity": "high", "detail": "x"} for i in range(60)
    ]
    md = aggregate([_tool("a", many)])["markdown"]
    assert "…他 10 件" in md


def test_l1_SASTはCodeQL未導入と書かず実測した稼働を出す() -> None:
    """#408 の再発防止。CodeQL default setup は 2026-08-12 から回っている。"""
    measured = parse_code_scanning(CODE_SCANNING_SAMPLE)
    sast = {"status": "verified", **_no_flag(measured)}
    line = sast_line(sast)
    assert line.startswith("✅")
    assert "2026-08-14T01:48:12Z" in line  # 「いつ回ったか」まで出す (稼働の証拠)
    assert "refs/heads/main" in line
    assert "0123456" in line and "0123456789abcdef" not in line  # sha は短縮
    md = aggregate([_tool("a", [])], sast)["markdown"]
    assert line in md
    # 事実と食い違う宣言 (#408 で見つかった「CodeQL 未導入」) を二度と出さない
    assert "CodeQL 未導入" not in md
    assert "この sweep の対象外" in md  # 「回していない」こと自体は毎回明示する


def test_l1_SASTの取得失敗は稼働と区別できる未検証になる() -> None:
    """403 / ネットワーク失敗を「稼働中」と読ませない (取れなかったものを合格と書かない)。"""
    try:
        parse_code_scanning({"unavailable": "HTTP 403: Resource not accessible"})
        raise AssertionError("取得失敗を黙って通した")
    except ParseError as exc:
        assert "403" in str(exc)

    line = sast_line({"status": "unverified", "reason": "HTTP 403"})
    assert not line.startswith("✅")
    assert "未検証" in line
    assert "HTTP 403" in line  # 理由を落とさない
    report = aggregate([_tool("a", [])], {"status": "unverified", "reason": "HTTP 403"})
    assert line in report["markdown"]

    # API のエラー文に `|` や改行が混ざっても表 (= 理由の読める場所) を壊さない
    messy = sast_line({"status": "unverified", "reason": "gh: 403\n{a|b}\n"})
    assert "\n" not in messy and "{a\\|b}" in messy


def test_l1_SAST解析実績ゼロは稼働とも未検証とも別の顔で出る() -> None:
    """空配列 = 「有効なのに一度も回っていない」。0 件 = 安全ではない。"""
    assert parse_code_scanning([]) == {"analyzed": False}
    line = sast_line({"status": "never_analyzed"})
    assert not line.startswith("✅")
    assert "解析実績ゼロ" in line and "未検証" not in line
    assert line in aggregate([_tool("a", [])], {"status": "never_analyzed"})["markdown"]


def test_l1_SASTの実測を渡さなければ稼働扱いされず未検証になる() -> None:
    """呼び出し側が実測を落としたとき、黙って「稼働中」に見えないこと。"""
    report = aggregate([_tool("a", [])])
    assert report["sast"]["status"] == "unverified"
    assert "未検証" in report["markdown"]


def test_l1_load_sast_statusはファイルの有無と中身で3通りに分かれる(
    tmp_path: Path,
) -> None:
    assert load_sast_status(tmp_path)["status"] == "unverified"  # 実測 step が回らず

    path = tmp_path / "code-scanning.json"
    path.write_text(json.dumps(CODE_SCANNING_SAMPLE), encoding="utf-8")
    assert load_sast_status(tmp_path) == {
        "status": "verified",
        "created_at": "2026-08-14T01:48:12Z",
        "ref": "refs/heads/main",
        "commit_sha": "0123456",
        "tool": "CodeQL",
    }

    path.write_text("[]", encoding="utf-8")
    assert load_sast_status(tmp_path)["status"] == "never_analyzed"

    path.write_text('{"unavailable": "HTTP 403"}', encoding="utf-8")
    unverified = load_sast_status(tmp_path)
    assert unverified["status"] == "unverified"
    assert "403" in unverified["reason"]


def test_l1_SASTの失敗はsweep自身のツール失敗と混ざらない() -> None:
    """SAST は別 workflow の担当 — ここで failed に混ぜると run を落とす判定が濁る。"""
    report = aggregate([_tool("a", [])], {"status": "unverified", "reason": "HTTP 403"})
    assert report["failed"] == []


def _no_flag(measured: dict) -> dict:
    """parse_code_scanning の analyzed フラグを外した残り (load_sast_status と同じ形)。"""
    rest = dict(measured)
    assert rest.pop("analyzed") is True
    return rest


def test_l1_load_tool_resultsは欠損ファイルをerrorにする(tmp_path: Path) -> None:
    (tmp_path / "npm-root.json").write_text(json.dumps(NPM_SAMPLE), encoding="utf-8")
    (tmp_path / "gitleaks.json").write_text("[]", encoding="utf-8")
    # 残り 4 ファイルは無い → error (0 件扱いしない)
    results = {r["id"]: r["status"] for r in load_tool_results(tmp_path)}
    assert results["npm-audit-root"] == "ok"
    assert results["gitleaks"] == "ok"
    assert results["pnpm-audit-frontend"] == "error"
    assert results["pip-audit-ai-agent"] == "error"
