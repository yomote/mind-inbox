"""[単体] ADR 採番の前倒し検査 (Issue #392 案 C)。

無いと何が静かに通るか:
    - **退役番号 (0008 / 0011 / …) の再利用**が書き始めの時点で素通りする。
      ファイル名から番号を落としてあるので、`docs/adr/` を数えるだけでは
      使用済みだと分からない
    - `origin/main` を引けなかったときに「衝突なし」と答える — 作業ツリーだけを
      見ると並行セッションが取った番号を見落とし、衝突が再発する
      (0015→0019 / 0026→0027 と同じ事故)
    - **未使用だが規約に合わない番号** (欠番埋め 0011 / 飛び番 9999) が通る —
      CI の `adr-number-guard.yml` は衝突しか見ていないので、ここで通すと誰も止めない
    - ADR 以外の Write (README / template / archive 配下) まで止める
"""

import subprocess
from pathlib import Path

from adr_number_guard import (
    RETIRED_FILE,
    adr_number_of,
    collect_used,
    format_reason,
    handle,
    next_free,
    numbers_in_names,
    parse_retired,
)


def test_単体_ADR_のパスだけ番号を取る() -> None:
    assert adr_number_of("docs/adr/0046-environment.md") == 46
    assert adr_number_of("docs/adr/README.md") is None
    assert adr_number_of("docs/adr/template.md") is None
    assert adr_number_of("docs/adr/archive/operations/0008-x.md") is None
    assert adr_number_of("docs/design/0001-x.md") is None


def test_単体_退役一覧はコメントを無視する() -> None:
    text = "# 二度と使わない\n0008\n0011\n\n  0014  \n# 0099 はコメント\n"
    assert parse_retired(text) == {8, 11, 14}
    assert 99 not in parse_retired(text)


def test_単体_ファイル名から番号を拾う() -> None:
    names = ["docs/adr/0001-a.md", "docs/adr/0046-b.md", "docs/adr/README.md"]
    assert numbers_in_names(names) == {1, 46}


def test_単体_次の番号は最大_プラス1_で欠番は埋めない() -> None:
    assert next_free({1, 2, 46, 48}) == 49
    assert next_free(set()) == 1


