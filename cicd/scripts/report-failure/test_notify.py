"""[単体] 通報ステップの実体 notify.py を gh スタブ越しに動かす (Issue #507)。

無いと何が静かに通るか:
    **「無言で死ぬ」が復活しても、run が赤いだけで理由がどこにも残らない。**
    2026-08-17 の deploy 3 run がまさにそれで、成功したデプロイが 3 回続けて赤に
    なったのに、ログには `Process completed with exit code 1` の 1 行しか無かった
    (stdout / stderr が 0 バイト)。原因が特定できたのはコードを読んで再現させた
    あとで、ログからは何も分からなかった。

    ここで固定するのは 3 つ:
      1. gh がどこで失敗しても **stdout に痕跡が残る** (無言の終了が無い)
      2. 失敗しても **job が成功側なら step は緑** (デプロイの緑を汚さない)
      3. 一覧が引けない失敗側では **重複覚悟で Issue を立てる** (通報を落とさない)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
NOTIFY = HERE / "notify.py"

_STUB = '''#!{python}
"""gh スタブ。呼び出しを記録し、GH_STUB_FAIL で指定されたサブコマンドだけ落とす。"""
import json, os, sys

argv = sys.argv[1:]
key = "-".join(argv[:2])
log = os.environ["GH_STUB_LOG"]
calls = []
if os.path.exists(log):
    calls = json.load(open(log))
calls.append(argv)
json.dump(calls, open(log, "w"))

seen = sum(1 for c in calls if "-".join(c[:2]) == key)
fail = set(filter(None, os.environ.get("GH_STUB_FAIL", "").split(",")))
fail_times = int(os.environ.get("GH_STUB_FAIL_TIMES", "0") or 0)

if key in fail and (fail_times == 0 or seen <= fail_times):
    sys.stderr.write("gh-stub: HTTP 502 Bad Gateway for %s\\n" % key)
    sys.exit(1)
if key == "label-create":
    sys.stderr.write("HTTP 422: Validation Failed\\nName already exists\\n")
    sys.exit(1)
if key == "issue-list":
    sys.stdout.write(os.environ.get("GH_STUB_ISSUES", "[]"))
    sys.exit(0)
sys.stdout.write("https://github.com/yomote/mind-inbox/issues/999\\n")
sys.exit(0)
'''

TITLE_DEPLOY = "[ci-failure] deploy が落ちている"


@pytest.fixture()
def gh_stub(tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "gh"
    stub.write_text(_STUB.format(python=sys.executable), encoding="utf-8")
    stub.chmod(0o755)
    return bindir


def run_notify(
    gh_stub: Path,
    tmp_path: Path,
    *,
    job_status: str = "success",
    work_ran: str = "true",
    cancelled_is_failure: str = "true",
    fail: str = "",
    fail_times: int = 0,
    issues: list[dict] | None = None,
    raw_issues: str | None = None,
) -> tuple[subprocess.CompletedProcess, list[list[str]], str]:
    log = tmp_path / "calls.json"
    summary = tmp_path / "summary.md"
    env = {
        **os.environ,
        "PATH": f"{gh_stub}{os.pathsep}{os.environ['PATH']}",
        "GH_STUB_LOG": str(log),
        "GH_STUB_FAIL": fail,
        "GH_STUB_FAIL_TIMES": str(fail_times),
        "GH_STUB_ISSUES": (
            raw_issues if raw_issues is not None else json.dumps(issues or [])
        ),
        "GITHUB_STEP_SUMMARY": str(summary),
        "REPORT_FAILURE_BACKOFF_SECONDS": "0",
        "JOB_STATUS": job_status,
        "WORK_RAN": work_ran,
        "CANCELLED_IS_FAILURE": cancelled_is_failure,
        "WHAT": "**dev への自動デプロイが失敗しました。**",
        "WF": "deploy",
        "RUN_URL": "https://github.com/yomote/mind-inbox/actions/runs/1",
        "REPO": "yomote/mind-inbox",
        "SHA": "5f9f672",
        "REF": "main",
        "GH_TOKEN": "dummy",
    }
    proc = subprocess.run(
        [sys.executable, str(NOTIFY)], capture_output=True, text=True, env=env, timeout=120
    )
    calls = json.loads(log.read_text()) if log.exists() else []
    return proc, calls, summary.read_text(encoding="utf-8") if summary.exists() else ""


def _keys(calls: list[list[str]]) -> list[str]:
    return ["-".join(c[:2]) for c in calls]


def test_単体_成功デプロイで一覧が引けなくても緑のまま理由が残る(gh_stub, tmp_path) -> None:
    """#507 そのもの。旧実装はこの入力で **0 バイト出力の exit 1** だった。"""
    proc, calls, summary = run_notify(gh_stub, tmp_path, fail="issue-list")

    assert proc.returncode == 0, proc.stdout + proc.stderr  # デプロイの緑を汚さない
    assert proc.stdout.strip(), "無言で終わった (これが #507 の再発)"
    assert "::error::" in proc.stdout  # 通報の失敗は必ず見える
    assert "gh issue list" in proc.stdout  # どのコマンドが落ちたか
    assert "502 Bad Gateway" in proc.stdout  # gh の stderr を捨てていない
    assert "赤にしない" in proc.stdout
    assert "通報ステップ" in summary  # ログを開かなくても気づける
    # 一覧が引けない成功側では Issue を触らない (直っているのに障害 Issue を湧かせない)
    assert "issue-create" not in _keys(calls)


