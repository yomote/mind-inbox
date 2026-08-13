"""[単体] 握り潰し検出 (Issue #392 案 A)。

無いと何が静かに通るか:
    - **既存コードの握り潰しまで咎めてしまい**、無関係な 1 行を直しただけで
      ブロックが出る → hook が邪魔者になって外される (誤検知は guard の死)
    - 逆に、新規追加された `2>/dev/null` / `|| true` / 空 catch を見逃して、
      失敗が黙って握り潰されるコードが入る (このリポジトリで最も繰り返している事故)
    - 「コメントを書けば通る」という逃げ道が壊れ、規約どおり理由を書いた
      コードまでブロックされる
    - `structuredPatch` を解釈できなかったときに **何も出さずに素通しし**、
      「検査して問題なし」と「検査していない」が区別できなくなる
"""

from pathlib import Path

from swallow_guard import (
    added_line_numbers,
    find_swallows,
    handle,
    has_nearby_comment,
    is_checked_path,
)


def _patch(new_start: int, lines: list[str]) -> list[dict]:
    return [{"oldStart": new_start, "oldLines": 0, "newStart": new_start, "lines": lines}]


# --- 追加行の特定 ---------------------------------------------------------


def test_単体_structuredPatch_の追加行だけを新規行として拾う() -> None:
    patch = _patch(10, [" ctx", "-old", "+new1", "+new2", " ctx2"])
    # 文脈行 10、削除行は進めない、追加行は 11 と 12
    assert added_line_numbers({"structuredPatch": patch}, {}) == {11, 12}


def test_単体_Write_の新規作成は全行が新規行() -> None:
    response = {"type": "create", "content": "a\nb\nc", "structuredPatch": []}
    assert added_line_numbers(response, {}) == {1, 2, 3}


def test_単体_差分を解釈できないときは空集合ではなく_None() -> None:
    # 空集合 (追加行なし) と None (調べられなかった) を混同すると、
    # 解釈失敗が「問題なし」に化ける
    assert added_line_numbers({"unexpected": 1}, {}) is None
    assert added_line_numbers({"structuredPatch": []}, {}) == set()


# --- 対象ファイルの絞り込み -----------------------------------------------


def test_単体_コード以外は検査対象外() -> None:
    assert is_checked_path("cicd/deploy.sh")
    assert is_checked_path(".github/workflows/x.yml")
    assert is_checked_path("apps/bff/src/a.ts")
    assert not is_checked_path("docs/adr/0001-x.md")  # 規約の解説が自分に引っかかるのを防ぐ
    assert not is_checked_path("package.json")


# --- 検出そのもの ---------------------------------------------------------


def test_単体_新規追加された握り潰しを検出する() -> None:
    lines = ["set -e", "curl https://example.com 2>/dev/null", "npm test || true"]
    findings = find_swallows(lines, {2, 3})
    assert [f.line_no for f in findings] == [2, 3]
    assert "2>/dev/null" in findings[0].label
    assert "|| true" in findings[1].label


def test_単体_既存行の握り潰しは咎めない() -> None:
    lines = ["npm test || true", "echo done"]
    # 2 行目だけを追加した編集。1 行目は既存なので対象外
    assert find_swallows(lines, {2}) == []


def test_単体_近傍にコメントがあれば通る() -> None:
    same_line = ["npm test || true  # 失敗しても続ける: このテストは未実装 (#2)"]
    assert find_swallows(same_line, {1}) == []

    prev_line = ["# 見えなくなるもの: npm の非ゼロ終了", "npm test || true"]
    assert find_swallows(prev_line, {2}) == []

    next_line = ["npm test || true", "# 見えなくなるもの: npm の非ゼロ終了"]
    assert find_swallows(next_line, {1}) == []


def test_単体_URL_のスラッシュをコメントと誤認しない() -> None:
    # `https://` の `//` を行内コメントとみなすと、握り潰しが全部素通りする
    lines = ["curl https://example.com/a 2>/dev/null"]
    assert len(find_swallows(lines, {1})) == 1


