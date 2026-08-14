"""[単体] sync.py の配線 — 判定 (純関数) の**消費側**が壊れていないか。

無いと何が静かに通るか:
    - **拒んだのに run が緑で終わる** — `decide_apply` が「ダイジェストが違う」と
      言っても `exit_code = 1` に落ちなければ、workflow は `steps.sync.outcome` で
      run の色を決め、sync step は `continue-on-error: true` なので
      (`.github/workflows/github-settings.yml`)、**適用を拒否した run と正常な
      check が区別できなくなる**
    - **拒んだのに適用してしまう** — 判定の消費が `if decision.proceed` から
      外れると、ダイジェスト不一致でも書き込み API が飛ぶ
    - **不正な `--repository` が API に届く** — 純関数側のテストだけでは、
      `main()` が検証を呼んでいるかを固定できない (検証ブロックを消しても
      settings_diff のテストは緑のまま通る)

なぜ `gh` をスタブにして `main()` を丸ごと回すか (docs/testing/strategy.md §1.4):
    判定を純関数へ切り出しても、**その判定を誰も見ていない**なら安全弁は無い。
    ここで見たいのは判定そのものではなく「判定 → 終了コード / API 呼び出し」の
    配線なので、境界 (`gh`) を差し替えて実際に `main()` を実行する。
    **assert するのは「何を出力したか」ではなく「API を叩いたか / exit は何か」**。

性質の出どころ:
    - Issue #344 の「満たすべき性質」2 (差分を見せてから適用する) / 3 (check は
      読み取りのみ) と、Issue #372 の major 1 (適用先の固定) / minor 3 (安全弁)
    - `.github/workflows/github-settings.yml` の `continue-on-error: true` +
      `steps.sync.outcome` による色の決め方 (= exit code が run の色を決める)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sync  # noqa: E402
from settings_diff import DeclarationError  # noqa: E402

# `gh` の代わりに PATH へ置く実行ファイル。**全呼び出しを記録する** —
# 「拒否のメッセージが出たか」ではなく「API を叩かなかったか」で判定するため。
GH_STUB = '''#!/usr/bin/env python3
import json, os, sys

argv = sys.argv[1:]
method = argv[argv.index("-X") + 1] if "-X" in argv else "GET"
endpoint = [a for a in argv[1:] if not a.startswith("-") and a != "-"][-1]
body = None
if "--input" in argv:
    body = sys.stdin.read()

with open(os.environ["GH_STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"method": method, "endpoint": endpoint, "body": body}) + "\\n")

STATE = os.environ["GH_STUB_STATE"]
state = json.load(open(STATE, encoding="utf-8"))

BOOLS = ("enforce_admins", "required_linear_history", "required_conversation_resolution",
         "block_creations", "lock_branch", "allow_force_pushes", "allow_deletions",
         "allow_fork_syncing")


def to_get_shape(payload):
    """PUT の body を GitHub が GET で返す形にする (GET と PUT の非対称のモデル)。"""
    raw = {}
    rsc = payload.get("required_status_checks")
    if rsc is not None:
        raw["required_status_checks"] = {
            "strict": rsc["strict"],
            "checks": [{"context": c, "app_id": None} for c in rsc["contexts"]],
        }
    rpr = payload.get("required_pull_request_reviews")
    if rpr is not None:
        raw["required_pull_request_reviews"] = dict(rpr)
    for name in BOOLS:
        raw[name] = {"enabled": bool(payload.get(name))}
    if payload.get("restrictions"):
        raw["restrictions"] = payload["restrictions"]
    return raw


def ok(doc):
    print(json.dumps(doc))
    sys.exit(0)


def not_found():
    sys.stderr.write("gh: Not Found (HTTP 404)\\n")
    sys.exit(1)


branch = None
if "/branches/" in endpoint:
    branch = endpoint.split("/branches/", 1)[1].split("/")[0]

if method != "GET":
    # GH_STUB_FREEZE=1 は「API は 200 を返すが実際には反映されない」現実のモデル。
    # 「設定した」と「そうなっている」を区別できるかを見るために要る (ADR 0018)
    if branch is not None and os.environ.get("GH_STUB_FREEZE") != "1":
        state["branches"][branch] = (
            None if method == "DELETE" else to_get_shape(json.loads(body))
        )
        json.dump(state, open(STATE, "w", encoding="utf-8"))
    ok({"ok": True})

if endpoint == "repos/owner/repo":
    ok({"full_name": "owner/repo", "security_and_analysis": state["security_and_analysis"]})
if endpoint.endswith("/vulnerability-alerts"):
    sys.exit(0)
if endpoint.endswith("/automated-security-fixes"):
    ok({"enabled": True, "paused": False})
if endpoint.endswith("/code-scanning/default-setup"):
    ok({"state": "configured"})
if branch is not None:
    doc = state["branches"].get(branch, "missing")
    if doc == "missing":
        not_found()  # ブランチが無い
    if doc is None:
        not_found()  # ブランチはあるが保護されていない (endpoint が protection のとき)
    if endpoint.endswith("/protection"):
        ok(doc)
    ok({"name": branch})  # ブランチ自体の GET
not_found()
'''

# 宣言は**このテスト専用のものを書く** (PO の cicd/github/settings.yml を人が編集
# しても、この配線のテストが道連れで落ちないように)。
DECLARATION_YAML = """
version: 1
security:
  secret_scanning: enabled
  secret_scanning_push_protection: enabled
  dependabot_alerts: enabled
  dependabot_security_updates: enabled
  code_scanning_default_setup: configured
