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
    "md への参照のうち**引用形でないもの** — 末尾に `/ CLAUDE.md` と添えるだけの帰属表記や、"
    "`§8.3` のような節番号指定は、実在検査ができない (何を引いたのかが本文に無い)。"
    "**直すときは引用形 (`<path>.md「実在する文言」`) に書き換えること** — そうすれば"
    "次に移動したとき unresolved-md-references が拾う",
    "参照先の md 自体が見つからない引用 — パス表記の揺れ (`$SCRIPT_DIR/README.md` 等) と"
    "区別できないため報告しない。ファイルの実在は broken-doc-links が md → md についてのみ見る",
    "凍結された記録の中の引用 — ADR 本文 (`docs/adr/**`。退役分を含む。索引の README.md は除く) と "
    "`docs/debrief/journal.md` は**当時の引用として正しい**ので走査しない。"
    "**`Accepted` の ADR 本文は書き換えない**規約 (AGENTS.md) があり、報告しても直せないため — "
    "腐った参照は supersede か索引側で扱う",
    "引用検査の対象外にある拡張子 — 検査するのは detect.py の MD_REF_SCAN_EXTS に並べた "
    "拡張子だけ (.py/.ts/.tsx/.js/.mjs/.sh/.yml/.yaml/.md/.tf/.bicep)。"
    "ここに無い形式のファイルが md を引用していても 1 行も検査されない",
    "参照先の fenced code の中にある文言 — 「引用」の照合では本文全体を見るので、"
    "**コード例の中の文字列にも当たる**。節 (`の X 節`) の照合では fenced code を落としている",
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

# ── 引用形の md 参照 (`<path>.md「X」` / `<path>.md の X 節`) の走査設定 ─────────
# 「CLAUDE.md にこう書いてある」と引きながら、その文言が**移動済み / 最初から無い**
# 事故を拾う (#387 の 2)。broken-doc-links はリンク先ファイルの実在しか見ないので、
# **ファイルは在るのに中身が別物**というこの形は素通りしていた。
MD_REF_SCAN_DIRS = ("apps", "cicd", "docs", ".github", ".claude")
# 拡張子は**リポジトリ内で実際に md を引用しているもの**を並べる。`.tf` / `.bicep` は
# 実物がある (`cicd/github/terraform/rulesets.tf` が docs/team.md を、`cicd/iac/*.bicep` が
# runbook を引いている) ので入れてある。**ここに無い拡張子は 1 行も検査されない** —
# 走査範囲の狭さは 0 件と区別が付かないので UNCOVERED にも書く (PR #512 Codex P2)。
MD_REF_SCAN_EXTS = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".mjs",
        ".sh",
        ".yml",
        ".yaml",
        ".md",
        ".tf",
        ".bicep",
    }
)
# **凍結された記録は走査しない。** ADR 本文 (退役分を含む) とセッション記録は
# 「その時点の引用」が正しい姿で、現在の文面に追随させると記録として壊れる。加えて
# **`Accepted` の ADR 本文は書き換えない**のがこのリポジトリの規約 (AGENTS.md /
# `docs/adr/README.md`) なので、直せないものを毎週報告しても finding が消えない
# (PR #512 Codex P1)。腐った参照は supersede か索引側で扱う。
# 黙って狭めないため UNCOVERED にも書いてある。
#
# ADR の**索引** (`docs/adr/README.md` / `docs/adr/archive/README.md`) は記録ではなく
# 現役の案内なので走査する。判定は下の is_frozen_record。
FROZEN_RECORD_PREFIXES = ("docs/debrief/journal.md",)
ADR_DIR_PREFIX = "docs/adr/"

