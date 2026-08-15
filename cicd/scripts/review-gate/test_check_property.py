"""[単体] review-gate の「機械行」抽出のプロパティテスト (hypothesis / #431)。

仕様の出どころ: `check.py` の `_machine_lines` docstring (ADR 0036 / Issue #380 /
PR #404 の Codex round 1〜5 と security-reviewer 指摘)。そこに書かれている
**1 文の仕様**を、例ではなく性質として置いたのがこのファイル:

> 本人が書いた宣言ではないものを宣言として読まない。
> = 引用 (`>`) / コードフェンス (``` ~~~) / インデントコード / raw HTML の
>   `<pre>` `<code>` ブロック**の中に置かれた行は、中身が何であっても
>   機械判定 (受け入れ・代役レビュー) の材料にならない**。
> 逆に、それらの**外**に置かれた宣言は必ず材料になる。

無いと何が静かに通るか (この 2 方向はどちらも「気づけない」向きに壊れる):

- **過剰に読む** → 第三者が不可視の `<!-- standin-review --> <sha>` を投稿し、
  権限保持者が "Quote reply" するだけで門が開く / 書式の説明としてコードブロックに
  貼った `[pm-accept] <sha>` の例文が受け入れとして読まれる。**門は開いたまま
  マージが普通に成功する**ので、壊れても誰も気づかない。
- **過剰に落とす** → 正規の受け入れ・代役レビューが読まれず門が閉じ続ける。
  こちらは赤で気づけるが、原因が「本文の書き方」に見えるため切り分けが長い。

例ベースのテスト (`test_check.py`) は代表例を残す担当で、こちらは
「どんな包み方・どんな入れ子でも」を道具に探させる担当 (docs/testing/strategy.md §3 /
docs/testing/property-based-testing.md)。

反例が出たときの手順 (fast-check 側と同じ流儀 / property-based-testing.md §3):
hypothesis は最小化した反例と `@reproduce_failure` を出す。**まずその反例を
`test_check.py` に例ベースのテストとして固定してから**実装を直す。実装が仕様より
弱いと分かった場合は直さず、性質を弱めた理由をコメントと PR 本文に残す。

seed は固定しない (fast-check 側と同じ) — 毎回違う入力を探させるのが目的で、
再現は反例の固定化で担保する。`max_examples` は CI 時間との折り合いで既定 100 から
少し上げた 200 (このファイル 8 本で約 5 秒 / 実測。I/O は一切しない純粋関数)。
"""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from check import (
    STANDIN_REVIEW_MARKER,
    _machine_lines,
    has_pm_accept,
    has_standin_review,
    latest_pm_accept_token,
)

# このテスト全体の実行設定。
# - deadline=None: 1 例あたりの時間は測らない。中身は純粋な文字列処理で、
#   CI の共有ランナーで初回だけ遅い、を落ちる理由にしない (落ちるのは性質違反だけ)。
# - max_examples=200: 既定 100 の倍。ここは入力の形 (包み方 × 入れ子) が
#   組み合わせで効く場所なので探索を厚くしたいが、ファイル全体で 5 秒程度に留める。
PROPERTY = settings(max_examples=200, deadline=None)

# 門を開ける側の材料。**実在しそうな hex** にすると平文の生成でうっかり衝突するので、
# 平文アルファベット (下記) と 1 文字も重ならない値にしてある。
HEAD = "abc1234def5678"
SHORT = HEAD[:7]

# 平文行に使う文字。**hex を作れる文字 (a-f / 3-8) を入れない** —
# 入れると「包みの外の地の文が偶然 SHA トークンになる」ことが起きて、
# 性質ではなく生成器の事故でテストが落ちる。
# `<` も入れない (raw HTML の開きタグを偶然作らないため。行頭以外でも効く判定なので)。
_PLAIN_HEAD = st.sampled_from(list("xyzXYZ0129あ絵🙂"))
_PLAIN_TAIL = st.text(
    alphabet=st.sampled_from(list("xyzXYZ0129 .-_/!()[]`~>あ絵🙂")), max_size=20
)
# 行頭が除外構文のどれにもならないことを**構成で**保証する (実装の判定式は写さない)。
PLAIN_LINE = st.one_of(
    st.just(""),
    st.builds(lambda head, tail: head + tail, _PLAIN_HEAD, _PLAIN_TAIL),
)


def _pm_accept_declaration() -> st.SearchStrategy[list[str]]:
    """正規の書式で書かれた PM 受け入れ宣言 (これ単独なら門が開く)。"""
    return st.sampled_from(
        [
            [f"[pm-accept] {SHORT}"],
            [f"[pm-accept] {HEAD} — 受け入れの一言"],
            [f"[pm-accept] `{SHORT}` — バッククォート囲み (このリポジトリの慣習)"],
        ]
    )


