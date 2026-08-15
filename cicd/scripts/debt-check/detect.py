#!/usr/bin/env python3
"""機械検出できる技術負債を洗う (ADR 0037 / 旧 maint-check Routine の機械部分)。

規律 — **静かに嘘をつかない検出器だけを置く**:
    - 検出できることは決定論的に検出する (壊れた相対リンク / placeholder の test script)。
      LLM の判断が要るもの・偽陽性が混ざるヒューリスティックは置かない
    - 検出できないこと (意味的な docs 陳腐化 / デッドコード / 依存の逆流 …) は
      UNCOVERED として**毎回明示する**。「0 件 = 全部健全」と読ませないため —
      ここを省くと、カバー範囲の狭さが緑色の顔をして通る

使い方:
    detect.py <repo_root>
      stdout に JSON 1 個:
        {"total": int, "detectors": [{"id", "name", "findings": [...]}, ...],
         "uncovered": [...], "markdown": str}
      markdown は Issue 本文にそのまま使える形 (uncovered を必ず含む)。診断は stderr。

終了コード: 0 = 検出処理が走った (件数は total で表す) / 1 = 前提不足
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
from pathlib import Path

# 検出器がカバーしていない領域 (旧 maint-check の想定範囲との差分)。
# 新しい検出器を足したらここから消す。消せない項目は debrief / PM tick で人が見る。
UNCOVERED = [
    "意味的な docs の陳腐化 (実装と説明の乖離) — LLM の判断が要る。debrief / PM tick で拾う",
    "未使用 export・デッドコード — ts-prune / knip 等のツール未導入。導入したら検出器に昇格する",
    "依存の逆流 (レイヤ違反) — 検出器未実装",
    "Markdown の参照形式リンク ([text][ref]) と外部 URL の死活 — インライン相対リンクのみ検査している",
    "リポジトリ外へ抜ける相対リンク (例: `../../../issues/4` — GitHub web 上でだけ解決する形) — "
    "ローカルに実体が無く機械では真偽を判定できないため検査対象外",
    "リンク検査の走査対象外にある Markdown — `apps/**` / `.claude/**` / `.github/**` の md は"
    "未検査 (検査するのは `docs/**` / `cicd/**` / リポジトリ直下の *.md のみ)",
]

# リンク検査の走査対象。ここに無いディレクトリの md は**検査されない** —
# 「0 件」を「リポジトリ全体が健全」と読ませないため、この範囲は UNCOVERED にも書く。
#
# 足す基準は「外部由来 / 生成物の md が紛れ込まない場所か」。混ざると偽陽性が出て
# Issue がノイズで埋まり、本物の壊れリンクが読まれなくなる (偽陽性は検出器の死):
#   - `apps/**` は依存物 (`node_modules`) の md を抱えうる
#   - `.claude/**` は worktree (`.claude/worktrees/<agent>/…`) を抱えうる —
#     他セッションの作業中ブランチを走査して他人の未完成リンクを報告してしまう
# 対象を広げるときは、広げた状態で実測して偽陽性 0 を確認してから足す (Issue #421)。
LINK_SCAN_DIRS = ("docs", "cicd")
# リポジトリ直下の *.md (README.md / CLAUDE.md / AGENTS.md)。再帰しない —
# ルート再帰は node_modules や worktree を巻き込むため、上の基準に反する。
LINK_SCAN_ROOT_FILES = "*.md"

# 走査対象の中に現れても検査しないディレクトリ名 (リポジトリが書いた md ではないもの)。
# 混ざると偽陽性が出るうえ、**あるかどうかが実行環境で変わる** — CI では存在せず
# ローカルにだけある `.venv` (実測: `apps/services/ai-agent/.venv` 配下に md 38 本) を
# 拾うと、同じコミットでも実行場所で結果が変わる検出器になる。
EXCLUDED_DIR_NAMES = {"node_modules", ".venv", "worktrees", ".git"}

_FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)
# インラインリンク [text](target) / 画像 ![alt](target)。title 付き (target "title") も拾う
_INLINE_LINK = re.compile(r"\]\(\s*<?([^)<>\s]+)>?(?:\s+\"[^\"]*\")?\s*\)")


def log(message: str) -> None:
    print(message, file=sys.stderr)


def strip_fenced_code(text: str) -> str:
    """コード fence 内を検査対象から外す (例示のリンクを壊れ扱いしない)。"""
    return _FENCED_CODE.sub("", text)


def iter_inline_links(text: str) -> list[str]:
    return _INLINE_LINK.findall(strip_fenced_code(text))


def is_checkable_relative(target: str) -> bool:
    """検査対象の相対リンクか。外部 URL / ページ内アンカー / mailto は対象外。"""
    if not target or target.startswith("#"):
        return False
    parsed = urllib.parse.urlparse(target)
    return not parsed.scheme  # http(s):, mailto:, tel: などを一括で除外


def broken_links_in(md_file: Path, text: str, root: Path) -> list[dict]:
    """1 つの Markdown ファイル内の壊れた相対リンクを返す (root からの相対で報告)。"""
    findings = []
    for target in iter_inline_links(text):
        if not is_checkable_relative(target):
            continue
        path_part = urllib.parse.unquote(target.split("#", 1)[0])
        if not path_part:
            continue  # 純アンカー (#section) — アンカーの実在検査は対象外 (UNCOVERED ではなく仕様)
        if path_part.startswith("/"):
            # ルート相対 (](/README.md) 等)。Path の `/` 演算子は右辺が絶対パスだと
            # 左辺を丸ごと捨てるため、素通しすると OS ルートに解決され
            # 「リポジトリ外」として黙って検査対象外になる (PR #224 レビュー指摘)。
            # 書き手の意図 (リポジトリルート相対) に解決して実在を検査する
            resolved = (root / path_part.lstrip("/")).resolve()
        else:
            resolved = (md_file.parent / path_part).resolve()
        if not resolved.is_relative_to(root):
            # リポジトリ外へ抜けるリンクは GitHub web 相対 (../../../issues/N 等) で、
            # ローカルでは真偽を判定できない。「壊れている」と誤報するより
            # UNCOVERED として明示する方を選ぶ (実出力 2026-08-10 で ADR 0018 の
            # Issue リンク 5 件を誤報しかけた)
            continue
        if not resolved.exists():
            findings.append(
                {
                    "file": md_file.relative_to(root).as_posix(),
                    "target": target,
                }
            )
    return findings


def iter_scanned_markdown(root: Path) -> list[Path]:
    """リンク検査の対象となる Markdown を列挙する (走査範囲は LINK_SCAN_DIRS が定義)。"""
    md_files: list[Path] = []
    for scan_dir in LINK_SCAN_DIRS:
        target = root / scan_dir
        if not target.is_dir():
            # 走査対象が無いのを黙って飛ばすと「0 件 = 健全」に化ける。
            # ここでは列挙を続け、実行経路 (main) が前提不足として run を落とす
            continue
        md_files.extend(target.rglob("*.md"))
    md_files.extend(root.glob(LINK_SCAN_ROOT_FILES))
    return sorted(
        f for f in md_files if not EXCLUDED_DIR_NAMES & set(f.relative_to(root).parts)
    )


def missing_scan_dirs(root: Path) -> list[str]:
    """LINK_SCAN_DIRS のうち実在しないもの。

    走査対象が (改名・移動で) 消えると、検査していないのに 0 件になり
    「異常なし」として緑になる。main() がこれを**前提不足 (exit 1)** として扱い、
    run を落とすことで沈黙と健全を区別する — レポート内の finding にすると
    「検出処理は走った」ことになってしまう。
    """
    return [d for d in LINK_SCAN_DIRS if not (root / d).is_dir()]


def detect_broken_doc_links(root: Path) -> list[dict]:
    """走査対象 (LINK_SCAN_DIRS + リポジトリ直下) の md のインライン相対リンク切れ。

    走査対象の実在は main() が事前に検査する (missing_scan_dirs)。
    """
    findings = []
    for md_file in iter_scanned_markdown(root):
        if md_file.name == "template.md":
            # 雛形の NNNN-xxx.md は意図した placeholder。毎週誤報すると
            # Issue がノイズで埋まり、本物の壊れリンクが読まれなくなる
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            # 読めないファイルを黙って飛ばさない — 読めないこと自体を負債として報告する
            findings.append(
                {
                    "file": md_file.relative_to(root).as_posix(),
                    "target": f"(読めません: {exc})",
                }
            )
            continue
        findings.extend(broken_links_in(md_file, text, root))
    return findings


def detect_placeholder_test_scripts(root: Path) -> list[dict]:
    """package.json の scripts に残っている placeholder (テストのふりをする echo)。

    例 (実物): "test:py:voicevox": "echo 'placeholder: ...' && exit 0" —
    test:fast が緑でも voicevox は 1 行もテストされていない。
    """
    findings = []
    for pkg in sorted(root.rglob("package.json")):
        if "node_modules" in pkg.parts:
            continue
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue  # 壊れた package.json は lint / CI が別途落とす守備範囲
        scripts = data.get("scripts") if isinstance(data, dict) else None
        if not isinstance(scripts, dict):
            continue
        for name, command in scripts.items():
            if isinstance(command, str) and "placeholder" in command.lower():
                findings.append(
                    {
                        "file": pkg.relative_to(root).as_posix(),
                        "script": name,
                        "command": command,
                    }
                )
    return findings


def detect_all(root: Path) -> dict:
    detectors = [
        {
            "id": "broken-doc-links",
            "name": "壊れた相対リンク (docs/ + cicd/ + リポジトリ直下の *.md)",
            "findings": detect_broken_doc_links(root),
        },
        {
            "id": "placeholder-test-scripts",
            "name": "placeholder のままのテスト script",
            "findings": detect_placeholder_test_scripts(root),
        },
    ]
    total = sum(len(d["findings"]) for d in detectors)
    report = {
        "total": total,
        "detectors": detectors,
        "uncovered": UNCOVERED,
    }
    report["markdown"] = to_markdown(report)
    return report


def _finding_line(finding: dict) -> str:
    if "script" in finding:
        return f"- `{finding['file']}` scripts.`{finding['script']}`: `{finding['command']}`"
    return f"- `{finding['file']}` → `{finding['target']}`"


def to_markdown(report: dict) -> str:
    """Issue 本文 / step summary 用。0 件でも uncovered は必ず出す (silent caps 禁止)。"""
    lines = [f"## 機械検出できた負債 ({report['total']} 件)", ""]
    for detector in report["detectors"]:
        lines.append(f"### {detector['name']} ({len(detector['findings'])} 件)")
        lines.append("")
        if detector["findings"]:
            lines.extend(_finding_line(f) for f in detector["findings"])
        else:
            lines.append("- なし")
        lines.append("")
    lines += [
        "## この検出器がカバーしていないもの",
        "",
        "**0 件 = 全部健全、ではありません。** 以下は機械検出の対象外です (ADR 0037):",
        "",
    ]
    lines.extend(f"- {item}" for item in report["uncovered"])
    lines += [
        "",
        "---",
        "",
        "_Generated by [Claude Code](https://claude.ai/code) — "
        "`.github/workflows/debt-check.yml` / 検出器: `cicd/scripts/debt-check/detect.py`_",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        log(f"使い方: {Path(argv[0]).name} <repo_root>")
        return 1
    root = Path(argv[1]).resolve()
    missing = missing_scan_dirs(root)
    if missing:
        # 走査対象が欠けたまま検出を走らせると「検査していないのに 0 件」の
        # レポートが出る。前提不足として run を落とし、沈黙を緑にしない
        log(
            "repo root に見えません / リンク検査の走査対象がありません "
            f"({' / '.join(missing)}): {root}"
        )
        return 1
    report = detect_all(root)
    log(
        f"検出 {report['total']} 件 / カバー外 {len(report['uncovered'])} 領域 (本文に明示)"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