def test_単体_空行を挟んでもコメントを近傍とみなす() -> None:
    lines = ["# 理由", "", "npm test || true"]
    assert find_swallows(lines, {3}) == []


def test_単体_複数行にまたがる空の_catch_を検出する() -> None:
    lines = ["try {", "  run();", "} catch (e) {", "}"]
    findings = find_swallows(lines, {3, 4})
    assert len(findings) == 1
    assert "catch" in findings[0].label


def test_単体_中身のある_catch_は咎めない() -> None:
    lines = ["} catch (e) {", "  log(e);", "}"]
    assert find_swallows(lines, {1, 2, 3}) == []


def test_単体_空の_except_を検出する() -> None:
    lines = ["try:", "    run()", "except OSError:", "    pass"]
    assert len(find_swallows(lines, {3, 4})) == 1


def test_単体_continue_on_error_を検出する() -> None:
    assert len(find_swallows(["    continue-on-error: true"], {1})) == 1


def test_単体_コメント行そのものは検出対象にしない() -> None:
    # 規約の解説や、免除のために書いたコメントが自分に引っかからないこと
    assert find_swallows(["# `|| true` を足すときは理由を書く"], {1}) == []


def test_単体_近傍判定は範囲外の行番号で落ちない() -> None:
    assert has_nearby_comment(["a"], 99) is False
    assert has_nearby_comment(["a"], 0) is False


# --- hook としての振る舞い -------------------------------------------------


def test_単体_違反があるとブロックし理由に行番号が入る(tmp_path: Path) -> None:
    target = tmp_path / "run.sh"
    target.write_text("set -e\nnpm test || true\n", encoding="utf-8")
    event = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
        "tool_response": {"structuredPatch": _patch(2, ["+npm test || true"])},
    }
    result = handle(event)
    assert result is not None
    assert result["decision"] == "block"
    assert "L2" in result["reason"]


def test_単体_違反が無ければ何も返さない(tmp_path: Path) -> None:
    target = tmp_path / "run.sh"
    target.write_text("set -e\nnpm test\n", encoding="utf-8")
    event = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
        "tool_response": {"structuredPatch": _patch(2, ["+npm test"])},
    }
    assert handle(event) is None


def test_単体_差分を読めなかったときは黙って素通ししない(tmp_path: Path) -> None:
    # ここが黙ると「検査して問題なし」と「検査していない」が区別できなくなる
    target = tmp_path / "run.sh"
    target.write_text("set -e\n", encoding="utf-8")
    event = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
        "tool_response": {"何か想定外": True},
    }
    result = handle(event)
    assert result is not None
    assert "未検証" in result["systemMessage"]


def test_単体_ファイルを読めなかったときも黙って素通ししない(tmp_path: Path) -> None:
    event = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(tmp_path / "存在しない.sh")},
        "tool_response": {"structuredPatch": _patch(1, ["+npm test || true"])},
    }
    result = handle(event)
    assert result is not None
    assert "未検証" in result["systemMessage"]


def test_単体_対象外ツールと対象外パスは即_None() -> None:
    assert handle({"tool_name": "Bash", "tool_input": {"command": "x || true"}}) is None
    assert (
        handle(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "/x/README.md"},
                "tool_response": {"structuredPatch": _patch(1, ["+a || true"])},
            }
        )
        is None
    )


def test_単体_subagent_の編集も同じように咎める(tmp_path: Path) -> None:
    # 規約は親にも subagent にも等しく効く。agent_id の有無で免除しないこと
    target = tmp_path / "run.sh"
    target.write_text("npm test || true\n", encoding="utf-8")
    event = {
        "agent_id": "a1",
        "agent_type": "general-purpose",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
        "tool_response": {"structuredPatch": _patch(1, ["+npm test || true"])},
    }
    assert handle(event)["decision"] == "block"
