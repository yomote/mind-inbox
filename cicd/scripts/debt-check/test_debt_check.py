"""[L1] 技術負債の機械検出 (ADR 0037)。

無いと何が静かに通るか:
    - コード fence 内の例示リンクを「壊れている」と誤報し、Issue がノイズで
      埋まって誰も読まなくなる (偽陽性は検出器の死)
    - 逆に、実在しないファイルへのリンクを見逃して docs が静かに腐る (偽陰性)
    - 0 件のときに「カバーしていない領域」の明示が落ち、
      **「0 件 = 全部健全」という嘘が緑色の顔をして通る** (silent caps)
    - 走査対象が docs/ に閉じたまま cicd/ 配下のリンクが腐り、
      **見ていないだけの場所が「壊れていない」ことにされる** (Issue #421)
"""

import json
from pathlib import Path

from detect import (
    RECORD_DIR_PREFIXES,
    EXCLUDED_DIR_NAMES,
    FROZEN_RECORD_PREFIXES,
    LINK_SCAN_DIRS,
    MD_REF_SCAN_DIRS,
    MD_REF_SCAN_EXTS,
    UNCOVERED,
    detect_all,
    detect_broken_doc_links,
    detect_placeholder_test_scripts,
    detect_unresolved_md_references,
    is_checkable_relative,
    iter_inline_links,
    iter_md_references,
    iter_reference_scanned_files,
    iter_scanned_markdown,
    main,
    reference_resolves,
    resolve_reference_target,
)


def _repo(tmp_path: Path) -> Path:
    """走査対象のディレクトリを揃えた最小のリポジトリ。

    LINK_SCAN_DIRS から自動生成しない — 定数を書き換える変異でも fixture が
    追従してしまい、走査範囲のテストが「常に自分と一致する」空テストになる。
    走査対象を足したらここも足す (足し忘れは未検査として全テストが落ちる)。
    """
    assert set(LINK_SCAN_DIRS) == {"docs", "cicd"}, "走査対象が変わっている"
    assert set(MD_REF_SCAN_DIRS) == {
        "apps",
        "cicd",
        "docs",
        ".github",
        ".claude",
    }, "引用検査の走査対象が変わっている"
    for name in ("docs", "cicd", "apps", ".github", ".claude"):
        (tmp_path / name).mkdir()
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


def test_l1_cicd配下の壊れたリンクも検出する(tmp_path) -> None:
    """走査対象が docs/ に閉じていないこと (Issue #421)。

    無いと何が静かに通るか:
        `cicd/keys/README.md` のように他ディレクトリを `../../` 越しに指す md が
        あるのに、走査対象が docs/ だけだと参照先が動いてもリンク切れが検出されず、
        週次 debt-check は「0 件」で緑のまま。**見ていないだけの場所が
        「壊れていない」ことにされる。**
    """
    root = _repo(tmp_path)
    (root / "docs" / "runbooks").mkdir()
    (root / "docs" / "runbooks" / "live.md").write_text("# live", encoding="utf-8")
    (root / "cicd" / "keys").mkdir()
    (root / "cicd" / "keys" / "README.md").write_text(
        "[生きてる](../../docs/runbooks/live.md) / [移動済み](../../docs/runbooks/moved.md)",
        encoding="utf-8",
    )
    findings = detect_broken_doc_links(root)
    assert [(f["file"], f["target"]) for f in findings] == [
        ("cicd/keys/README.md", "../../docs/runbooks/moved.md")
    ]