def test_単体_失敗デプロイで一覧が引けなければ重複覚悟で立てる(gh_stub, tmp_path) -> None:
    """通報を落とすより重複を選ぶ。無通報は「沈黙 = 異常なし」の前提ごと壊す。"""
    proc, calls, _ = run_notify(gh_stub, tmp_path, job_status="failure", fail="issue-list")

    assert "issue-create" in _keys(calls)
    assert proc.returncode == 1  # job が赤い回に通報を落としたことは赤で残す
    assert "::error::" in proc.stdout
    # 「一覧が引けなかった」を「open Issue は無い」に丸めると、この一言が消えて
    # 重複 Issue が理由不明で湧く (旧実装はそもそもここに到達せず無言で死んでいた)
    assert "重複する可能性がある状態で新規に立てる" in proc.stdout


def test_単体_一覧が引けた失敗回は重複の断り書きを出さない(gh_stub, tmp_path) -> None:
    """上のテストの対照。断り書きが常時出ると意味を持たなくなる。"""
    proc, calls, _ = run_notify(gh_stub, tmp_path, job_status="failure")
    assert "issue-create" in _keys(calls)
    assert "重複する可能性がある状態で新規に立てる" not in proc.stdout


@pytest.mark.parametrize("raw", ["これは JSON ではない", '{"number": 1}'])
def test_単体_一覧の応答が壊れていたら_Issue_無しに丸めない(gh_stub, tmp_path, raw: str) -> None:
    """gh が 0 で返しても中身が壊れていることはある。JSON エラーを「open Issue は無い」に
    丸めると、落ちた回に **既存 Issue への追記のかわりに新規 Issue が湧く**か、
    直った回に閉じ損ねる。どちらも run は緑のまま通る。"""
    proc, _calls, summary = run_notify(gh_stub, tmp_path, raw_issues=raw)
    assert proc.returncode == 0  # 成功デプロイの緑は汚さない
    assert "::error::" in proc.stdout
    assert "一覧" in proc.stdout and summary.strip()


def test_単体_一覧の一過性エラーは再試行して回復し試行はログに残る(gh_stub, tmp_path) -> None:
    """2026-08-21 の deploy は同じコードで緑に戻った = gh の非ゼロは一過性だった。
    再試行で吸収するが、**吸収したこと自体は黙らない**。"""
    proc, calls, _ = run_notify(
        gh_stub,
        tmp_path,
        fail="issue-list",
        fail_times=2,
        issues=[{"number": 42, "title": TITLE_DEPLOY}],
    )

    assert proc.returncode == 0
    assert "::error::" not in proc.stdout  # 最終的に取れたので失敗扱いにしない
    assert "失敗 (1/3)" in proc.stdout and "3 回目で成功した" in proc.stdout
    assert _keys(calls).count("issue-list") == 3
    assert "issue-close" in _keys(calls)  # 取れたので復旧として閉じる


def test_単体_復旧した回は既存_Issue_にコメントして閉じる(gh_stub, tmp_path) -> None:
    proc, calls, _ = run_notify(
        gh_stub, tmp_path, issues=[{"number": 42, "title": TITLE_DEPLOY}]
    )
    assert proc.returncode == 0
    assert _keys(calls) == ["label-create", "issue-list", "issue-comment", "issue-close"]
    comment = next(c for c in calls if c[:2] == ["issue", "comment"])
    assert comment[2] == "42"
    assert "✅ 復旧しました。" in comment[comment.index("--body") + 1]


def test_単体_閉じられなくても成功デプロイは緑のまま理由が残る(gh_stub, tmp_path) -> None:
    proc, _calls, summary = run_notify(
        gh_stub,
        tmp_path,
        fail="issue-close",
        issues=[{"number": 42, "title": TITLE_DEPLOY}],
    )
    assert proc.returncode == 0
    assert "::error::" in proc.stdout and "#42 を閉じられなかった" in proc.stdout
    assert summary.strip()


def test_単体_落ちた回は既存_Issue_に再発を追記する(gh_stub, tmp_path) -> None:
    proc, calls, _ = run_notify(
        gh_stub,
        tmp_path,
        job_status="failure",
        issues=[{"number": 42, "title": TITLE_DEPLOY}],
    )
    assert proc.returncode == 0  # 通報自体は成功しているので step は赤にしない
    assert "issue-create" not in _keys(calls)  # 冪等: 毎 run 立てない
    comment = next(c for c in calls if c[:2] == ["issue", "comment"])
    assert "🔴 まだ落ちています" in comment[comment.index("--body") + 1]


