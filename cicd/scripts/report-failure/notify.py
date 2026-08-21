#!/usr/bin/env python3
"""落ちた workflow が自分で Issue を立て、直ったら閉じる実体 (ADR 0035 D2 / Issue #507)。

`.github/actions/report-failure` の composite step から呼ばれる唯一の入口。
判定は notify_rules.py / decide.py (純関数・pytest 済み) に置き、ここは
**gh の呼び出しと「結果を必ず見える形で出す」ことだけ**を担当する。

このファイルが守っている不変条件:
    1. **無言で終わらない。** 何をしたか / 何に失敗したかを必ず 1 行以上 stdout に出す。
       想定外の例外も握らず ::error:: に翻訳して出す (旧実装は stderr を捨てていたため
       原因コマンドがログに残らなかった — #507)。
    2. **exit code は step_verdict() だけが決める。** シェルの errexit や例外の巻き添えで
       「デプロイは成功したのに run が赤」にならないようにする。
    3. **gh の標準エラーは捨てない。** 捨てるとこの Issue の再発を検知できない。

環境変数 (composite action が渡す):
    GH_TOKEN / JOB_STATUS / WORK_RAN / CANCELLED_IS_FAILURE / WHAT / WF /
    RUN_URL / REPO / SHA / REF
    REPORT_FAILURE_BACKOFF_SECONDS — 再試行の待ち (テストで 0 にするためだけの逃げ道)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import notify_rules as rules  # noqa: E402

LABEL = "ci-failure"
LABEL_COLOR = "b60205"
LABEL_DESCRIPTION = "落ちた workflow 自身が立てた Issue (ADR 0035 D2)"

# 一過性の API 障害 (5xx / rate limit / ネットワーク) で通報を落とさないための再試行。
# #507 の 3 run は同じコードで 2026-08-21 に緑へ戻っており、一過性だった。
# **再試行は握り潰しではない** — 失敗した試行はすべてログに出す。
LOOKUP_ATTEMPTS = 3
WRITE_ATTEMPTS = 2


class GhResult:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _backoff_seconds() -> float:
    raw = os.environ.get("REPORT_FAILURE_BACKOFF_SECONDS", "2")
    try:
        return max(0.0, float(raw))
    except ValueError:
        # 値が壊れていても通報自体は止めない。ただし黙って既定値に戻さず理由を出す。
        log(f"::warning::REPORT_FAILURE_BACKOFF_SECONDS={raw!r} を数値として読めないため 2 秒を使う")
        return 2.0


def log(message: str) -> None:
    print(message, flush=True)


def summary(message: str) -> None:
    """Actions のジョブサマリにも残す (ログを開かなくても気づけるように)。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except OSError as exc:
        # サマリに書けないこと自体で通報を落とさない。ただし黙らない
        # (見えなくなるのは「サマリ欄からの気づき」だけで、ログには残る)。
        log(f"::warning::step summary に書けなかった: {exc}")


def run_gh(
    args: list[str], *, label: str, attempts: int = 1, quiet_failure: bool = False
) -> GhResult:
    """gh を叩く。**標準エラーは捨てず、失敗した試行を毎回ログに出す。**

    quiet_failure=True は「非ゼロが正常運用に含まれる」呼び出し (ラベルの既存衝突) 用。
    **失敗を隠すためではない** — 呼び出し側が分類して必ず結果を 1 行出す責任を持つ。
    """
    backoff = _backoff_seconds()
    result = GhResult(1, "", "gh を 1 度も実行できなかった")
    for attempt in range(1, attempts + 1):
        try:
            completed = subprocess.run(
                ["gh", *args],
                capture_output=True,
                text=True,
                timeout=120,
            )
            result = GhResult(completed.returncode, completed.stdout, completed.stderr)
        except (OSError, subprocess.SubprocessError) as exc:
            result = GhResult(1, "", f"{type(exc).__name__}: {exc}")
        if result.ok:
            if attempt > 1:
                log(f"{label}: {attempt} 回目で成功した")
            return result
        if not quiet_failure:
            log(
                f"{label}: 失敗 ({attempt}/{attempts}) rc={result.returncode} "
                f"cmd=`gh {' '.join(args)}` stderr={_one_line(result.stderr)}"
            )
        if attempt < attempts:
            time.sleep(backoff * attempt)
    return result