def test_l1_リポジトリ直下のmdも走査するが未対象ツリーは走査しない(tmp_path) -> None:
    """走査範囲の線引きそのものを固定する (Issue #421)。

    無いと何が静かに通るか:
        - 直下: CLAUDE.md / README.md は最も読まれる入口なのに、走査対象から
          外れていると参照先が動いても誰も気づかない
        - 未対象ツリー: apps/ や .claude/ を無自覚に巻き込むと node_modules や
          worktree の md まで検査対象になり、**他人の未完成リンクを毎週報告して**
          Issue がノイズで埋まる (偽陽性は検出器の死)。広げるなら実測してから
        - 除外ディレクトリ: 走査対象の中に居る `.venv` / `node_modules` を拾うと、
          **同じコミットでも実行場所 (CI / ローカル) で結果が変わる**検出器になる
    """
    root = _repo(tmp_path)
    (root / "CLAUDE.md").write_text("[壊れ](docs/gone.md)", encoding="utf-8")
    (root / "apps" / "frontend").mkdir(parents=True)
    (root / "apps" / "frontend" / "README.md").write_text(
        "[壊れ](nope.md)", encoding="utf-8"
    )
    # 除外名は直書きする — EXCLUDED_DIR_NAMES から生成すると、集合から名前を
    # 落とす変異で fixture も一緒に消え、「常に自分と一致する」空テストになる
    assert EXCLUDED_DIR_NAMES == {"node_modules", ".venv", "worktrees", ".git"}
    for excluded in ("node_modules", ".venv", "worktrees", ".git"):
        # 走査対象 (cicd/) の**中に**現れた依存物・worktree・仮想環境。
        # .venv は CI に無くローカルにだけあるので、拾うと同じコミットでも
        # 実行場所で結果が変わる (実測: ai-agent の .venv 配下に md 38 本)
        nested = root / "cicd" / "scripts" / excluded / "dep"
        nested.mkdir(parents=True)
        (nested / "README.md").write_text("[壊れ](nope.md)", encoding="utf-8")
    findings = detect_broken_doc_links(root)
    assert [(f["file"], f["target"]) for f in findings] == [
        ("CLAUDE.md", "docs/gone.md")
    ]
    scanned = {p.relative_to(root).as_posix() for p in iter_scanned_markdown(root)}
    assert scanned == {"CLAUDE.md"}


def test_l1_走査対象が欠けていたら実行経路が前提不足で落ちる(tmp_path, capsys) -> None:
    """workflow と同じ入口 (main) を通す (PR #438 の Codex 指摘)。

    無いと何が静かに通るか:
        走査対象のディレクトリが (改名・移動で) 消えると、検査していないのに
        findings が 0 件になり「異常なし」として緑の run が出る。レポート内の
        finding として出すだけでは「検出処理は走った」ことになってしまうので、
        **run 自体を落とす**。関数を直接呼ぶテストだけだと、この実行経路が
        本当に落ちるかを見ていない。
    """
    root = _repo(tmp_path)
    (root / "cicd").rmdir()
    assert main(["detect.py", str(root)]) == 1
    captured = capsys.readouterr()
    assert "cicd" in captured.err
    assert captured.out == "", "前提不足なのにレポート JSON を出している"