def test_単体_ラベル作成の本当のエラーは_stderr_ごと見える(gh_stub, tmp_path) -> None:
    """既存衝突 (正常) の非ゼロは静かに流すが、それ以外の非ゼロまで一緒に流すと
    **ラベルの付かない Issue** が立ち、状況ページの ci-failure 集計から静かに漏れる。"""
    proc, _calls, _ = run_notify(gh_stub, tmp_path, fail="label-create")
    assert "ラベル ci-failure を用意できなかった" in proc.stdout
    assert "502 Bad Gateway" in proc.stdout  # gh の stderr を捨てていない


def test_単体_ラベルが既にあるだけの回は失敗として出さない(gh_stub, tmp_path) -> None:
    """毎 run 出る「失敗」はノイズで、本物の失敗を埋める (対照テスト)。"""
    proc, _calls, _ = run_notify(gh_stub, tmp_path)
    assert "ラベル ci-failure は既にある (正常)" in proc.stdout
    assert "ラベル作成: 失敗" not in proc.stdout
    assert "::error::" not in proc.stdout


def test_単体_job_status_が空でも黙らない(gh_stub, tmp_path) -> None:
    """job.status が取れない = 復旧も障害も判定できない。noop に丸めない。"""
    proc, _calls, _ = run_notify(gh_stub, tmp_path, job_status="")
    assert "JOB_STATUS が空" in proc.stdout
    assert proc.returncode == 1  # 未知の状態は報告側に倒す (effective_status)


@pytest.mark.parametrize(
    "fail",
    ["label-create", "issue-list", "issue-comment", "issue-close", "issue-create"],
)
def test_単体_どこで失敗しても出力が空にならない(gh_stub, tmp_path, fail: str) -> None:
    """#507 の再発検知。**どの 1 手が落ちても** ログが 0 バイトにならないことを固定する。"""
    for job_status in ("success", "failure"):
        proc, _calls, _ = run_notify(
            gh_stub,
            tmp_path / f"{fail}-{job_status}",
            job_status=job_status,
            fail=fail,
            issues=[{"number": 42, "title": TITLE_DEPLOY}],
        )
        assert proc.stdout.strip(), f"{fail}/{job_status} で無言になった"
        assert "通報ステップ終了" in proc.stdout


def test_単体_gh_が存在しなくても無言で死なない(gh_stub, tmp_path) -> None:
    """PATH から gh が消えるのは「壊れてから気づく」形の典型。"""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    log = tmp_path / "calls.json"
    env = {
        **os.environ,
        "PATH": str(empty),
        "GH_STUB_LOG": str(log),
        "REPORT_FAILURE_BACKOFF_SECONDS": "0",
        "JOB_STATUS": "success",
        "WORK_RAN": "true",
        "CANCELLED_IS_FAILURE": "true",
        "WHAT": "x",
        "WF": "deploy",
        "RUN_URL": "https://example/run/1",
        "REPO": "yomote/mind-inbox",
        "SHA": "abc",
        "REF": "main",
    }
    proc = subprocess.run(
        [sys.executable, str(NOTIFY)], capture_output=True, text=True, env=env, timeout=120
    )
    assert proc.returncode == 0  # デプロイは成功しているので緑のまま
    assert "FileNotFoundError" in proc.stdout or "No such file" in proc.stdout
    assert "::error::" in proc.stdout


def test_単体_想定外の例外でも無言にならず緑を汚さない(monkeypatch, tmp_path, capsys) -> None:
    """無いと何が静かに通るか: 通報側のバグ (typo / API 変更) で例外が飛ぶと、
    旧実装と同じ「デプロイは成功したのに run が赤」に戻る。しかも traceback を
    出さずに握れば #507 の無言死そのものが復活する。"""
    sys.path.insert(0, str(HERE))
    import notify

    monkeypatch.setattr(notify, "ensure_label", _boom)
    monkeypatch.setenv("JOB_STATUS", "success")
    monkeypatch.setenv("WORK_RAN", "true")
    monkeypatch.setenv("CANCELLED_IS_FAILURE", "true")
    monkeypatch.setenv("WF", "deploy")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "s.md"))

    assert notify.run() == 0  # デプロイの緑は汚さない
    out = capsys.readouterr()
    assert "::error::" in out.out and "想定外の例外" in out.out
    assert "RuntimeError" in out.err  # traceback を握り潰さない

    # job が落ちた回は赤のまま (通報できていない = 障害が Issue に出ない)
    monkeypatch.setenv("JOB_STATUS", "failure")
    assert notify.run() == 1


def _boom(*_args, **_kwargs):
    raise RuntimeError("想定外")
