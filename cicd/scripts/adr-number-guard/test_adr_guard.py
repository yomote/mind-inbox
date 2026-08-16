"""adr-number-guard の判定純関数のテスト (Issue #381)。

無いと何が静かに通るか:
    この判定は CI (adr_guard.py main) と review-gate (gate_conflicts) の両方から
    呼ばれる採番門の本体。ここが壊れると「同じ番号の ADR が 2 つ main に入る」が
    静かに通る — しかも guard の run 自体は緑のまま、という #381 と同型の腐り方をする
    (過去の実衝突: 0015→0019 / 0026→0027 / PR #222 の 0048 二重化寸前)。
"""

from adr_guard import (
    Violation,
    adr_number_of,
    adr_numbers,
    gate_conflicts,
    judge,
    next_number,
    parse_retired,
)

# PR #222 の実事例をそのまま使う: PR は 0048-readonly-… を追加、
# その後 main に別の 0048-child-… が着地した
PR_ADR = "docs/adr/0048-readonly-investigation-identity-on-unprotected-branch.md"
MAIN_ADR = "docs/adr/0048-child-sessions-are-usable-again-with-a-one-way-poke-channel.md"
OLD_MAIN = ["docs/adr/0046-a.md", "docs/adr/0047-b.md"]
NEW_MAIN = OLD_MAIN + [MAIN_ADR]
NO_RETIRED: set[int] = set()


def judge_simple(head: list[str], main: list[str]) -> list[Violation]:
    return judge(head, main, NO_RETIRED, NO_RETIRED, NO_RETIRED)


# ---- パスの解釈 ----


def test_番号付きadrだけを数える() -> None:
    assert adr_number_of(PR_ADR) == 48
    assert adr_number_of("docs/adr/template.md") is None
    assert adr_number_of("docs/adr/README.md") is None
    # archive 配下・ADR 外は対象外 (archive は番号を落として退避される)
    assert adr_number_of("docs/adr/archive/0001-old.md") is None
    assert adr_number_of("apps/bff/0048-x.md") is None
    assert adr_numbers(["docs/adr/template.md"]) == {}


def test_退役一覧はコメントと空行を無視する() -> None:
    text = "# 退役番号\n0031\n\n  0033  \nabc\n#0035\n"
    assert parse_retired(text) == {31, 33}


# ---- #381 の本体: 緑が腐る経路 ----


def test_旧baseでは緑_今のmainでは衝突として検出される() -> None:
    """#381 の再現: 同じ PR 内容でも、比較先が「イベント時点の base」だと緑、
    「今の main」だと赤 — 比較先を run 時点の main にすることが修正の本体。"""
    head = OLD_MAIN + [PR_ADR]
    # PR イベント時点の base (0048 未着地) との比較 = 旧実装の見え方 → 緑
    assert judge_simple(head, OLD_MAIN) == []
    # その後 main に 0048-child-… が着地 → 今の main と比較すれば衝突
    violations = judge_simple(head, NEW_MAIN)
    assert [v.kind for v in violations] == ["collision"]
    assert violations[0].number == 48
    assert violations[0].head_paths == (PR_ADR,)
    assert violations[0].base_paths == (MAIN_ADR,)


def test_mainと同名のファイルは衝突ではない() -> None:
    # 既存 ADR の修正 (同名) や main 追随のマージは衝突と読まない
    assert judge_simple(NEW_MAIN, NEW_MAIN) == []


def test_同番号での改名は衝突として止める() -> None:
    # 旧シェル実装と同じ挙動: 番号を保ったままの改名も「別名で同番号」なので赤
    head = ["docs/adr/0047-renamed.md"]
    violations = judge_simple(head, ["docs/adr/0047-b.md"])
    assert [v.kind for v in violations] == ["collision"]


def test_pr内の同番号重複を止める() -> None:
    head = ["docs/adr/0050-a.md", "docs/adr/0050-b.md"]
    violations = judge_simple(head, [])
    assert [v.kind for v in violations] == ["dup"]
    assert violations[0].head_paths == ("docs/adr/0050-a.md", "docs/adr/0050-b.md")


# ---- 退役番号 ----


def test_退役一覧の行削除はmergebase基準で検出する() -> None:
    # PR が退役番号 0031 の行を消した → 赤
    violations = judge([], [], set(), {31}, {31})
    assert [v.kind for v in violations] == ["retired-removed"]
    assert violations[0].number == 31


def test_pr分岐後にmainへ足された退役番号は削除と誤検知しない() -> None:
    """比較先を「今の main」へ変えたときの新しい誤検知経路を塞ぐ。

    無いと何が静かに通るかの逆 (何が不当に赤くなるか): main が退役番号を足すたび、
    その前に分岐した全 open PR の guard が「行を消した」と赤くなり、門が信用を失う。
    """
    # merge-base 時点は {31}、main はその後 {31, 32} に。PR は触っていない ({31} のまま)
    assert judge([], [], {31}, {31, 32}, {31}) == []


def test_退役番号の再利用はmainとprの和集合で止める() -> None:
    # PR 分岐後に main 側で 0050 が退役 → PR の 0050 追加はマージ時に再利用になる
    head = ["docs/adr/0050-new.md"]
    violations = judge(head, [], NO_RETIRED, {50}, NO_RETIRED)
    assert [v.kind for v in violations] == ["retired-reuse"]
    # PR 側の一覧にだけある番号でも同じ (一覧を導入する PR 自身にも効く)
    violations = judge(head, [], {50}, NO_RETIRED, NO_RETIRED)
    assert [v.kind for v in violations] == ["retired-reuse"]


def test_次に使える番号は実ファイルと退役番号の最大プラス1() -> None:
    assert next_number(["docs/adr/0047-b.md"], {53}) == 54
    assert next_number(["docs/adr/0047-b.md"], set()) == 48
    assert next_number([], set()) is None


# ---- review-gate 用 (gate_conflicts) ----


def test_gateはadr変更なしなら空() -> None:
    assert gate_conflicts([("apps/bff/x.ts", "modified")], NEW_MAIN, NO_RETIRED) == []


def test_gateは追加adrの衝突を今のmainで検出する() -> None:
    messages = gate_conflicts([(PR_ADR, "added")], NEW_MAIN, NO_RETIRED)
    assert messages == [f"ADR 0048 が今の main と衝突 ({MAIN_ADR})"]


def test_gateは既存adrの修正を衝突と読まない() -> None:
    assert gate_conflicts([(MAIN_ADR, "modified")], NEW_MAIN, NO_RETIRED) == []


def test_gateは削除済みファイルを対象にしない() -> None:
    # archive への退避 (元ファイルの削除) を衝突と読まない
    assert gate_conflicts([(MAIN_ADR, "removed")], NEW_MAIN, NO_RETIRED) == []


def test_gateは退役番号の再利用も止める() -> None:
    messages = gate_conflicts([("docs/adr/0031-zombie.md", "added")], OLD_MAIN, {31})
    assert messages == ["ADR 0031 は退役番号 (再利用不可)"]


def test_gateはpr内重複も止める() -> None:
    files = [("docs/adr/0050-a.md", "added"), ("docs/adr/0050-b.md", "added")]
    assert gate_conflicts(files, OLD_MAIN, NO_RETIRED) == ["ADR 0050 が PR 内で重複"]


# main snapshot の取得は review-gate 側 (check.py fetch_main_snapshot — GitHub API) が
# 持ち、テストもそちら (test_check.py)。作業ツリーを読む loader はここに置かない
# (sweep のループ内で腐る — Codex P1 / PR #469)。