def _standin_declaration() -> st.SearchStrategy[list[str]]:
    """正規の書式で書かれた代役 judge レビュー (これ単独なら門が開く)。

    rubric (review-rubric.md Part 6) の必須ヘッダに合わせ、マーカー行と SHA 行は別行。
    """
    return st.sampled_from(
        [
            [STANDIN_REVIEW_MARKER, f"**代役レビュー (`{SHORT}`)** — 合格"],
            [STANDIN_REVIEW_MARKER, "", f"対象 {HEAD}"],
        ]
    )


DECLARATION = st.one_of(_pm_accept_declaration(), _standin_declaration())


@st.composite
def fenced_block(draw: st.DrawFn, inner: list[str]) -> list[str]:
    """inner を**閉じたコードフェンス**で包む。

    中に混ぜる「デコイ」は、CommonMark では**閉じにならない**フェンス行だけ
    (PR #404 Codex round 2 P1 の穴そのもの):
      - 開きより短い同文字の走り (```` ``` ```` の中の ```` `` ````… は 3 未満なので
        そもそもフェンス行にならないため、開きが 4 以上のときだけ生成する)
      - 同文字・同数以上だが info string 付き (```` ```python ````)
      - 別の文字のフェンス (```` ``` ```` の中の `~~~`)
    どれも「閉じた」と誤読されたら中身が機械行へ漏れる = 門が開く。
    """
    char = draw(st.sampled_from(["`", "~"]))
    other = "~" if char == "`" else "`"
    open_len = draw(st.integers(min_value=3, max_value=6))
    open_info = draw(st.sampled_from(["", "text", "python", " js", "diff"]))

    decoy_choices: list[st.SearchStrategy[str]] = [
        # 同文字・同数以上 + info string → CommonMark では閉じではなく中身
        st.builds(
            lambda n, info: char * n + info,
            st.integers(min_value=open_len, max_value=open_len + 2),
            st.sampled_from(["text", "python", " js"]),
        ),
        # 別文字のフェンス → 閉じない
        st.builds(
            lambda n, info: other * n + info,
            st.integers(min_value=3, max_value=8),
            st.sampled_from(["", "text"]),
        ),
    ]
    if open_len > 3:
        # 同文字だが短い走り → 閉じない (フェンス行としては検出される)
        decoy_choices.append(
            st.builds(
                lambda n: char * n, st.integers(min_value=3, max_value=open_len - 1)
            )
        )
    decoys_before = draw(st.lists(st.one_of(decoy_choices), max_size=2))
    decoys_after = draw(st.lists(st.one_of(decoy_choices), max_size=2))

    close_len = draw(st.integers(min_value=open_len, max_value=open_len + 2))
    close_pad = draw(st.sampled_from(["", " ", "  "]))
    return [
        char * open_len + open_info,
        *decoys_before,
        *inner,
        *decoys_after,
        char * close_len + close_pad,
    ]


@st.composite
def quoted_block(draw: st.DrawFn, inner: list[str]) -> list[str]:
    """inner を引用 (`>`) で包む。多重引用・字下げ付きも同じ扱いのはず。"""
    depth = draw(st.integers(min_value=1, max_value=3))
    indent = draw(st.sampled_from(["", " ", "  ", "　"]))
    prefix = indent + "> " * depth
    return [prefix + line for line in inner]


@st.composite
def indented_block(draw: st.DrawFn, inner: list[str]) -> list[str]:
    """inner をインデントコード (行頭 4 スペース以上 / タブ) で包む。"""
    indent = draw(st.sampled_from(["    ", "     ", "        ", "\t", "\t  ", "\t\t"]))
    return [indent + line for line in inner]


@st.composite
def html_code_block(draw: st.DrawFn, inner: list[str]) -> list[str]:
    """inner を raw HTML の `<pre>` / `<code>` ブロックで包む。

    デコイは**不一致の閉じタグ** (`<pre>` の中の `</code>`)。解除してしまうと
    中身が機械行へ漏れる (PR #404 Codex round 5 P1)。
    """
    tag = draw(st.sampled_from(["pre", "code"]))
    other = "code" if tag == "pre" else "pre"
    attrs = draw(st.sampled_from(["", ' class="highlight"', " lang=js"]))
    open_case = draw(st.sampled_from([str.lower, str.upper]))
    decoys = draw(
        st.lists(st.sampled_from([f"</{other}>", f"</{other} >"]), max_size=2)
    )
    return [
        f"<{open_case(tag)}{attrs}>",
        *decoys,
        *inner,
        f"</{tag}>",
    ]


