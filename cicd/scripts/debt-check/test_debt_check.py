"""[L1] 技術負債の機械検出 (ADR 0037)。

無いと何が静かに通るか:
    - コード fence 内の例示リンクを「壊れている」と誤報し、Issue がノイズで
      埋まって誰も読まなくなる (偽陽性は検出器の死)
    - 逆に、実在しないファイルへのリンクを見逃して docs が静かに腐る (偽陰性)
    - 0 件のときに「カバーしていない領域」の明示が落ち、
      **「0 件 = 全部健全」という嘘が緑色の顔をして通る** (silent caps)
"""

import json
from pathlib import Path

from detect import (
    detect_all,
    detect_broken_doc_links,
    detect_placeholder_test_scripts,
    is_checkable_relative,
    iter_inline_links,
)


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    return tmp_path


def test_l1_壊れた相対リンクを検出し_生きたリンクは検出しない(tmp_path) -> None:
    root = _repo(tmp_path)
    (root / "docs" / "real.md").write_text("# real", encoding="utf-8")
    (root / "docs" / "sub").mkdir()
    (root / "docs" / "index.md").write_text(
        "[生きてる](real.md) / [ディレクトリ](sub) / "
        "[壊れてる](missing.md) / [上へ壊れ](../nope/x.md)",
        encoding="utf-8",
    )
    findings = detect_broken_doc_links(root)
    targets = {f["target"] for f in findings}
    assert targets == {"missing.md", "../nope/x.md"}
    assert all(f["file"] == "docs/index.md" for f in findings)


def test_l1_アンカー付きリンクはファイル部分だけで判定する(tmp_path) -> None:
    root = _repo(tmp_path)
    (root / "docs" / "real.md").write_text("# real", encoding="utf-8")
    (root / "docs" / "index.md").write_text(
        "[節へ](real.md#section) / [ページ内](#local) / [壊れ節](gone.md#x)",
        encoding="utf-8",
    )
    targets = {f["target"] for f in detect_broken_doc_links(root)}
    assert targets == {"gone.md#x"}


def test_l1_外部URLとコードフェンス内は対象外(tmp_path) -> None:
    root = _repo(tmp_path)
    (root / "docs" / "index.md").write_text(
        "\n".join(
            [
                "[外部](https://example.com/x) / [メール](mailto:a@b.c)",
                "```bash",
                "cat [説明](fence-inside-not-a-real-file.md)",
                "```",
            ]
        ),
        encoding="utf-8",
    )
    assert detect_broken_doc_links(root) == []


def test_l1_リポジトリ外へ抜けるリンクとtemplateは誤報しない(tmp_path) -> None:
    """実出力 (2026-08-10) で踏んだ偽陽性 2 種。

    - `../../../issues/4` は GitHub web 上でだけ解決する形 — ローカルで真偽を
      判定できないものを「壊れている」と報告すると、Issue がノイズで埋まる
    - template.md の `NNNN-xxx.md` は意図した placeholder
    """
    root = _repo(tmp_path)
    (root / "docs" / "adr").mkdir()
    (root / "docs" / "adr" / "0001.md").write_text(
        "[Issue](../../../issues/4)", encoding="utf-8"
    )
    (root / "docs" / "adr" / "template.md").write_text(
        "[雛形リンク](NNNN-xxx.md)", encoding="utf-8"
    )
    assert detect_broken_doc_links(root) == []


def test_l1_ルート相対リンクはリポジトリルートに解決して検査する(tmp_path) -> None:
    """](/x.md) 形式を黙って検査対象外にしない (PR #224 レビュー指摘)。

    無いと何が静かに通るか:
        Path の `/` 演算子は右辺が絶対パスだと左辺を捨てるため、/x.md が OS ルートに
        解決され「リポジトリ外」として黙って skip される。壊れたルート相対リンクが
        UNCOVERED にも載らず緑のままになる — 検出器の「静かに嘘をつかない」原則に反する。
    """
    root = _repo(tmp_path)
    (root / "README.md").write_text("# top", encoding="utf-8")
    (root / "docs" / "index.md").write_text(
        "[生きてる](/README.md) / [壊れてる](/nope/gone.md)",
        encoding="utf-8",
    )
    findings = detect_broken_doc_links(root)
    assert {f["target"] for f in findings} == {"/nope/gone.md"}


def test_l1_リンク書式のバリエーションを拾う() -> None:
    text = '[a](x.md) ![img](img/y.png) [b](<z.md>) [c](w.md "title")'
    assert iter_inline_links(text) == ["x.md", "img/y.png", "z.md", "w.md"]
    assert not is_checkable_relative("https://example.com")
    assert not is_checkable_relative("#anchor")
    assert is_checkable_relative("../adr/0001.md")


def test_l1_placeholderのtest_scriptを検出_node_modulesは除外(tmp_path) -> None:
    root = _repo(tmp_path)
    (root / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "test": "vitest",
                    "test:py:voicevox": "echo 'placeholder: tests not yet implemented' && exit 0",
                }
            }
        ),
        encoding="utf-8",
    )
    nm = root / "node_modules" / "dep"
    nm.mkdir(parents=True)
    (nm / "package.json").write_text(
        json.dumps({"scripts": {"x": "echo placeholder"}}), encoding="utf-8"
    )
    findings = detect_placeholder_test_scripts(root)
    assert [(f["file"], f["script"]) for f in findings] == [
        ("package.json", "test:py:voicevox")
    ]


def test_l1_0件でもカバー外領域を必ず明示する(tmp_path) -> None:
    """silent caps 禁止の本丸。0 件レポートが「全部健全」に見えたら検出器の負け。"""
    root = _repo(tmp_path)
    (root / "docs" / "clean.md").write_text("リンクなし", encoding="utf-8")
    report = detect_all(root)
    assert report["total"] == 0
    assert report["uncovered"], "カバー外領域の一覧が空になっている"
    assert "カバーしていない" in report["markdown"]
    assert "0 件 = 全部健全、ではありません" in report["markdown"]


def test_l1_検出ありのmarkdownはIssue本文として成立する(tmp_path) -> None:
    root = _repo(tmp_path)
    (root / "docs" / "index.md").write_text("[壊れ](missing.md)", encoding="utf-8")
    report = detect_all(root)
    assert report["total"] == 1
    md = report["markdown"]
    assert "機械検出できた負債 (1 件)" in md
    assert "`docs/index.md` → `missing.md`" in md
    # 検出があっても uncovered は消えない
    assert "カバーしていない" in md
