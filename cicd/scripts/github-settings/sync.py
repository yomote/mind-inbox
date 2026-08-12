#!/usr/bin/env python3
"""GitHub 設定の点検 / 適用 (Issue #344) — I/O だけ。判定は settings_diff.py。

このスクリプトがやること:
    1. 宣言 (cicd/github/settings.yml) を読んで検証する
    2. `gh api` で現実を読む (read-only)
    3. 差分と適用計画を計算する (settings_diff.py の純関数)
    4. 事実のスナップショット (JSON) と人間向けレポート (markdown) を書き出す
    5. --mode apply のときだけ、計画を順に適用して**適用後にもう一度読み直す**
       (ADR 0018 — 「設定した」ではなく振る舞いで確かめる)

使い方:
    sync.py --mode check --declaration cicd/github/settings.yml \\
            --snapshot-out /tmp/snapshot.json --report-out /tmp/report.md
    sync.py --mode apply --declaration ... --snapshot-out ... --report-out ... \\
            --expect-digest <check が出した 12 桁> [--allow-weakening]

認証:
    `gh` が認証済みであること。Actions では **device-code で PO 本人が対話認証した
    トークン**を使う (GITHUB_TOKEN はリポジトリ管理系 API に届かない)。
    このスクリプトはトークンを読まない・書かない・出力しない。

stdout に JSON 1 個 (workflow が jq で読む):
    {"mode","repository","drift","unavailable","in_sync","digest","weakening",
     "applied":[...],"failed":[...],"skipped":[...]}

終了コード:
    0 = 実行できた (差分の有無は JSON で表す)
    1 = 実行できなかった / 適用に失敗した (宣言が不正・gh が未認証・
        ダイジェスト不一致・許可なしの weaken・API 失敗)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from settings_diff import (  # noqa: E402
    DeclarationError,
    Operation,
    Unavailable,
    build_plan,
    diff_settings,
    normalize_branch_protection,
    normalize_security,
    plan_digest,
    render_report,
    render_snapshot,
    validate_declaration,
    weakening_operations,
)

GH_TIMEOUT_SECONDS = 60


def log(message: str) -> None:
    print(message, file=sys.stderr)


def gh_api(
    endpoint: str, method: str = "GET", payload: dict | None = None
) -> tuple[int, str, str]:
    """`gh api` を 1 回叩く。戻りは (returncode, stdout, stderr)。

    例外にせず戻り値で返すのは、404 が「無い」を意味する API が複数あるため
    (呼び出し側が意味を決める)。**stderr はそのまま呼び出し元に渡す** —
    握り潰すと「読めなかった理由」が消える。
    """
    args = ["gh", "api", "-X", method, endpoint]
    if payload is not None:
        args += ["--input", "-"]
    try:
        proc = subprocess.run(
            args,
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return 127, "", "gh コマンドが見つかりません"
    except subprocess.TimeoutExpired:
        return 124, "", f"gh api がタイムアウトしました ({GH_TIMEOUT_SECONDS}s)"
    return proc.returncode, proc.stdout, proc.stderr


def short_reason(stderr: str, rc: int) -> str:
    """スナップショットに載せる短い理由。

    **毎回変わる情報 (時刻 / request id / URL) を落とす** — 揺れる文字列を
    スナップショットに書くと、差分がノイズだらけになって履歴が読めなくなる。
    """
    for line in stderr.splitlines():
        line = line.strip()
        if line.startswith("gh: ") or "HTTP " in line:
            if "HTTP 401" in line:
                return "HTTP 401 (未認証)"
            if "HTTP 403" in line:
                return "HTTP 403 (権限なし / 機能が無効)"
            if "HTTP 404" in line:
                return "HTTP 404 (見つからない / 無効)"
            if "HTTP 5" in line:
                return "HTTP 5xx (GitHub 側の一時障害)"
    return f"gh api が失敗しました (exit {rc})"


def parse_json(raw: str) -> dict | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


# --- 現実を読む (read-only) -------------------------------------------------


def read_security(repo: str) -> dict:
    rc, out, err = gh_api(f"repos/{repo}")
    repo_doc: dict | Unavailable
    if rc != 0 or parse_json(out) is None:
        repo_doc = Unavailable(short_reason(err, rc))
    else:
        repo_doc = parse_json(out) or {}

    # 204 = 有効 / 404 = 無効。gh は 404 を非 0 で返す
    rc, _out, err = gh_api(f"repos/{repo}/vulnerability-alerts")
    if rc == 0:
        alerts: bool | Unavailable = True
    elif "HTTP 404" in err:
        alerts = False
    else:
        alerts = Unavailable(short_reason(err, rc))

    rc, out, err = gh_api(f"repos/{repo}/automated-security-fixes")
    fixes: dict | None | Unavailable
    if rc == 0 and parse_json(out) is not None:
        fixes = parse_json(out)
    else:
        fixes = Unavailable(short_reason(err, rc))

    rc, out, err = gh_api(f"repos/{repo}/code-scanning/default-setup")
    scanning: dict | None | Unavailable
    if rc == 0 and parse_json(out) is not None:
        scanning = parse_json(out)
    elif "HTTP 404" in err:
        scanning = {"state": "not-configured"}
    else:
        scanning = Unavailable(short_reason(err, rc))

    return normalize_security(repo_doc, alerts, fixes, scanning)


def read_branch_protection(repo: str, branch: str) -> dict | Unavailable:
    rc, out, err = gh_api(f"repos/{repo}/branches/{branch}/protection")
    if rc == 0:
        raw = parse_json(out)
        if raw is None:
            return Unavailable("応答が JSON ではありませんでした")
        return normalize_branch_protection(raw)
    if "HTTP 404" in err:
        # 404 は「保護されていない」と「ブランチが無い」の両方で返る。
        # 区別しないと、存在しないブランチを「未保護」と報告して
        # 適用しようとして必ず失敗する
        rc2, _out2, err2 = gh_api(f"repos/{repo}/branches/{branch}")
        if rc2 == 0:
            return normalize_branch_protection(None)
        if "HTTP 404" in err2:
            return Unavailable("ブランチがありません")
        return Unavailable(short_reason(err2, rc2))
    return Unavailable(short_reason(err, rc))


def read_actual(repo: str, declaration: dict) -> dict:
    return {
        "security": read_security(repo),
        "branch_protection": {
            branch: read_branch_protection(repo, branch)
            for branch in sorted(declaration["branch_protection"])
        },
    }


# --- 適用 -------------------------------------------------------------------


def apply_plan(
    plan: tuple[Operation, ...],
) -> tuple[list[str], list[tuple[str, str]], list[str]]:
    """計画を順に適用する。戻りは (適用済み, 失敗, 未適用)。

    GitHub の設定変更にトランザクションは無い。**途中で落ちたときに「何が適用済みで
    何が未適用か」を必ず出す** — 分からないまま終わるのが最悪の状態。
    1 操作は 1 API 呼び出しなので、途中終了しても「操作の途中」にはならない。
    順序 (強める→弱める) は settings_diff.order_plan が持つ。
    """
    applied: list[str] = []
    failed: list[tuple[str, str]] = []
    skipped: list[str] = []

    for index, op in enumerate(plan):
        if failed:
            skipped.append(op.op_id)
            continue
        log(f"[{index + 1}/{len(plan)}] {op.method} {op.endpoint} — {op.summary}")
        rc, _out, err = gh_api(op.endpoint, method=op.method, payload=op.payload)
        if rc == 0:
            applied.append(op.op_id)
            log(f"    ✅ 適用しました: {op.op_id}")
            continue
        reason = short_reason(err, rc)
        failed.append((op.op_id, reason))
        log(f"    ❌ 失敗しました: {op.op_id} — {reason}")
        log(f"    {err.strip()[:500]}")
    return applied, failed, skipped


# --- 入口 -------------------------------------------------------------------


def load_declaration(path: Path) -> dict:
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover - 実行環境の前提
        raise DeclarationError(
            "PyYAML がありません (workflow は pip install pyyaml==6.0.3 で入れています)"
        ) from exc
    with path.open(encoding="utf-8") as handle:
        return validate_declaration(yaml.safe_load(handle))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="GitHub 設定の点検 / 適用")
    parser.add_argument("--mode", choices=("check", "apply"), required=True)
    parser.add_argument("--declaration", required=True, type=Path)
    parser.add_argument("--repository", default=None, help="既定は $GITHUB_REPOSITORY")
    parser.add_argument("--snapshot-out", type=Path, default=None)
    parser.add_argument("--report-out", type=Path, default=None)
    parser.add_argument(
        "--expect-digest",
        default="",
        help="apply 必須: check が出した計画ダイジェスト。一致しなければ何もしない",
    )
    parser.add_argument("--allow-weakening", action="store_true")
    args = parser.parse_args(argv)

    repo = args.repository or os.environ.get("GITHUB_REPOSITORY", "")
    if not repo or repo.count("/") != 1:
        log("対象リポジトリが分かりません (--repository か $GITHUB_REPOSITORY)")
        return 1

    try:
        declaration = load_declaration(args.declaration)
    except (DeclarationError, OSError) as exc:
        log(f"宣言を読めません: {exc}")
        return 1
    except Exception as exc:  # yaml のパースエラー等
        log(f"宣言を読めません: {exc}")
        return 1

    actual = read_actual(repo, declaration)
    report = diff_settings(declaration, actual)
    plan = build_plan(declaration, report, repo)
    digest = plan_digest(plan)
    weak = weakening_operations(plan)

    applied: list[str] = []
    failed: list[tuple[str, str]] = []
    skipped: list[str] = []
    exit_code = 0

    if args.mode == "apply":
        if not plan:
            log("差分がありません — 適用するものはありません")
        elif args.expect_digest != digest:
            log(
                "ダイジェストが一致しません。**何も適用していません**。\n"
                f"  期待 (入力): {args.expect_digest or '(空)'}\n"
                f"  実際 (いま計算した計画): {digest}\n"
                "  宣言か現実が check の時点から変わっています。"
                "check をもう一度流し、出てきた差分を読んでからやり直してください。"
            )
            exit_code = 1
        elif weak and not args.allow_weakening:
            log(
                f"保護を弱める操作が {len(weak)} 件あります。"
                "allow_weakening が無いので**何も適用していません**。\n"
                + "\n".join(f"  - {o.op_id}: {o.summary}" for o in weak)
            )
            exit_code = 1
        else:
            applied, failed, skipped = apply_plan(plan)
            # 適用したと言い切る前に、もう一度読み直して振る舞いで確かめる (ADR 0018)
            actual = read_actual(repo, declaration)
            report = diff_settings(declaration, actual)
            plan = build_plan(declaration, report, repo)
            digest = plan_digest(plan)
            weak = weakening_operations(plan)
            if failed:
                exit_code = 1
            elif report.findings:
                # API は成功と言ったのに、読み直すとまだ違う。「設定した」ではなく
                # 振る舞いで見る (ADR 0018) — ここを緑にすると嘘が定着する
                log(
                    f"適用後に読み直すと、まだ {len(report.findings)} 項目の差分が残っています。"
                    "API は成功を返したのに反映されていません。"
                )
                exit_code = 1

    if args.snapshot_out:
        args.snapshot_out.write_text(render_snapshot(repo, actual), encoding="utf-8")
    if args.report_out:
        args.report_out.write_text(
            render_report(
                repo,
                report,
                plan,
                args.mode,
                applied=tuple(applied),
                failed=tuple(failed),
                skipped=tuple(skipped),
            ),
            encoding="utf-8",
        )

    summary = {
        "mode": args.mode,
        "repository": repo,
        "drift": len(report.findings),
        "unavailable": len(report.unavailable),
        "unmanaged": len(report.unmanaged),
        "in_sync": report.in_sync,
        "complete": report.complete,
        "digest": digest,
        "weakening": len(weak),
        "applied": applied,
        "failed": [op_id for op_id, _ in failed],
        "skipped": skipped,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