# 参照先 md → (任意の閉じ括弧) → 「引用」 または 「の X 節」。
#
# ⚠️ **引用と「例示」は機械では区別できない。** md 名の直後に来る鉤括弧は引用として
# 扱うので、「〜は誤り」という取り消し線が `CLAUDE.md` に溜まる …を md 名が先に来る
# 語順で書くと、**例示のつもりの鉤括弧が引用として拾われる**。逃げ道を検出器側に作る
# (助詞を絞る等) と本物の引用も落ちるので、**書き手側で語順を変えて** (鉤括弧を md 名の
# 前に置く。この段落自身がその書き方) 曖昧さを外すこと。
# 閉じ括弧を許すのは `[`a.md`](path/a.md)「X」` という書き方が普通にあるため。
# 「の <12 文字まで>」を挟めるのは `CLAUDE.md の不変条件「X」` の形を拾うため
# (句読点と鉤括弧は挟めないので、別の文の引用符まで飲み込むことはない)。
_MD_REFERENCE = re.compile(
    r"((?:[\w.-]+/)*[\w.-]+\.md)"
    r"[)\]`]{0,2}\s*"
    r"(?:(?:の[^「」\n。、]{0,12})?「([^」]{2,120})」"
    r"|の\s*([^\s「」()（）,、。]{2,40})\s*節)"
)
# 比較のときに落とす飾り。**強調・引用符・改行・行頭のコメント記号は「文言が同じか」に
# 関係しない** — コメントの折り返しで `#` や `*` が挟まるだけで偽陽性になるのを防ぐ。
#
# ⚠️ **`_` は落とさない。** Markdown の強調記号でもあるが、コード識別子の一部でもある
# (`auto_merge` / `enforce_admins`)。位置を問わず消すと `auto_merge` と `automerge` が
# 同じ文言になり、**まさに検出したい文言変更が 0 件で通る** (PR #512 Codex P2)。
_DECORATION = re.compile(r"[\s*`「」『』\"'　#]")

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


# ── 引用形の md 参照が解決するか (純粋関数) ─────────────────────────────────


def normalize_reference_text(text: str) -> str:
    """文言の比較用に飾りを落とす。

    落とすのは強調 (`**`) / 各種の引用符 / 空白と改行 / 行頭のコメント記号だけで、
    **文字そのものは 1 つも落とさない**。折り返して `#` が挟まっただけの引用を
    「見つからない」と誤報しないための正規化であって、曖昧一致ではない。
    """
    return _DECORATION.sub("", text)


def iter_md_references(text: str) -> list[tuple[str, str, str]]:
    """`<path>.md「X」` / `<path>.md の X 節` を (参照先, 文言, 種別) で返す。

    種別は "quote" (本文のどこかに在ればよい) / "section" (見出しに在ること)。
    """
    references = []
    for match in _MD_REFERENCE.finditer(text):
        target, quoted, section = match.group(1), match.group(2), match.group(3)
        if quoted is not None:
            references.append((target, quoted, "quote"))
        else:
            references.append((target, section, "section"))
    return references


def headings_of(markdown: str) -> str:
    """ATX 見出し行だけを連ねたもの ("section" 参照の照合先)。

    **fenced code の中は見出しではない。** 落とさないと ```sh ブロックの
    `# 何とか` がシェルのコメントのまま見出しに化け、**実際には削除済みの節への参照を
    「解決済み」として 0 件に数える** (PR #512 Codex P2)。
    """
    return "\n".join(
        line
        for line in strip_fenced_code(markdown).splitlines()
        if line.lstrip().startswith("#")
    )


def reference_resolves(phrase: str, kind: str, target_text: str) -> bool:
    """引用した文言が参照先に実在するか。

    "quote" は本文全体、"section" は見出し行だけを照合先にする。
    **部分一致で判定するのは意図的** — 引用は原文の一部を切り出す形が普通で、
    完全一致を要求すると「正しい引用」がほぼ全部落ちる。
    """
    haystack = target_text if kind == "quote" else headings_of(target_text)
    return normalize_reference_text(phrase) in normalize_reference_text(haystack)


def resolve_reference_target(src_file: Path, target: str, root: Path) -> list[Path]:
    """参照先の候補 (実在するものだけ)。

    - パスを含む形 (`apps/bff/CLAUDE.md`) → リポジトリルート相対 / 参照元からの相対
    - 素の名前 (`CLAUDE.md`) → 参照元から**ルートへ遡る各階層**の同名ファイル
      (領域別 CLAUDE.md → root CLAUDE.md の探し方そのもの)

    **他所の領域の CLAUDE.md は候補に入れない** — 例えば frontend のコードが
    `CLAUDE.md` とだけ書いて BFF の CLAUDE.md の文言を引いていたら、それは
    「読み手が辿り着けない参照」なので、解決しないのが正しい。
    """
    if "/" in target:
        candidates = [root / target, src_file.parent / target]
    else:
        candidates = []
        directory = src_file.parent
        while True:
            candidates.append(directory / target)
            if directory == root:
                break
            directory = directory.parent
    resolved = []
    for candidate in candidates:
        absolute = candidate.resolve()
        if absolute.is_file() and absolute.is_relative_to(root):
            resolved.append(absolute)
    return resolved