def _one_line(text: str, limit: int = 300) -> str:
    flat = " ".join(text.split())
    return flat[:limit] if flat else "(空)"


def _flag(value: str) -> bool:
    return value.strip().lower() == "true"


def main() -> int:
    job_status = os.environ.get("JOB_STATUS", "")
    work_ran = _flag(os.environ.get("WORK_RAN", ""))
    cancelled_is_failure = _flag(os.environ.get("CANCELLED_IS_FAILURE", "true"))
    workflow = os.environ.get("WF", "")
    repo = os.environ.get("REPO", "")
    run_url = os.environ.get("RUN_URL", "")
    sha = os.environ.get("SHA", "")
    ref = os.environ.get("REF", "")
    what = os.environ.get("WHAT", "")

    title = rules.issue_title(workflow)
    # 最初の 1 行は gh を叩く前に出す。ここから先で何が起きても、
    # 「無言の exit 1」(#507) にはならない。
    log(
        f"通報ステップ開始: job={job_status or '(空)'} / work-ran={work_ran} / "
        f"cancelled-is-failure={cancelled_is_failure} / title={title!r}"
    )

    failures: list[str] = []
    if not job_status.strip():
        # job.status が取れないと「復旧」も「障害」も判定できない。黙って noop にしない。
        failures.append("JOB_STATUS が空 (呼び出し元の job-status 入力を確認)")

    ensure_label(repo, failures)
    lookup_ok, existing = find_open_issue(repo, title, failures)

    action = rules.plan_action(
        job_status,
        work_ran,
        cancelled_is_failure,
        lookup_ok=lookup_ok,
        has_open_issue=existing is not None,
    )
    log(f"判定: {action} (open Issue={existing if existing is not None else 'なし/不明'})")
    if not lookup_ok and action == rules.OPEN:
        # 「一覧が引けなかった」を「open Issue は無い」に丸めると、この 1 行が消えて
        # 重複 Issue が理由不明で湧く。丸めた瞬間に test_notify.py が落ちる。
        log(
            "一覧が引けなかったため、既存の Issue と重複する可能性がある状態で新規に立てる "
            "(無通報より重複を選ぶ / ADR 0035 D2)"
        )

    apply_action(action, repo, title, existing, what, workflow, run_url, sha, ref, failures)

    verdict = rules.step_verdict(job_status, cancelled_is_failure, failures)
    if failures:
        for item in failures:
            log(f"::error::通報に失敗: {item}")
        summary(f"### ⚠️ {workflow} の通報ステップ\n\n{verdict.message}\n")
    log(f"通報ステップ終了: {verdict.message}")
    return 1 if verdict.fail_step else 0


def ensure_label(repo: str, failures: list[str]) -> None:
    result = run_gh(
        [
            "label",
            "create",
            LABEL,
            "-R",
            repo,
            "--color",
            LABEL_COLOR,
            "--description",
            LABEL_DESCRIPTION,
        ],
        label="ラベル作成",
        quiet_failure=True,  # 既存衝突が既定なので、分類してから下で必ず 1 行出す
    )
    kind = rules.classify_label_create(result.returncode, result.stderr)
    if kind == "created":
        log(f"ラベル {LABEL} を作成した")
    elif kind == "already-exists":
        log(f"ラベル {LABEL} は既にある (正常)")
    else:
        failures.append(
            f"ラベル {LABEL} を用意できなかった (rc={result.returncode}): "
            f"{_one_line(result.stderr)}"
        )