def wrapped(inner: list[str]) -> st.SearchStrategy[list[str]]:
    """「機械判定の材料にしない」と決めた 4 つの包みのどれか。"""
    return st.one_of(
        fenced_block(inner),
        quoted_block(inner),
        indented_block(inner),
        html_code_block(inner),
    )


@st.composite
def body_with_wrapped_declaration(draw: st.DrawFn) -> tuple[str, list[str]]:
    """宣言を包みの中に閉じ込めた本文と、その宣言行を返す。"""
    declaration = draw(DECLARATION)
    block = draw(wrapped(declaration))
    before = draw(st.lists(PLAIN_LINE, max_size=3))
    after = draw(st.lists(PLAIN_LINE, max_size=3))
    return "\n".join([*before, *block, *after]), declaration


@st.composite
def body_with_declaration_outside(draw: st.DrawFn, declaration: list[str]) -> str:
    """宣言を**包みの外**に置き、前後を (閉じた) 包みで挟んだ本文。"""
    noise_before = draw(wrapped(draw(st.lists(PLAIN_LINE, min_size=1, max_size=3))))
    noise_after = draw(wrapped(draw(st.lists(PLAIN_LINE, min_size=1, max_size=3))))
    return "\n".join([*noise_before, *declaration, *noise_after])


@PROPERTY
@given(case=body_with_wrapped_declaration())
def test_declaration_inside_any_block_never_opens_the_gate(
    case: tuple[str, list[str]],
) -> None:
    """[単体] 引用・コードフェンス・インデント・raw HTML の中の宣言は、どう包んでも門を開けない。

    権限保持者 (OWNER) が投稿した本文として渡す = **投稿者の信頼だけは満たした状態**で、
    それでも開かないことを見る (第三者の投稿で落ちても、この性質の証明にならない)。
    """
    body, _ = case
    comments = [(body, "OWNER")]
    assert has_pm_accept(comments, HEAD) is False
    assert has_standin_review(comments, HEAD) is False


@PROPERTY
@given(case=body_with_wrapped_declaration())
def test_declaration_inside_any_block_is_not_carried_over(
    case: tuple[str, list[str]],
) -> None:
    """[単体] 包みの中の受け入れ行は、引き継ぎ (ADR 0042) の材料にもならない。

    無いと: `has_pm_accept` だけを直しても、base 追随の引き継ぎ経路
    (`latest_pm_accept_token`) から同じ穴が残る。
    """
    body, _ = case
    assert latest_pm_accept_token([(body, "OWNER")]) is None


@PROPERTY
@given(declaration=_pm_accept_declaration(), body=st.data())
def test_pm_accept_outside_blocks_always_opens_the_gate(
    declaration: list[str], body: st.DataObject
) -> None:
    """[単体] 閉じた包みに挟まれていても、包みの**外**にある受け入れ行は必ず読まれる。

    無いと何が静かに通るか: 過剰除外はテストが無いと「安全側だから良い」で通るが、
    実際には正規の受け入れが読まれず門が閉じ続ける (原因が本文の書き方に見えるため
    切り分けが長い)。フェンスの閉じ規則を厳しくするたびにここが壊れうる。
    """
    text = body.draw(body_with_declaration_outside(declaration))
    assert has_pm_accept([(text, "OWNER")], HEAD) is True


@PROPERTY
@given(declaration=_standin_declaration(), body=st.data())
def test_standin_review_outside_blocks_always_opens_the_gate(
    declaration: list[str], body: st.DataObject
) -> None:
    """[単体] 包みの外にある代役レビュー (マーカー + 現 head の SHA) は必ず読まれる。"""
    text = body.draw(body_with_declaration_outside(declaration))
    assert has_standin_review([(text, "OWNER")], HEAD) is True


@st.composite
def block_only_body(draw: st.DrawFn) -> str:
    """包み (引用 / フェンス / インデント / raw HTML) だけでできた本文。"""
    blocks = draw(
        st.lists(
            st.one_of(
                st.lists(PLAIN_LINE, min_size=1, max_size=3), DECLARATION
            ).flatmap(wrapped),
            min_size=1,
            max_size=3,
        )
    )
    return "\n".join(line for block in blocks for line in block)