def unresolved_md_references_in(src_file: Path, text: str, root: Path) -> list[dict]:
    """1 ファイル分の「解決しない引用」。

    **参照先そのものが見つからない引用は報告しない** — `$SCRIPT_DIR/README.md` の
    ような変数入りのパス表記と「本当に無いファイル」を機械では区別できないため
    (UNCOVERED に明示済み)。ここが見るのは「ファイルは在るのに文言が無い」だけ。
    """
    findings = []
    for target, phrase, kind in iter_md_references(text):
        candidates = resolve_reference_target(src_file, target, root)
        if not candidates:
            continue
        for candidate in candidates:
            try:
                target_text = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if reference_resolves(phrase, kind, target_text):
                break
        else:
            findings.append(
                {
                    "file": src_file.relative_to(root).as_posix(),
                    "reference": target,
                    "phrase": " ".join(phrase.split()),
                }
            )
    return findings


def is_frozen_record(relative_path: str) -> bool:
    """引用検査の対象外 (= 当時の引用が正しい記録) か。

    - `docs/debrief/journal.md` — セッション記録
    - `docs/adr/**` の ADR 本文 (退役分の `archive/` を含む) — **`Accepted` の ADR 本文は
      書き換えない**規約 (AGENTS.md) があるので、報告しても直せない
    - ただし ADR の**索引** (`README.md`) は現役の案内なので対象に**含める**
    """
    if relative_path.startswith(FROZEN_RECORD_PREFIXES):
        return True
    if relative_path.startswith(ADR_DIR_PREFIX):
        return not relative_path.endswith("/README.md")
    return False


def iter_reference_scanned_files(root: Path) -> list[Path]:
    """引用検査の対象ファイル (走査範囲は MD_REF_SCAN_DIRS + リポジトリ直下の *.md)。"""
    files: list[Path] = []
    for scan_dir in MD_REF_SCAN_DIRS:
        target = root / scan_dir
        if not target.is_dir():
            continue
        files.extend(f for f in target.rglob("*") if f.suffix in MD_REF_SCAN_EXTS)
    files.extend(root.glob(LINK_SCAN_ROOT_FILES))
    selected = []
    for f in files:
        relative = f.relative_to(root).as_posix()
        if EXCLUDED_DIR_NAMES & set(f.relative_to(root).parts):
            continue
        if is_frozen_record(relative):
            continue
        selected.append(f)
    return sorted(set(selected))


def detect_unresolved_md_references(root: Path) -> list[dict]:
    """`<path>.md「X」` と引きながら、その文言が参照先に無いもの。

    走査対象の実在は main() が事前に検査する (missing_scan_dirs)。
    """
    findings = []
    for src_file in iter_reference_scanned_files(root):
        try:
            text = src_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            findings.append(
                {
                    "file": src_file.relative_to(root).as_posix(),
                    "reference": "(読めません)",
                    "phrase": str(exc),
                }
            )
            continue
        findings.extend(unresolved_md_references_in(src_file, text, root))
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
    """走査対象 (LINK_SCAN_DIRS + MD_REF_SCAN_DIRS) のうち実在しないもの。

    走査対象が (改名・移動で) 消えると、検査していないのに 0 件になり
    「異常なし」として緑になる。main() がこれを**前提不足 (exit 1)** として扱い、
    run を落とすことで沈黙と健全を区別する — レポート内の finding にすると
    「検出処理は走った」ことになってしまう。
    """
    # 引用検査の走査対象 (MD_REF_SCAN_DIRS) も同じ扱い。片方だけ前提を守ると、
    # 「apps/ が消えたので引用を 1 件も見ていない」状態が 0 件で緑になる。
    expected = list(dict.fromkeys(LINK_SCAN_DIRS + MD_REF_SCAN_DIRS))
    return [d for d in expected if not (root / d).is_dir()]


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
        {
            "id": "unresolved-md-references",
            "name": "解決しない md の引用 (`<path>.md「X」` / `の X 節` が参照先に無い)",
            "findings": detect_unresolved_md_references(root),
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
    if "reference" in finding:
        return (
            f"- `{finding['file']}` が `{finding['reference']}` から引いている "
            f"「{finding['phrase']}」が参照先に無い"
        )
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