def test_l1_走査対象が揃っていれば実行経路はレポートJSONを出す(
    tmp_path, capsys
) -> None:
    """上の前提不足判定が「常に 1 を返すだけ」になっていないことを押さえる。"""
    root = _repo(tmp_path)
    (root / "cicd" / "keys").mkdir()
    (root / "cicd" / "keys" / "README.md").write_text(
        "[壊れ](../../docs/gone.md)", encoding="utf-8"
    )
    assert main(["detect.py", str(root)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total"] == 1
    assert "`cicd/keys/README.md`" in report["markdown"]


def test_l1_走査対象外のツリーはカバー外領域として明示される() -> None:
    """silent caps 禁止。走査範囲を広げても「見ていない場所」は残るので本文に出す。"""
    uncovered_text = "\n".join(UNCOVERED)
    assert "apps/**" in uncovered_text
    assert ".claude/**" in uncovered_text


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


# ── 解決しない md の引用 (#387 の 2) ─────────────────────────────────────────
#
# **検出対象の文字列をテスト本文に素で置かない。** 置くとこのファイル自身が走査
# 対象になったとき偽の finding が立つ (検出器が自分の testfixture を負債として
# 報告する)。組み立てて作る。
_Q_OPEN, _Q_CLOSE = "\u300c", "\u300d"


def _quote_ref(target: str, phrase: str) -> str:
    """`<target>「<phrase>」` の形の引用を作る。"""
    return f"{target}{_Q_OPEN}{phrase}{_Q_CLOSE}"


def _section_ref(target: str, heading: str) -> str:
    """`<target> の <heading> 節` の形の引用を作る。"""
    return f"{target} の {heading} 節"


def test_単体_引用した文言が参照先に無ければ報告し_在れば報告しない(tmp_path) -> None:
    """#387 の 2 そのもの。

    無いと何が静かに通るか:
        「CLAUDE.md にこう書いてある」と引いたまま、その文言が**別のファイルへ
        移された / 最初から無い**状態が誰にも気づかれずに残る。`broken-doc-links`
        は**リンク先ファイルの実在**しか見ないので、ファイルが在る限り緑のまま
        通る (実際 #385 の移送で 7 箇所がこの形ですり抜けた)。
    """
    root = _repo(tmp_path)
    (root / "CLAUDE.md").write_text(
        "# 規約\n\n- **取れなかったものを異常なしと書かない**\n", encoding="utf-8"
    )
    (root / "cicd" / "a.py").write_text(
        f"# {_quote_ref('CLAUDE.md', '取れなかったものを異常なしと書かない')}\n",
        encoding="utf-8",
    )
    (root / "cicd" / "b.py").write_text(
        f"# {_quote_ref('CLAUDE.md', 'テストファーストで切る')}\n", encoding="utf-8"
    )
    findings = detect_unresolved_md_references(root)
    assert [(f["file"], f["phrase"]) for f in findings] == [
        ("cicd/b.py", "テストファーストで切る")
    ]


def test_単体_折り返しや強調が挟まっても同じ文言なら解決する(tmp_path) -> None:
    """偽陽性は検出器の死 (この repo の既存規律)。

    無いと何が静かに通るか:
        コメントの折り返しで `#` や `*` が挟まっただけの**正しい引用**を毎週
        報告するようになり、Issue がノイズで埋まって本物の finding が読まれなく
        なる。実物では `.github/workflows/github-terraform-check.yml` の引用が
        2 行に折り返されている。
    """
    root = _repo(tmp_path)
    (root / "CLAUDE.md").write_text(
        "- **取れなかったものを異常なしと書かない**\n", encoding="utf-8"
    )
    wrapped = _quote_ref("CLAUDE.md", "取れなかったものを\n#    異常なしと書かない")
    (root / "cicd" / "w.yml").write_text(f"# {wrapped}\n", encoding="utf-8")
    assert detect_unresolved_md_references(root) == []


def test_単体_節の引用は見出しにだけ解決する(tmp_path) -> None:
    """`の X 節` は「節がある」という主張なので、本文一致では通さない。

    無いと何が静かに通るか:
        見出しが消えて本文の一言だけが残った状態を「節はある」と読ませてしまう。
        読み手は目次から辿れず、参照が案内として機能しない。
    """
    root = _repo(tmp_path)
    (root / "cicd" / "CLAUDE.md").write_text(
        "# cicd\n\n## stub fallback を壊さない\n\n本文に 命名規約 とだけ書いてある\n",
        encoding="utf-8",
    )
    (root / "cicd" / "ok.py").write_text(
        f"# {_section_ref('cicd/CLAUDE.md', 'stub fallback を壊さない')}\n",
        encoding="utf-8",
    )
    (root / "cicd" / "ng.py").write_text(
        f"# {_section_ref('cicd/CLAUDE.md', '命名規約')}\n", encoding="utf-8"
    )
    assert [f["file"] for f in detect_unresolved_md_references(root)] == ["cicd/ng.py"]


def test_単体_素のCLAUDE_mdは参照元から遡る階層だけを候補にする(tmp_path) -> None:
    """`CLAUDE.md` とだけ書いたとき、どこを指したことになるか。

    無いと何が静かに通るか:
        リポジトリ中のどの CLAUDE.md でも当たれば合格にすると、
        **読み手が辿り着けない参照**が合格になる (frontend のコードが素の
        `CLAUDE.md` と書いて BFF の CLAUDE.md の文言を引いていた実例 = #387 の 2)。
    """
    root = _repo(tmp_path)
    (root / "CLAUDE.md").write_text("# root\n", encoding="utf-8")
    (root / "apps" / "bff").mkdir(parents=True)
    (root / "apps" / "bff" / "CLAUDE.md").write_text(
        "# bff\n\nstub fallback を壊さない\n", encoding="utf-8"
    )
    (root / "apps" / "frontend").mkdir(parents=True)
    (root / "apps" / "frontend" / "x.ts").write_text(
        f"// {_quote_ref('CLAUDE.md', 'stub fallback を壊さない')}\n", encoding="utf-8"
    )
    assert [f["file"] for f in detect_unresolved_md_references(root)] == [
        "apps/frontend/x.ts"
    ]
    # 明示的にパスで書けば解決する (= 直し方が存在する)
    (root / "apps" / "frontend" / "x.ts").write_text(
        f"// {_quote_ref('apps/bff/CLAUDE.md', 'stub fallback を壊さない')}\n",
        encoding="utf-8",
    )
    assert detect_unresolved_md_references(root) == []
    # 候補の解決規則そのものも固定する (相対パスでも辿れる)
    candidates = resolve_reference_target(
        root / "apps" / "frontend" / "x.ts", "../bff/CLAUDE.md", root
    )
    assert [c.relative_to(root).as_posix() for c in candidates] == [
        "apps/bff/CLAUDE.md"
    ]


def test_単体_参照先のmdが見つからない引用は報告しない(tmp_path) -> None:
    """UNCOVERED に明示した線引き。

    無いと何が静かに通るか:
        `$SCRIPT_DIR/README.md` のような**変数入りのパス表記**を毎週「壊れている」
        と報告し、直しようのない finding で Issue が埋まる (偽陽性は検出器の死)。
        代わりに「報告しない」ことを UNCOVERED に書いて、狭さを毎回見せている。
    """
    root = _repo(tmp_path)
    (root / "cicd" / "s.sh").write_text(
        f"# {_quote_ref('$SCRIPT_DIR/README.md', 'App 権限の最小化と根拠')}\n",
        encoding="utf-8",
    )
    assert detect_unresolved_md_references(root) == []
    assert any("見つからない引用" in item for item in UNCOVERED), (
        "報告しない線引きを UNCOVERED に書いていない (silent caps)"
    )


def test_単体_凍結された記録は走査しない(tmp_path) -> None:
    """ADR 本文・実測記録・セッション記録は「当時の引用」が正しい姿。

    無いと何が静かに通るか:
        - 過去の記録を現在の文面に追随させろという finding が毎週立ち、直すと
          **記録が記録でなくなる** (書いた時点で何と書いてあったかが失われる)
        - `Accepted` の ADR 本文 (AGENTS.md) と `docs/reviews/` の実測記録
          (`docs/documentation/strategy.md`) は規約で書き換えを禁じられているので、
          報告しても**直せない finding が毎週残り続ける** (ノイズで本物が読まれなくなる /
          PR #512 Codex P1)
        - 逆に除外が効きすぎると、**現役の案内である索引 (README.md)** の腐った参照まで
          見逃す
    """
    root = _repo(tmp_path)
    assert FROZEN_RECORD_PREFIXES == ("docs/debrief/journal.md",), "凍結範囲が変わっている"
    assert RECORD_DIR_PREFIXES == (
        "docs/adr/",
        "docs/reviews/",
    ), "記録ディレクトリが変わっている"
    (root / "CLAUDE.md").write_text("# root\n", encoding="utf-8")
    stale = f"- {_quote_ref('CLAUDE.md', '当時はこう書いてあった')}\n"
    (root / "docs" / "adr" / "archive" / "operations").mkdir(parents=True)
    (root / "docs" / "adr" / "archive" / "operations" / "old.md").write_text(
        stale, encoding="utf-8"
    )
    (root / "docs" / "adr" / "0001-x.md").write_text(stale, encoding="utf-8")
    # 実測記録も同じ (「測定日時点の事実として残す」= 書き換えられない / PR #512 Codex P1)
    (root / "docs" / "reviews").mkdir(parents=True)
    (root / "docs" / "reviews" / "analysis-2026-08-12.md").write_text(
        stale, encoding="utf-8"
    )
    (root / "docs" / "debrief").mkdir(parents=True)
    (root / "docs" / "debrief" / "journal.md").write_text(stale, encoding="utf-8")
    assert detect_unresolved_md_references(root) == []
    # 索引と、凍結範囲の外に同じものを置けば報告される (= 除外が効きすぎていない)
    (root / "docs" / "adr" / "README.md").write_text(stale, encoding="utf-8")
    (root / "docs" / "adr" / "archive" / "README.md").write_text(stale, encoding="utf-8")
    (root / "docs" / "reviews" / "README.md").write_text(stale, encoding="utf-8")
    (root / "docs" / "live.md").write_text(stale, encoding="utf-8")
    assert sorted(f["file"] for f in detect_unresolved_md_references(root)) == [
        "docs/adr/README.md",
        "docs/adr/archive/README.md",
        "docs/live.md",
        "docs/reviews/README.md",
    ]


def test_単体_引用の切り出しと照合は純粋関数で決まる() -> None:
    """判定の 1 行 (`iter_md_references` / `reference_resolves`) を直接押さえる。

    無いと何が静かに通るか:
        走査 (IO) と一緒にしか検査していないと、**照合の緩め方** (例: 見出し限定を
        本文一致に変える / 正規化で文字を落とす) が偽陽性・偽陰性として現れるまで
        気づけない。
    """
    quote = _quote_ref("apps/bff/CLAUDE.md", "副作用を伴うツール呼び出し")
    assert iter_md_references(f"# {quote}") == [
        ("apps/bff/CLAUDE.md", "副作用を伴うツール呼び出し", "quote")
    ]
    assert iter_md_references(
        f"# {_section_ref('cicd/CLAUDE.md', 'リソース命名')}"
    ) == [("cicd/CLAUDE.md", "リソース命名", "section")]
    body = "# 見出し\n\n本文に **副作用を伴う** ツール呼び出し\n"
    assert reference_resolves("見出し", "section", body)
    assert not reference_resolves("本文に", "section", body), (
        "見出し限定のはずが本文でも通っている"
    )
    assert reference_resolves("副作用を伴う ツール呼び出し", "quote", body)
    assert not reference_resolves("存在しない文言", "quote", body)


def test_単体_新しい検出器がレポートに載っている(tmp_path) -> None:
    """検出器を書いても detect_all に繋がなければ 1 度も走らない。

    無いと何が静かに通るか:
        週次 run のレポートに検出器の節ごと現れず、**動いていないことに誰も
        気づかない** (0 件と未実行が同じ見た目になる)。
    """
    root = _repo(tmp_path)
    report = detect_all(root)
    ids = [d["id"] for d in report["detectors"]]
    assert "unresolved-md-references" in ids
    section = next(
        d for d in report["detectors"] if d["id"] == "unresolved-md-references"
    )
    assert section["name"] in report["markdown"]


def test_単体_fenced_code_の中のコメントは見出しに数えない(tmp_path) -> None:
    """PR #512 Codex P2。

    無いと何が静かに通るか:
        参照先に ```sh ブロックがあると、その中の `# 何とか` がシェルのコメントのまま
        ATX 見出しとして数えられる。**実際には削除済みの節への参照が「解決済み」に化け、
        検証できていないものが 0 件に混じる**。
    """
    body = "# 本物の見出し\n\n```sh\n# 偽の節\necho hi\n```\n"
    assert reference_resolves("本物の見出し", "section", body)
    assert not reference_resolves("偽の節", "section", body), (
        "コード例の中のコメントを見出しとして数えている"
    )
    root = _repo(tmp_path)
    (root / "cicd" / "CLAUDE.md").write_text(body, encoding="utf-8")
    (root / "cicd" / "x.py").write_text(
        f"# {_section_ref('cicd/CLAUDE.md', '偽の節')}\n", encoding="utf-8"
    )
    assert [f["file"] for f in detect_unresolved_md_references(root)] == ["cicd/x.py"]


def test_単体_文言そのものの文字は照合で落とさない() -> None:
    """PR #512 Codex P2 (2 ラウンド分)。

    無いと何が静かに通るか:
        正規化で落としてよいのは**構造上の装飾** (強調・鉤括弧・行頭のコメント記号・
        改行) だけ。文言そのものの文字まで落とすと、**まさに検出したい文言変更が
        0 件で通る**:
        - `_`: `auto_merge` と `automerge` が同じ文言になる (識別子の改名)
        - `?` `！`: 「無いと何が静かに通るか?」から `?` が消えても気づけない
    """
    assert not reference_resolves("auto_merge", "quote", "automerge を使う")
    assert reference_resolves("auto_merge", "quote", "**auto_merge** を使う")
    assert not reference_resolves("静かに通るか?", "quote", "静かに通るか を書く")
    assert not reference_resolves("大事だ！", "quote", "大事だ と書く")
    # 構造上の装飾は落とす (折り返しの `#` / 強調 / 全角空白)
    assert reference_resolves("静かに通るか?", "quote", "**静かに通るか?**")


def test_単体_引用検査の対象拡張子には_tf_と_bicep_が入っている(tmp_path) -> None:
    """PR #512 Codex P2。

    無いと何が静かに通るか:
        走査対象に `cicd` を掲げながら拡張子を絞ると、実在する
        `cicd/github/terraform/*.tf` や `cicd/iac/*.bicep` の md 引用が**一度も
        検査されない**。参照先の文言が動いても 0 件のままで、**見ていないだけの範囲が
        「壊れていない」ことにされる**。
    """
    # 集合から生成しない — 拡張子を落とす変異で fixture も一緒に消え、
    # 「常に自分と一致する」空テストになる
    assert {".tf", ".bicep"} <= MD_REF_SCAN_EXTS, "宣言ファイルが引用検査の対象外"
    root = _repo(tmp_path)
    (root / "cicd" / "CLAUDE.md").write_text("# cicd\n\n実在する文言\n", encoding="utf-8")
    for name in ("a.tf", "b.bicep"):
        (root / "cicd" / name).write_text(
            f"// {_quote_ref('cicd/CLAUDE.md', '実在しない文言')}\n", encoding="utf-8"
        )
    scanned = {p.name for p in iter_reference_scanned_files(root)}
    assert {"a.tf", "b.bicep"} <= scanned
    assert sorted(f["file"] for f in detect_unresolved_md_references(root)) == [
        "cicd/a.tf",
        "cicd/b.bicep",
    ]


def test_単体_つなぎの書き方が違っても同じ引用として拾う(tmp_path) -> None:
    """PR #512 Codex P2 (2 ラウンド分)。

    無いと何が静かに通るか:
        参照先と引用のあいだの**つなぎ**は書き方が何通りもある (実物だけで 5 種)。
        区切りを 1 つずつ足す実装だと**足し忘れた形が「0 件」に化ける** —
        走査しているつもりの範囲に穴が開き、狭さが 0 件と区別できない。
        実際 Codex は 2 ラウンド続けて別の形 (`:` / `N 行目`) を指摘した。
    """
    expected = [("CLAUDE.md", "実在する文言", "quote")]
    quoted = _Q_OPEN + "実在する文言" + _Q_CLOSE
    forms = {
        "直後": "CLAUDE.md" + quoted,
        "コロン": "CLAUDE.md:" + quoted,
        "全角コロン": "CLAUDE.md\uff1a" + quoted,
        "行番号": "CLAUDE.md:12" + quoted,
        "N 行目": "[`CLAUDE.md`](CLAUDE.md) 59 行目" + quoted,
        "助詞つき": "CLAUDE.md の不変条件" + quoted,
        "リンクの閉じ括弧": "[`x`](CLAUDE.md)" + quoted,
    }
    for name, text in forms.items():
        assert iter_md_references(text) == expected, name

    root = _repo(tmp_path)
    (root / "CLAUDE.md").write_text("# root\n\n実在する文言\n", encoding="utf-8")
    (root / "cicd" / "ok.bicep").write_text(
        "// " + forms["コロン"] + "\n", encoding="utf-8"
    )
    (root / "cicd" / "ng.bicep").write_text(
        "// CLAUDE.md 59 行目" + _Q_OPEN + "移動した文言" + _Q_CLOSE + "\n",
        encoding="utf-8",
    )
    assert [f["file"] for f in detect_unresolved_md_references(root)] == [
        "cicd/ng.bicep"
    ]


def test_単体_つなぎに別の参照先が挟まったら引用として拾わない() -> None:
    """つなぎを広げたことで生まれる誤検出を止める (PR #512)。

    無いと何が静かに通るか:
        `§` や `ADR` は**別の参照先**を導入する語。つなぎに含めると
        `domain_rules.md §1 と ADR 0024「X」` の「X」を domain_rules.md からの引用と
        読み違え、**直しようのない finding** を毎週報告する (偽陽性は検出器の死)。
        実物が `apps/bff/src/domain/sentences.property.test.ts` にある。
    """
    quoted = _Q_OPEN + "別の参照先の文言" + _Q_CLOSE
    assert iter_md_references("docs/design/domain_rules.md \u00a71 と ADR 0024" + quoted) == []
    assert iter_md_references("docs/testing/strategy.md \u00a71.2 の" + quoted) == []
    # つなぎに別の md が現れたら、**鉤括弧にいちばん近い方**を参照先にする
    # (`foo.md と bar.md「X」` の「X」は bar.md のもの / PR #512 Codex P2)
    assert iter_md_references("docs/foo.md と docs/bar.md" + quoted) == [
        ("docs/bar.md", "別の参照先の文言", "quote")
    ]


def test_単体_省略記号を含む引用は断片ごとに実在を確かめる() -> None:
    """`A … B` は「あいだを省いた」という意味 (PR #512)。

    無いと何が静かに通るか:
        - 省略を丸ごと 1 文字列として照合すると、**正しい引用が毎週偽陽性**になる
          (実物が `.github/claude/review-rubric.md` にある)
        - 逆に省略があるからと素通しすると、**片方の断片が消えていても気づけない**
        - 順番を見ないと、**参照先で並びが逆転していても**解決済みになる
    """
    body = "前半の文言 — 途中は関係ない話 — 後半の文言\n"
    assert reference_resolves("前半の文言 … 後半の文言", "quote", body)
    assert not reference_resolves("前半の文言 … 消えた文言", "quote", body)
    assert not reference_resolves("消えた文言 … 後半の文言", "quote", body)
    # **出現順まで見る** — 断片が「どこかにある」だけで通すと、参照先で順序が
    # 逆転していても解決済みになる (PR #512 Codex P2)
    assert not reference_resolves("前半の文言 … 後半の文言", "quote", "後半の文言 — 前半の文言")