@PROPERTY
@given(body=block_only_body())
def test_a_body_made_only_of_blocks_has_no_machine_lines(body: str) -> None:
    """[単体] 包みだけでできた本文からは、機械行が 1 行も出ない (`_machine_lines` を直接見る)。

    上の 2 本は入口 (`has_pm_accept` / `has_standin_review`) を主語にしているが、
    **その入口はマーカーを桁 0 に限る二重防御を持っている**ため、引用・インデントの
    除外を壊しても入口の性質だけでは観測できない (2026-08-15 のバグ仕込み実験で実測:
    引用の除外を丸ごと外しても、例ベース・入口プロパティのどちらも緑のまま)。
    `_machine_lines` 自身が持つ「包みの中は材料にしない」という契約は、ここで直接固定する
    — 二重防御のどちらか一方だけが静かに死ぬのを防ぐため。
    """
    assert _machine_lines(body) == []


@PROPERTY
@given(lines=st.lists(PLAIN_LINE, max_size=15))
def test_body_without_any_block_syntax_loses_no_line(lines: list[str]) -> None:
    """[単体] 除外構文をひとつも含まない本文は、1 行も落とさずそのまま返る。

    無いと何が静かに通るか: 除外条件を広げすぎた変更 (例: 「4 スペース以上」を
    「1 スペース以上」にする) が、地の文まで落として門を閉じ続ける。
    """
    body = "\n".join(lines)
    assert _machine_lines(body) == body.splitlines()


def _is_subsequence(sub: list[str], whole: list[str]) -> bool:
    it = iter(whole)
    return all(any(item == candidate for candidate in it) for item in sub)


@st.composite
def any_body(draw: st.DrawFn) -> str:
    """構造行と地の文が入り混じった、壊れた markdown も含む本文。"""
    line = st.one_of(
        PLAIN_LINE,
        st.sampled_from(
            [
                "```",
                "````",
                "```python",
                "~~~",
                "~~~~text",
                "> 引用",
                "  > > 多重引用",
                "    インデントコード",
                "\tタブのコード",
                "<pre>",
                "</pre>",
                "<code>",
                "</code>",
                "<CODE lang=js>",
                f"[pm-accept] {SHORT}",
                STANDIN_REVIEW_MARKER,
                "",
            ]
        ),
    )
    return "\n".join(draw(st.lists(line, max_size=18)))


@PROPERTY
@given(body=any_body())
def test_machine_lines_never_rewrites_a_line(body: str) -> None:
    """[単体] 返るのは入力行の**部分列**。行の中身を書き換えたり並べ替えたりしない。

    無いと何が静かに通るか: ここで trim や小文字化などの「正規化」が混ざると、
    桁 0 に限った構文判定 (PM_ACCEPT_LINE_RE / STANDIN_MARKER_LINE_RE) の前提が
    黙って崩れ、字下げされた言及が宣言として読まれるようになる。
    """
    assert _is_subsequence(_machine_lines(body), body.splitlines())


@PROPERTY
@given(body=any_body())
def test_machine_lines_reaches_a_fixed_point(body: str) -> None:
    """[単体] 抽出した機械行だけを並べた本文には、もう除外対象が 1 行も残っていない。

    無いと何が静かに通るか: 「1 回目は落とすが 2 回目は落とす」ような状態依存の除外
    (フェンス状態が本文の途中で開きっぱなしになる等) が入り込み、同じ 1 行が
    前後の文脈次第で宣言として読まれたり読まれなかったりする。

    **素朴な冪等 (`_machine_lines(join(once)) == once`) では書けない** —
    `"\\n".join([""])` は `""` で、`"".splitlines()` は `[]` なので、空行だけの本文で
    join/splitlines の往復が成り立たない (性質ではなく往復側の正規化で落ちる /
    property-based-testing.md の「往復性質は正規化に負ける」と同じ踏み方)。
    そこで右辺も同じ往復を通した形で書いている。
    """
    joined = "\n".join(_machine_lines(body))
    assert _machine_lines(joined) == joined.splitlines()


# --- 生成器そのものの健全性 -------------------------------------------------
# プロパティは「生成器が意図した入力を作れている」ことに全面的に乗る。生成器が
# 静かに壊れると (例: 平文アルファベットに hex 文字が混ざる)、性質は緑のまま
# **何も検証していない**状態になるので、その前提だけは例で固定する。

_HEX_TOKEN = re.compile(r"\b[0-9a-fA-F]{7,40}\b")


@settings(max_examples=300, deadline=None)
@given(line=PLAIN_LINE)
def test_plain_lines_never_contain_a_sha_token(line: str) -> None:
    """[単体] 平文生成器は SHA トークンを作らない (作ると門が偶然開いて性質が空振りする)。"""
    assert not _HEX_TOKEN.search(line)