def test_単体_退役番号の再利用を止める(monkeypatch, tmp_path: Path) -> None:
    import adr_number_guard as guard

    _stub_repo(monkeypatch, guard, tmp_path, used={1, 8, 46, 48})
    result = handle(_write_event(tmp_path, "docs/adr/0008-reuse.md"))
    assert result is not None
    decision = result["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "0049" in decision["permissionDecisionReason"]


def test_単体_最大プラス1_だけを通す(monkeypatch, tmp_path: Path) -> None:
    import adr_number_guard as guard

    _stub_repo(monkeypatch, guard, tmp_path, used={1, 8, 46, 48})
    assert handle(_write_event(tmp_path, "docs/adr/0049-new.md")) is None


def test_単体_未使用でも欠番と飛び番は止める(monkeypatch, tmp_path: Path) -> None:
    # used={1,8,46,48} のとき 0002 (欠番) も 9999 (飛び番) も未使用だが規約違反。
    # 「未使用なら通す」にすると、CI も衝突しか見ていないのでマージまで誰も止めない
    import adr_number_guard as guard

    cases = (("docs/adr/0002-gap.md", "欠番"), ("docs/adr/9999-far.md", "飛ば"))
    for relative, expected_word in cases:
        _stub_repo(monkeypatch, guard, tmp_path, used={1, 8, 46, 48})
        result = handle(_write_event(tmp_path, relative))
        assert result is not None, f"{relative} が素通りした"
        decision = result["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert expected_word in decision["permissionDecisionReason"]
        assert "0049" in decision["permissionDecisionReason"]


def test_単体_origin_main_を引けなければ衝突なしと答えない(monkeypatch, tmp_path: Path) -> None:
    import adr_number_guard as guard

    _stub_repo(monkeypatch, guard, tmp_path, used=set(), failure="origin/main が無い")
    result = handle(_write_event(tmp_path, "docs/adr/0008-reuse.md"))
    assert result is not None
    assert "未検証" in result["systemMessage"]


def test_単体_既存_ADR_の上書きは採番の話ではない(monkeypatch, tmp_path: Path) -> None:
    import adr_number_guard as guard

    _stub_repo(monkeypatch, guard, tmp_path, used={46})
    existing = tmp_path / "docs" / "adr" / "0046-x.md"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("# 既存", encoding="utf-8")
    assert handle(_write_event(tmp_path, "docs/adr/0046-x.md")) is None


def test_単体_ADR_以外の_Write_では_git_すら叩かない(monkeypatch, tmp_path: Path) -> None:
    import adr_number_guard as guard

    def _explode(*args, **kwargs):
        raise AssertionError("ADR 以外で git を叩いてはいけない (毎回の Write の費用になる)")

    monkeypatch.setattr(guard, "_git", _explode)
    assert handle(_write_event(tmp_path, "apps/bff/src/a.ts")) is None
    assert handle({"tool_name": "Edit", "tool_input": {"file_path": "docs/adr/0099-x.md"}}) is None


def test_単体_理由に採番規約と次の番号が入る() -> None:
    reason = format_reason(8, 49, {8, 48})
    assert "0008" in reason
    assert "0049" in reason
    assert "既に使われています" in reason
    # 文言は「こうしろ」ではなく「何が違反か + 通し方」。行動指示の形にすると
    # 「ツール出力に埋め込まれた指示」として無視される (2026-08-13 に実測)
    assert "ください" not in reason
    # 未使用の番号は「使われている」と言ってはいけない (嘘の理由は次から信用されない)
    assert "既に使われています" not in format_reason(2, 49, {1, 48})


def test_単体_理由に最大番号の根拠が入る() -> None:
    # 根拠が無いと、作業ツリーに 0003 までしか見えない状況で「次は 0008」と言われた
    # エージェントが**信用せずに従わない** (2026-08-13 に実測した実挙動)
    reason = format_reason(5, 8, {1, 2, 3, 7})
    assert "0007" in reason, "最大番号の根拠 (退役番号を含む) が出ていない"
    assert RETIRED_FILE in reason


def _make_repo(root: Path) -> None:
    """origin/main ref を持つ本物の git リポジトリを組み立てる。

    `collect_used` は 3 つの出所 (origin/main の実ファイル / 作業ツリー / 退役一覧) を
    合算する。ここを stub で済ませると、**退役一覧を合算し忘れても全テストが緑になる**
    (実際にミューテーション試験で生き残った穴)。
    """
    adr = root / "docs" / "adr"
    archive = root / "docs" / "adr" / "archive"
    adr.mkdir(parents=True)
    archive.mkdir(parents=True)
    (adr / "0001-a.md").write_text("# a", encoding="utf-8")
    (adr / "0003-b.md").write_text("# b", encoding="utf-8")
    (root / RETIRED_FILE).write_text("# 二度と使わない\n0002\n0007\n", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("add", ".")
    git("commit", "-qm", "init")
    # remote を持たないので origin/main ref を直接作る (fetch はしない = hook と同条件)
    git("update-ref", "refs/remotes/origin/main", "HEAD")


def test_単体_使用済み番号は_origin_main_と作業ツリーと退役一覧の合算(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    # commit 後に作業ツリーだけに置いた ADR (並行セッションが採った番号に相当)
    (tmp_path / "docs" / "adr" / "0005-local.md").write_text("# c", encoding="utf-8")

    used, failure = collect_used(tmp_path, str(tmp_path))
    assert failure is None
    assert 1 in used and 3 in used, "origin/main の実ファイルが漏れている"
    assert 5 in used, "作業ツリーだけにある ADR が漏れている"
    assert {2, 7} <= used, "退役番号が漏れている (再利用が素通りする)"
    assert next_free(used) == 8


def test_単体_退役番号を本物のリポジトリで拒否する(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    result = handle(_write_event(tmp_path, "docs/adr/0007-retired.md"))
    assert result is not None, "退役番号 0007 の再利用が素通りした"
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_単体_origin_main_が無いリポジトリでは衝突なしと答えない(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    subprocess.run(
        ["git", "update-ref", "-d", "refs/remotes/origin/main"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    used, failure = collect_used(tmp_path, str(tmp_path))
    assert failure is not None, "origin/main を引けないのに成功として返している"
    assert used == set()


def _write_event(root: Path, relative: str) -> dict:
    return {
        "tool_name": "Write",
        "cwd": str(root),
        "tool_input": {"file_path": str(root / relative), "content": "# x"},
    }


def _stub_repo(monkeypatch, guard, root: Path, used: set[int], failure: str | None = None) -> None:
    """git を使わずに「使用済み番号の集合」だけ差し替える。"""
    monkeypatch.setattr(guard, "_git", lambda args, cwd: (str(root), None))
    monkeypatch.setattr(guard, "collect_used", lambda repo_root, cwd: (used, failure))