branch_protection:
  main:
    protected: true
    required_status_checks:
      strict: false
      contexts:
        - "test"
        - "review-gate"
    required_pull_request_reviews:
      required_approving_review_count: 0
      dismiss_stale_reviews: false
      require_code_owner_reviews: false
      require_last_push_approval: false
    required_conversation_resolution: false
    required_linear_history: false
    enforce_admins: false
    allow_force_pushes: false
    allow_deletions: false
    block_creations: false
    lock_branch: false
    allow_fork_syncing: false
    restrictions: none
"""

SECURITY_ALL_ON = {
    "secret_scanning": {"status": "enabled"},
    "secret_scanning_push_protection": {"status": "enabled"},
}

# 宣言より required check が 1 つ少ない = 強める差分 1 件
PROTECTION_DRIFT = {
    "required_status_checks": {"strict": False, "checks": [{"context": "test"}]},
    "required_pull_request_reviews": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews": False,
        "require_code_owner_reviews": False,
        "require_last_push_approval": False,
    },
    **{
        name: {"enabled": False}
        for name in (
            "enforce_admins",
            "required_linear_history",
            "required_conversation_resolution",
            "block_creations",
            "lock_branch",
            "allow_force_pushes",
            "allow_deletions",
            "allow_fork_syncing",
        )
    },
}


def protection_weaken() -> dict:
    """宣言どおりにすると**弱くなる**現実 (push 制限があり、check も揃っている)。"""
    doc = json.loads(json.dumps(PROTECTION_DRIFT))
    doc["required_status_checks"]["checks"] = [
        {"context": "test"},
        {"context": "review-gate"},
    ]
    doc["restrictions"] = {"users": [], "teams": [{"slug": "x"}], "apps": []}
    return doc


class Gh:
    """スタブの記録を読む。**書き込み = GET 以外**の呼び出し。"""

    def __init__(self, log: Path) -> None:
        self.log = log

    @property
    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [
            json.loads(line)
            for line in self.log.read_text(encoding="utf-8").splitlines()
        ]

    @property
    def writes(self) -> list[dict]:
        return [c for c in self.calls if c["method"] != "GET"]


@pytest.fixture
def gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Gh:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(GH_STUB, encoding="utf-8")
    stub.chmod(0o755)
    log = tmp_path / "gh-calls.jsonl"
    state = tmp_path / "gh-state.json"
    state.write_text(
        json.dumps(
            {
                "security_and_analysis": SECURITY_ALL_ON,
                "branches": {"main": PROTECTION_DRIFT},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_STUB_LOG", str(log))
    monkeypatch.setenv("GH_STUB_STATE", str(state))
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    return Gh(log)


@pytest.fixture
def declaration(tmp_path: Path) -> Path:
    pytest.importorskip("yaml", reason="PyYAML が無いと宣言を読めない")
    path = tmp_path / "settings.yml"
    path.write_text(DECLARATION_YAML, encoding="utf-8")
    return path


def set_reality(protection: dict | None) -> None:
    state_path = Path(os.environ["GH_STUB_STATE"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["branches"]["main"] = protection
    state_path.write_text(json.dumps(state), encoding="utf-8")


def run(declaration: Path, *args: str) -> tuple[int, dict | None]:
    """sync.main() を実行して (exit code, stdout の JSON) を返す。"""
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = sync.main(["--declaration", str(declaration), *args])
    out = buf.getvalue().strip()
    return code, (json.loads(out) if out else None)


# --- check は読み取りだけ (正の対照) ---------------------------------------


def test_単体_checkは読み取りだけで差分を出す(gh: Gh, declaration: Path):
    """正の対照。ここが緑でなければ、以下の「拒む」テストは**何でも拒む**だけの
    テストになり、安全弁が効いている証拠にならない。
    """
    code, summary = run(declaration, "--mode", "check", "--repository", "owner/repo")
    assert code == 0
    assert summary["drift"] == 1  # required check が 1 つ足りない
    assert summary["unavailable"] == 0
    assert gh.calls and gh.writes == []  # check は GET だけ


# --- apply の安全弁 (配線ごと) ----------------------------------------------


def test_単体_ダイジェスト不一致のapplyは書き込まずrunを落とす(
    gh: Gh, declaration: Path
):
    """`exit_code = 1` を落とすと、workflow は `steps.sync.outcome` で色を決め
    (`continue-on-error: true`)、**拒否した run が緑で終わる**。
    """
    for wrong in ("deadbeef1234", "", "0" * 12):
        code, summary = run(
            declaration,
            "--mode",
            "apply",
            "--repository",
            "owner/repo",
            "--expect-digest",
            wrong,
        )
        assert code == 1, f"拒んだのに exit 0 で終わっている (digest={wrong!r})"
        assert gh.writes == [], "何も適用していないはずが書き込みが飛んでいる"
        assert summary["applied"] == []


def test_単体_許可のない弱化は書き込まずrunを落とす(gh: Gh, declaration: Path):
    """保護を外す操作が確認なしに実行される経路を塞ぐ。"""
    set_reality(protection_weaken())
    _code, check = run(declaration, "--mode", "check", "--repository", "owner/repo")
    assert check["weakening"] >= 1

    code, summary = run(
        declaration,
        "--mode",
        "apply",
        "--repository",
        "owner/repo",
        "--expect-digest",
        check["digest"],
    )
    assert code == 1
    assert gh.writes == []
    assert summary["applied"] == []


def test_単体_正しいダイジェストのapplyは適用して差分が消える(
    gh: Gh, declaration: Path
):
    """負の対照だけでは「常に拒む」実装でも緑になる。**門は開く**ことも見る。"""
    _code, check = run(declaration, "--mode", "check", "--repository", "owner/repo")
    code, summary = run(
        declaration,
        "--mode",
        "apply",
        "--repository",
        "owner/repo",
        "--expect-digest",
        check["digest"],
    )
    assert code == 0, "強めるだけの計画が適用できていない"
    assert summary["applied"] == ["01-branch-protection-main"]
    assert summary["drift"] == 0  # 適用後に読み直して確かめている (ADR 0018)
    assert [w["endpoint"] for w in gh.writes] == [
        "repos/owner/repo/branches/main/protection"
    ]

    # 冪等: もう一度 apply しても計画が無いので何も起きない
    before = len(gh.writes)
    code, summary = run(
        declaration,
        "--mode",
        "apply",
        "--repository",
        "owner/repo",
        "--expect-digest",
        summary["digest"],
    )
    assert code == 0
    assert len(gh.writes) == before


def test_単体_APIが成功と言っても反映されていなければrunを落とす(
    gh: Gh, declaration: Path, monkeypatch: pytest.MonkeyPatch
):
    """API が 200 を返しても、読み直して直っていなければ緑にしない (ADR 0018)。

    ここを緑にすると「設定した」という嘘が定着する — 適用に失敗した run が
    成功した run と区別できなくなる。
    """
    _code, check = run(declaration, "--mode", "check", "--repository", "owner/repo")
    # PUT は 200 を返すが現実は変わらない (権限の都合で黙って無視される等)
    monkeypatch.setenv("GH_STUB_FREEZE", "1")

    code, summary = run(
        declaration,
        "--mode",
        "apply",
        "--repository",
        "owner/repo",
        "--expect-digest",
        check["digest"],
    )
    assert gh.writes, "PUT は実際に飛んでいる (前提)"
    assert summary["applied"] == ["01-branch-protection-main"]  # API は成功と言った
    assert summary["drift"] == 1, "読み直しをしていない (適用前の差分のまま報告)"
    assert code == 1, "API の成功をそのまま run の成功にしている"


# --- 適用先の検証 (main() が呼んでいるか) -----------------------------------


def test_単体_不正な適用先はAPIに一度も届かない(gh: Gh, declaration: Path):
    """`../victim` はスラッシュ 1 個なので「形」の検査だけでは抜ける。

    純関数側のテストだけでは、`main()` が検証を**呼んでいる**ことを固定できない。
    """
    for bad in (
        "../victim",
        "owner/../victim",
        "owner",
        "owner/repo/extra",
        "own er/repo",
    ):
        code, summary = run(declaration, "--mode", "check", "--repository", bad)
        assert code == 1, f"不正な適用先が通っている: {bad!r}"
        assert summary is None
        assert gh.calls == [], f"API に届いている: {bad!r} / {gh.calls}"


def test_単体_読み取り関数も呼び出し元の検証を前提にしない(gh: Gh):
    """`read_*` は「main() が検証したはず」を前提にしない。

    無いと何が静かに通るか: 呼び出し元が 1 箇所でも増えた瞬間 (別スクリプトからの
    import・将来の分岐) に、検証されていない値がそのまま API のパスになる。
    """
    for bad_repo in ("../victim", "owner", "owner/../victim"):
        with pytest.raises(DeclarationError):
            sync.read_security(bad_repo)
        with pytest.raises(DeclarationError):
            sync.read_branch_protection(bad_repo, "main")
    with pytest.raises(DeclarationError):
        sync.read_branch_protection("owner/repo", "../../../victim/repo/branches/main")
    assert gh.calls == [], "検証前に API を叩いている"


def test_単体_適用先は環境変数から取るときも検証される(
    gh: Gh, declaration: Path, monkeypatch: pytest.MonkeyPatch
):
    """`$GITHUB_REPOSITORY` は workflow が渡す。`--repository` だけ守っても穴が残る。"""
    monkeypatch.setenv("GITHUB_REPOSITORY", "../victim/x")
    code, _summary = run(declaration, "--mode", "check")
    assert code == 1
    assert gh.calls == []
