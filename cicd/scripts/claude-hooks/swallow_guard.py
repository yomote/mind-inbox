#!/usr/bin/env python3
"""握り潰し (silent failure) を書いた直後に気づかせる PostToolUse hook。

これが無いと何が静かに通るか:
    `2>/dev/null` / `|| true` / 空の catch を**理由も書かずに**足したコードが、
    レビューにも CI にも引っかからないまま入る。実際に 2026-08-13 に
    `.github/workflows/adr-number-guard.yml` で起きている。握り潰しは定義上
    「失敗しても何も出ない」ので、**入った瞬間から誰も気づけない** — これが
    CLAUDE.md / AGENTS.md が「このリポジトリで最も繰り返している事故」と
    名指ししているもの。CI は「握り潰しがあるか」を見ていないので、
    この検査が無いと通す門がどこにも無い。

設計 (Issue #392 の要件をそのまま):
    - **この編集が触った行だけ**見る。既存コードは対象外
      (`structuredPatch` の `+` 行 + 削除を含む hunk の残存行)
    - **近傍にコメントを書けば通る**。「握り潰すな」ではなく
      「何が見えなくなるかを書け」が規約なので、書いてあれば違反ではない
    - `PostToolUse` は**書き込みを取り消せない** (2026-08-13 実測)。これは
      「止める」機構ではなく「直させる」機構

対象拡張子を絞っているのは誤検知を減らすため。`.md` を外しているのは、
規約そのものを説明する文書 (この規約の解説を含む) が自分自身に引っかかるため。

**この hook の生死は `watchers.json` (status ページ) では見ていない。**
status ページは GitHub の実データ (workflow run / Issue / PR) から生死を出すが、
hook は Claude Code セッションの中だけで動き、**GitHub 側に run を 1 つも残さない**。
行を足しても「動いた形跡が無い」と「動いていない」が区別できず、**取れていないものが
緑で表示される** — CLAUDE.md 最優先の禁止事項 (取れなかったものを異常なしと書かない) に
正面から反する。代わりに `test_hook_wiring.py` が配線を検査し、これは
`npm run test:scripts` → `npm run test:fast` → CI job `test (L0 / L1+L2 / L3 / L3-real)`
(required status check) で走る。**配線が壊れるとマージが止まる。**

**塞いでいない抜け道**: 検査は 1 回の編集の差分しか見ないので、握り潰しを 2 回の編集に
分けて書けば (1 回目に `catch (e) {`、2 回目に `}`) すり抜ける。塞ぐには編集のたびに
ファイル全体を検査することになり、既存コードの握り潰しで無関係な編集がブロックされる
(誤検知は hook が外される最大の原因)。**意図的に開けてある。CI 側にもこの検査は無い。**
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hook_io  # noqa: E402

# 検査するファイル。ここに無い拡張子は即 passthrough (毎回の Edit に費用をかけない)。
CODE_SUFFIXES = frozenset(
    {
        ".sh",
        ".bash",
        ".zsh",
        ".yml",
        ".yaml",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".py",
        ".ps1",
        ".bicep",
    }
)
CODE_NAMES = frozenset({"Dockerfile", "Makefile"})

# 「握り潰し」の形。**単純な正規表現しか置かない** — 偽陽性はコメント 1 行で回避できるが、
# 重い検査は毎回の Edit の費用になる。
_SINGLE_LINE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # 直後にコメントが無ければ「何のエラーを捨てたか」が永久に分からなくなる形
    (re.compile(r"2>\s*/dev/null"), "2>/dev/null (標準エラーを捨てている)"),
    (re.compile(r"2>&1\s*(?:\||>)\s*/dev/null"), "2>&1 >/dev/null (出力を全部捨てている)"),
    # `cmd >/dev/null 2>&1` — 上の 2 つはこの語順を拾わない (シェルで最も多い形)
    (re.compile(r">\s*/dev/null\s+2>&1"), ">/dev/null 2>&1 (標準出力も標準エラーも捨てている)"),
    (re.compile(r"&>\s*/dev/null"), "&>/dev/null (出力を全部捨てている)"),
    (re.compile(r"\|\|\s*true(?![\w-])"), "|| true (失敗を成功に化けさせている)"),
    (re.compile(r"\|\|\s*:\s*(?:$|;|&)"), "|| : (失敗を成功に化けさせている)"),
    (re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}"), "空の catch (例外を捨てている)"),
    (re.compile(r"\bexcept\b[^:]*:\s*pass\b"), "空の except (例外を捨てている)"),
    (re.compile(r"continue-on-error:\s*true"), "continue-on-error: true (失敗を緑にしている)"),
)

# 行をまたぐ形。開き行 → (空行/コメントを飛ばして) 閉じ行、の 2 行で判定する。
_CATCH_OPEN = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*$")
_EXCEPT_OPEN = re.compile(r"^\s*except\b[^:]*:\s*$")

_COMMENT_ONLY = re.compile(r"^\s*(?:#|//|/\*|\*|<!--|--\s)")
# 行内コメント判定の前に URL のスラッシュを潰す (`https://…` を `//` コメントと誤認しないため)
_URL_SCHEME = re.compile(r"\b[a-z][a-z0-9+.-]*://")


class Finding(NamedTuple):
    line_no: int
    label: str
    text: str
    source: str = "追加"  # "追加" = この編集で足した行 / "残置" = 削除の巻き添えで残った行


def is_checked_path(file_path: str) -> bool:
    """検査対象のファイルか。対象外なら以降を一切走らせない。"""
    if not file_path:
        return False
    path = Path(file_path)
    return path.suffix in CODE_SUFFIXES or path.name in CODE_NAMES


def added_line_numbers(tool_response: Any, tool_input: Any) -> set[int] | None:
    """新しいファイル内容における「新規追加行」の行番号 (1 始まり)。

    判定不能なら None を返す。**空 set と None を混同しないこと** —
    空 set は「追加行が無い」、None は「調べられなかった」で、後者は素通しを明示する。
    """
    if not isinstance(tool_response, dict):
        return None

    # Write での新規作成: structuredPatch は空で、content に全文が入る (実測)
    if tool_response.get("type") == "create":
        content = tool_response.get("content")
        if content is None and isinstance(tool_input, dict):
            content = tool_input.get("content")
        if not isinstance(content, str):
            return None
        return set(range(1, len(content.splitlines()) + 1))

    patch = tool_response.get("structuredPatch")
    if not isinstance(patch, list):
        return None

    added: set[int] = set()
    for hunk in patch:
        if not isinstance(hunk, dict):
            return None
        cursor = hunk.get("newStart")
        lines = hunk.get("lines")
        if not isinstance(cursor, int) or not isinstance(lines, list):
            return None
        for line in lines:
            if not isinstance(line, str):
                return None
            if line.startswith("+"):
                added.add(cursor)
                cursor += 1
            elif line.startswith("-"):
                continue
            else:  # 文脈行
                cursor += 1
    return added


def rescan_line_numbers(tool_response: Any) -> set[int]:
    """削除を含む hunk の、新しいファイル側に残っている行 (1 始まり)。

    無いと何が静かに通るか:
        `npm test || true` の直前にあった**理由コメントだけを削除する編集**は
        追加行が 1 行も無いので、追加行だけを見る検査では素通しする。結果、
        「理由の書かれていない握り潰し」が無警告で出来上がる。**理由を消すのは
        握り潰しを新しく書くのと同じ**なので、削除を含む hunk は残存行も見る。

    追加行を含まない hunk では何も足さない (無関係な削除で既存コードを咎めないため)。
    """
    if not isinstance(tool_response, dict):
        return set()
    patch = tool_response.get("structuredPatch")
    if not isinstance(patch, list):
        return set()

    touched: set[int] = set()
    for hunk in patch:
        if not isinstance(hunk, dict):
            continue
        cursor = hunk.get("newStart")
        lines = hunk.get("lines")
        if not isinstance(cursor, int) or not isinstance(lines, list):
            continue
        if not any(isinstance(line, str) and line.startswith("-") for line in lines):
            continue
        for line in lines:
            if not isinstance(line, str):
                break
            if line.startswith("-"):
                continue
            touched.add(cursor)
            cursor += 1
    return touched


def _strip_urls(text: str) -> str:
    return _URL_SCHEME.sub("scheme-", text)


def is_comment_line(text: str) -> bool:
    return bool(_COMMENT_ONLY.match(text))


def has_inline_comment(text: str) -> bool:
    """行内にコメントが付いているか。**迷ったら「付いている」に倒す** (誤ブロックより誤許容)。"""
    stripped = _strip_urls(text)
    return "#" in stripped or "//" in stripped or "/*" in stripped or "<!--" in stripped


def has_nearby_comment(lines: list[str], line_no: int) -> bool:
    """近傍 (同じ行 / 直前の非空行 / 直後の非空行) にコメントがあるか。

    ここが「コメントを書けば通る」の実体。規約は「握り潰すな」ではなく
    「それで何が見えなくなるかを書け」なので、書いてあれば違反ではない。
    """
    idx = line_no - 1
    if idx < 0 or idx >= len(lines):
        return False
    if has_inline_comment(lines[idx]):
        return True
    for step in (-1, 1):
        cursor = idx + step
        while 0 <= cursor < len(lines) and not lines[cursor].strip():
            cursor += step
        if 0 <= cursor < len(lines) and is_comment_line(lines[cursor]):
            return True
    return False


def _next_meaningful(lines: list[str], idx: int) -> tuple[int, str] | None:
    """空行を飛ばした次の行。コメント行はここでは飛ばさない (飛ばすと免除が壊れる)。"""
    cursor = idx + 1
    while cursor < len(lines) and not lines[cursor].strip():
        cursor += 1
    if cursor >= len(lines):
        return None
    return cursor, lines[cursor]


def find_swallows(
    lines: list[str], added: set[int], rescan: set[int] | frozenset[int] = frozenset()
) -> list[Finding]:
    """この編集が触った行のうち、近傍にコメントの無い握り潰しを返す。

    `added` = この編集で足した行。`rescan` = 削除を含む hunk に残った行
    (理由コメントだけを消した編集を拾うため)。どちらで見つけたかは Finding に残す。
    """
    findings: list[Finding] = []
    for line_no in sorted(set(added) | set(rescan)):
        idx = line_no - 1
        if idx < 0 or idx >= len(lines):
            continue
        text = lines[idx]
        label = _match_label(lines, idx, text)
        if label is None:
            continue
        if has_nearby_comment(lines, line_no):
            continue
        source = "追加" if line_no in added else "残置"
        findings.append(Finding(line_no, label, text.strip(), source))
    return findings


def _match_label(lines: list[str], idx: int, text: str) -> str | None:
    if is_comment_line(text):
        # コメント行そのものは対象外 (規約の説明文や、免除のために書いたコメント)
        return None
    for pattern, label in _SINGLE_LINE_PATTERNS:
        if pattern.search(text):
            return label
    if _CATCH_OPEN.search(text):
        following = _next_meaningful(lines, idx)
        if following and following[1].strip() == "}":
            return "空の catch (例外を捨てている)"
    if _EXCEPT_OPEN.match(text):
        following = _next_meaningful(lines, idx)
        if following and following[1].strip() == "pass":
            return "空の except (例外を捨てている)"
    return None


def format_reason(file_path: str, findings: list[Finding]) -> str:
    sources = {f.source for f in findings}
    if sources == {"残置"}:
        what = "この編集で理由コメントが消え、理由の書かれていない握り潰しだけが残りました"
    elif "残置" in sources:
        what = "理由の書かれていない握り潰しを追加、または理由コメントを消して残置しました"
    else:
        what = "理由の書かれていない握り潰しを新しく追加しました"
    head = (
        f"{file_path} に、{what} "
        f"({len(findings)} 件)。CLAUDE.md / AGENTS.md が「このリポジトリで最も繰り返している"
        "事故」と名指ししている形です。"
    )
    body = "\n".join(f"  L{f.line_no}: [{f.source}] {f.label}\n    {f.text}" for f in findings)
    tail = (
        "この編集は既に適用済みで、hook では取り消せません。次のどちらかにしてください:\n"
        "  - 握り潰しをやめる (失敗が見える形にする)\n"
        "  - 残すなら、**それで何が見えなくなるか**を近傍のコメントに書く "
        "(コメントがあればこの検査は通ります)"
    )
    return f"{head}\n{body}\n{tail}"


def handle(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
        return None

    tool_input = event.get("tool_input") or {}
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not is_checked_path(file_path):
        return None

    added = added_line_numbers(event.get("tool_response"), tool_input)
    if added is None:
        return hook_io.passthrough(
            f"{file_path} の差分 (structuredPatch) を解釈できず、握り潰しの検査をしていない"
        )
    rescan = rescan_line_numbers(event.get("tool_response"))
    if not added and not rescan:
        return None

    try:
        lines = Path(file_path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return hook_io.passthrough(
            f"{file_path} を読めず、握り潰しの検査をしていない ({exc.strerror})"
        )

    findings = find_swallows(lines, added, rescan)
    if not findings:
        return None
    return hook_io.block(format_reason(file_path, findings))


if __name__ == "__main__":
    raise SystemExit(hook_io.run(handle))
