"""[契約] README の宣言 (エンドポイント表 / 環境変数表) を実装と機械照合する (#487)。

層の判定 (PR #492 Codex P1): これはドメインルールの検証ではなく「**宣言側
(README) と実装側 (FastAPI routes / Settings) の 2 箇所に分かれた同じ事実が
ズレていないか**」の機械照合なので、単体ではなく**契約**
(docs/testing/strategy.md §2.1 と同じ性質 — 値の妥当性を見ず、宣言の集合差だけを
見る)。前例: cicd/scripts/env/test_mgmt_layer_tag_contract.py。

週次 debt-review (#480) で README とコードの乖離 (エンドポイント 3/6 欠落・
環境変数 10/23 欠落) が major finding になった。手で直すだけでは再発するので、
README の 2 表をパースして実装 (FastAPI `app.routes` / `Settings.model_fields`)
と両方向で突合する。

パース対象の前提 (README 側の約束):
- 「## エンドポイント」「## 環境変数」の見出し直下にそれぞれ 1 つの markdown 表がある
- 環境変数表は 1 行 1 変数 (複数変数を ` / ` で 1 セルにまとめない)
- #489 削除予定の変数は表ではなく blockquote (`> `) の脚注に書く
"""

import re
from pathlib import Path

from fastapi.routing import APIRoute

from app.config import Settings
from app.main import app

README = Path(__file__).resolve().parent.parent / "README.md"

# #489 で削除予定の未使用フィールド (参照箇所ゼロ)。README には「削除予定」の
# 脚注として載せ、表には載せない意図的除外。#489 が着地して Settings から
# フィールドが消えたら、この allowlist は「Settings に無い名前」として
# 下の対称チェックが赤にするので、消し忘れたまま残ることはない。README 側の
# 脚注も allowlist と突合している (脚注 ↔ allowlist の同期チェック) ので、
# allowlist だけ消して脚注を残す・その逆、のどちらも赤になる。
DEPRECATED_NOT_IN_README = {"VOICEVOX_URL", "VOICEVOX_ENABLED"}


def _section(markdown: str, heading: str) -> str:
    """`## {heading}` から次の `## ` (または EOF) までを返す。見出しが無ければ落とす。"""
    match = re.search(
        rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)",
        markdown,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, (
        f"README に「## {heading}」見出しが無い (見出しを変えたらこのテストも追従させる)"
    )
    return match.group(1)


def _table_rows(section: str) -> list[list[str]]:
    """セクション内の markdown 表のデータ行 (ヘッダ・区切り行を除く) をセル配列で返す。"""
    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        # 区切り行 (|---|---|) を除外
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue
        rows.append(cells)
    assert rows, "セクション内に markdown 表が見つからない"
    return rows[1:]  # 先頭はヘッダ行


def test_契約_README_のエンドポイント表が_FastAPI_の_routes_と一致する() -> None:
    """無いと何が静かに通るか: エンドポイントを足しても (消しても) README が古いまま緑で通る。"""
    rows = _table_rows(_section(README.read_text(encoding="utf-8"), "エンドポイント"))
    readme_endpoints = set()
    for cells in rows:
        method, path = cells[0], cells[1]
        path_match = re.fullmatch(r"`([^`]+)`", path)
        assert path_match, f"エンドポイント表の Path セルが `code` 形式でない: {path!r}"
        readme_endpoints.add((method.upper(), path_match.group(1)))

    app_endpoints = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)  # /docs /openapi.json 等の自動生成 Route を除外
        for method in route.methods
    }

    missing_in_readme = app_endpoints - readme_endpoints
    stale_in_readme = readme_endpoints - app_endpoints
    assert not missing_in_readme, (
        f"実装にあるのに README のエンドポイント表に無い: {sorted(missing_in_readme)}"
    )
    assert not stale_in_readme, (
        f"README のエンドポイント表にあるのに実装に無い: {sorted(stale_in_readme)}"
    )


def test_契約_README_の環境変数表が_Settings_のフィールドと一致する() -> None:
    """無いと何が静かに通るか: Settings にフィールドを足しても README が古いまま緑で通る
    (逆に、消したフィールドの行が README に残っても緑で通る)。"""
    section = _section(README.read_text(encoding="utf-8"), "環境変数")
    rows = _table_rows(section)
    readme_vars = set()
    for cells in rows:
        name_match = re.fullmatch(r"`([^`]+)`", cells[0])
        assert name_match, (
            f"環境変数表の変数名セルが `code` 1 個の形式でない (1 行 1 変数の約束): {cells[0]!r}"
        )
        readme_vars.add(name_match.group(1))

    settings_vars = {name.upper() for name in Settings.model_fields}

    overlap = DEPRECATED_NOT_IN_README & readme_vars
    assert not overlap, (
        f"#489 削除予定の変数が README の表に載っている (脚注に留める約束): {sorted(overlap)}"
    )
    stale_allowlist = DEPRECATED_NOT_IN_README - settings_vars
    assert not stale_allowlist, (
        f"allowlist の変数が Settings にもう無い (#489 着地済み?) — allowlist と README 脚注を消す: {sorted(stale_allowlist)}"
    )

    # 脚注 ↔ allowlist の同期 (PR #492 Codex P2): allowlist を空にするだけでは
    # README の脚注が残ったまま緑になってしまう。脚注 (blockquote 行) に出てくる
    # 環境変数名の集合が allowlist とちょうど一致することを要求する —
    # #489 着地時は「脚注の消し忘れ」が、載せ忘れ時は「脚注が無い」が赤になる。
    footnote_lines = [
        line for line in section.splitlines() if line.lstrip().startswith(">")
    ]
    footnote_vars = {
        name
        for line in footnote_lines
        for name in re.findall(r"`([A-Z][A-Z0-9_]+)`", line)
    }
    missing_in_footnote = DEPRECATED_NOT_IN_README - footnote_vars
    stale_in_footnote = footnote_vars - DEPRECATED_NOT_IN_README
    assert not missing_in_footnote, (
        f"allowlist の変数が README の脚注に書かれていない (削除予定だと読者に伝わらない): {sorted(missing_in_footnote)}"
    )
    assert not stale_in_footnote, (
        f"README の脚注にあるのに allowlist に無い変数が残っている (#489 着地後の消し忘れ?): {sorted(stale_in_footnote)}"
    )

    missing_in_readme = settings_vars - readme_vars - DEPRECATED_NOT_IN_README
    stale_in_readme = readme_vars - settings_vars
    assert not missing_in_readme, (
        f"Settings にあるのに README の環境変数表に無い: {sorted(missing_in_readme)}"
    )
    assert not stale_in_readme, (
        f"README の環境変数表にあるのに Settings に無い: {sorted(stale_in_readme)}"
    )