def find_open_issue(repo: str, title: str, failures: list[str]) -> tuple[bool, int | None]:
    """open な ci-failure Issue を 1 件探す。戻り値は (一覧が引けたか, 番号 or None)。

    **旧実装が無言で死んでいたのがここ。** 引けなかったことを「Issue は無い」と
    同じ値に丸めると、失敗した回に Issue が立たない / 成功した回に Issue が
    立ってしまうので、「引けなかった」は別の値で返す。
    """
    result = run_gh(
        [
            "issue",
            "list",
            "-R",
            repo,
            "--label",
            LABEL,
            "--state",
            "open",
            "--json",
            "number,title",
            "--limit",
            "100",
        ],
        label="open な ci-failure Issue の一覧取得",
        attempts=LOOKUP_ATTEMPTS,
    )
    if not result.ok:
        failures.append(
            f"open Issue の一覧を取得できなかった (rc={result.returncode}): "
            f"{_one_line(result.stderr)}"
        )
        return False, None
    try:
        issues = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        failures.append(f"open Issue の一覧を JSON として読めなかった: {exc}")
        return False, None
    if not isinstance(issues, list):
        failures.append(f"open Issue の一覧が配列ではない: {_one_line(str(issues))}")
        return False, None
    return True, rules.select_issue_number(issues, title)


def apply_action(
    action: str,
    repo: str,
    title: str,
    existing: int | None,
    what: str,
    workflow: str,
    run_url: str,
    sha: str,
    ref: str,
    failures: list[str],
) -> None:
    if action == rules.NOOP:
        log("何もしない")
        return

    if action in (rules.CLOSE, rules.APPEND):
        if existing is None:
            failures.append(f"{action} と判定したのに対象 Issue 番号が無い (実装の不整合)")
            return
        body = (
            rules.recovery_comment(run_url, sha, ref)
            if action == rules.CLOSE
            else rules.failure_comment(run_url, sha, ref)
        )
        comment = run_gh(
            ["issue", "comment", str(existing), "-R", repo, "--body", body],
            label=f"#{existing} へのコメント",
            attempts=WRITE_ATTEMPTS,
        )
        if not comment.ok:
            failures.append(
                f"#{existing} にコメントできなかった: {_one_line(comment.stderr)}"
            )
        if action == rules.APPEND:
            log(f"既存の #{existing} に追記した")
            return
        closed = run_gh(
            ["issue", "close", str(existing), "-R", repo, "--reason", "completed"],
            label=f"#{existing} のクローズ",
            attempts=WRITE_ATTEMPTS,
        )
        if closed.ok:
            log(f"復旧したので #{existing} を閉じた")
        else:
            failures.append(f"#{existing} を閉じられなかった: {_one_line(closed.stderr)}")
        return

    created = run_gh(
        [
            "issue",
            "create",
            "-R",
            repo,
            "--title",
            title,
            "--label",
            LABEL,
            "--body",
            rules.issue_body(what, workflow, run_url, sha, ref),
        ],
        label="Issue の新規作成",
        attempts=WRITE_ATTEMPTS,
    )
    if created.ok:
        log(f"新規 Issue を立てた: {_one_line(created.stdout)}")
    else:
        failures.append(f"Issue を作成できなかった: {_one_line(created.stderr)}")


def run() -> int:
    """main() を包み、**想定外の例外でも無言にせず、exit code は同じ規則で決める**。

    握るのは exit code だけで traceback は必ず出す。ここを素通しにすると、
    通報の想定外エラーで「デプロイは成功したのに run が赤」に戻る (#507)。
    """
    try:
        return main()
    except Exception:  # noqa: BLE001 — 想定外でも「無言の exit 1」にしない (#507)
        log("::error::通報ステップが想定外の例外で落ちた (下の traceback を参照)")
        traceback.print_exc()
        verdict = rules.step_verdict(
            os.environ.get("JOB_STATUS", ""),
            _flag(os.environ.get("CANCELLED_IS_FAILURE", "true")),
            ["通報ステップが想定外の例外で落ちた"],
        )
        summary(f"### ⚠️ 通報ステップが想定外の例外で落ちた\n\n{verdict.message}\n")
        log(f"通報ステップ終了: {verdict.message}")
        return 1 if verdict.fail_step else 0


if __name__ == "__main__":
    sys.exit(run())
