#!/usr/bin/env python3
"""マージの門 (review-gate) — PR がマージ可能かを判定して commit status を貼る (ADR 0036)。

使い方:
    python3 cicd/scripts/review-gate/check.py <pr_number>    # 門の判定 + advisory
    python3 cicd/scripts/review-gate/check.py --advisory-sweep
      # schedule 用: open PR (base=main) を列挙して advisory だけ適用する。
      # status は貼らない。イベント駆動だけだと「push 直後は猶予内 → その後
      # イベントが来ない」で advisory が一度も発火しないため (PR #238 P1 指摘)
      要: `gh` が認証済み (Actions では GH_TOKEN を渡す) / GITHUB_REPOSITORY

仕組み:
    貼るのは commit status `review-gate` (ブランチ保護が required check として読む)。
    workflow run の緑は「評価できたこと」しか意味しない — 判定の 🟢/🔴 は status 側に出る。

条件 (全部揃うまで failure):
    1. PM の受け入れコメント — `[pm-accept]` マーカー + いまの head SHA (先頭 7 桁) を
       含むコメントが PR にある。**SHA を含める規約により push で自動的に無効化される**
       (受け入れ後に積まれた未レビューコードのマージを防ぐ)
    2. レビュースレッドが全部解決している
    3. コード PR (apps/ か cicd/ に触れる) かつ REVIEW_GATE_REQUIRE_CODEX=true のとき、
       Codex のレビューが付いている (login が CODEX_LOGIN_PATTERN にマッチする投稿)

付随して、合否とは別に advisory のコメントを 2 種類だけ自動投稿する (ADR 0038):
    A. Codex 自動レビューの再トリガー依頼 — コード PR に Codex レビューが
       CODEX_RETRIGGER_MINUTES (既定 10) 分以上付いていなければ「接続済み
       アカウントから `@codex review` を投稿して」という依頼を 1 回だけ投稿する
       (自動トリガーはイベント欠落・枠切れでリトライされない — 2026-08-10 の
       PR #231 で 11 時間沈黙した実測への対処。bot の生メンションは Codex に
       無視されるため依頼形式 — PR #238 実測)
    B. 敏感パスの security review 依頼 — IaC / workflow / BFF の認証・トークン・
       CORS 関連等に触れる PR に `@codex security review` の依頼を 1 回だけ投稿する。
       **どちらも合否条件には入れない** (門を重くしない。入れるかは実測後に PO 判断)。
    冪等性はコメント本文のマーカー (機械可読) で判定し、投稿直前の再フェッチ確認 +
    workflow の per-PR concurrency で 2 重投稿を塞ぐ。イベントが来ない PR は
    schedule の advisory sweep (--advisory-sweep) が拾う。

規律 (status-page / ops-inspect と同じ):
    取れなかったものを「合格」と書かない。取得に失敗したら error status を貼って終わる。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

PM_ACCEPT_MARKER = "[pm-accept]"
# 受け入れとして数えるコメントの投稿者。**このリポジトリは public なので、
# 誰でも PR にコメントできる** — 投稿者を見ないと第三者が `[pm-accept] <sha>` と
# 書くだけで門が開く (2026-08-10 の受け入れレビューで発見)。
# GitHub が付ける author_association で絞る (本文からは詐称できない)。
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
CODE_PREFIXES = ("apps/", "cicd/")
STATUS_CONTEXT = "review-gate"
SHORT_SHA_LEN = 7

# ---- Codex 自動再トリガー / 敏感パス指名 (ADR 0038 — advisory、合否には入れない) ----

# 冪等マーカー。投稿済みかは本文のこの文字列で機械判定する (2 重投稿防止)。
CODEX_RETRIGGER_MARKER = "<!-- codex-auto-retrigger -->"
SECURITY_RETRIGGER_MARKER = "<!-- codex-security-retrigger -->"
# PR 作成 (または最新 push) からこの分数、Codex レビューが無ければ再トリガーする。
DEFAULT_RETRIGGER_MINUTES = 10.0

# security review を自動指名する敏感パス (初期セット — ADR 0038)。
# prefix 一致するもの:
SENSITIVE_PREFIXES = ("cicd/iac/", ".github/workflows/", ".github/actions/")
# apps/bff/src/** のうち、パスに認証・トークン・CORS の匂いがあるもの
# (機構で判定できる近似。取り逃しは release-gate の security-reviewer が持つ):
_SENSITIVE_BFF_PATTERN = re.compile(r"auth|token|cors", re.IGNORECASE)


def sensitive_paths(changed_paths: list[str]) -> list[str]:
    """変更ファイルのうち敏感パス (ADR 0038 の初期セット) に当たるものを返す。"""
    matched = []
    for path in changed_paths:
        basename = path.rsplit("/", 1)[-1]
        if (
            path.startswith(SENSITIVE_PREFIXES)
            or basename.startswith("local.settings")
            or (
                path.startswith("apps/bff/src/") and _SENSITIVE_BFF_PATTERN.search(path)
            )
        ):
            matched.append(path)
    return matched


def minutes_between(earlier_iso: str, later_iso: str) -> float:
    """ISO 8601 (GitHub API の `2026-08-11T03:00:00Z` 形式) の 2 時刻の差を分で返す。"""
    earlier = datetime.fromisoformat(earlier_iso.replace("Z", "+00:00"))
    later = datetime.fromisoformat(later_iso.replace("Z", "+00:00"))
    return (later - earlier).total_seconds() / 60.0


def should_retrigger_codex(
    *,
    code_pr: bool,
    draft: bool,
    codex_present: bool,
    marker_posted: bool,
    minutes_since_last_push: float,
    threshold_minutes: float = DEFAULT_RETRIGGER_MINUTES,
) -> bool:
    """`@codex review` を自動投稿すべきか。

    条件: コード PR / draft でない / Codex レビュー未着 / 再トリガー未投稿 /
    PR 作成 (または最新 push) から threshold 分以上経過。
    marker はコメント全量から探す — 1 PR につき 1 回しか投稿しない (push で
    リセットしない: 再トリガーしても沈黙するなら機構の外の問題で、
    毎 push 吠えても直らずノイズになるだけ)。
    """
    return (
        code_pr
        and not draft
        and not codex_present
        and not marker_posted
        and minutes_since_last_push >= threshold_minutes
    )


def should_request_security_review(
    *, draft: bool, sensitive: list[str], marker_posted: bool
) -> bool:
    """`@codex security review` を自動投稿すべきか (敏感パスに触れる PR に 1 回だけ)。"""
    return bool(sensitive) and not draft and not marker_posted


def still_unposted(marker: str, comment_bodies: list[str]) -> bool:
    """マーカー付きコメントがまだ無いか。

    初回スクリーニングと**投稿直前の再フェッチ確認** (PR #238 P2 指摘) の両方で使う。
    近接した 2 つの run が両方「未投稿」と観測すると 2 重投稿になるため、
    投稿直前に取り直したコメントでもう一度これを通す (workflow 側の per-PR
    concurrency と併せた二段防御。schedule sweep は PR 単位で直列化できないので
    こちらが必須の防御になる)。
    """
    return all(marker not in body for body in comment_bodies)


def sweep_targets(prs: list[dict]) -> list[dict]:
    """advisory sweep の対象 PR を選ぶ (PR #238 P1 指摘への対処)。

    対象: open / base=main / draft でない。閉じた PR・門の対象外 (base≠main)・
    draft を機械的に外す — sweep は 30 分毎に回るので、対象選定を誤ると
    無関係な PR へ毎回 API を叩き続ける。
    """
    return [
        pr
        for pr in prs
        if pr.get("state") == "open"
        and not pr.get("draft")
        and (pr.get("base") or {}).get("ref") == "main"
    ]


@dataclass
class Verdict:
    ok: bool
    missing: list[str] = field(default_factory=list)

    @property
    def description(self) -> str:
        text = (
            "OK: 受け入れ・スレッド・レビューが揃った"
            if self.ok
            else " / ".join(self.missing)
        )
        return text[:137] + "…" if len(text) > 140 else text


def is_code_pr(changed_paths: list[str]) -> bool:
    return any(p.startswith(CODE_PREFIXES) for p in changed_paths)


def has_pm_accept(comments: list[tuple[str, str]], head_sha: str) -> bool:
    """受け入れと数える条件は 3 つ全部。

    1. マーカー `[pm-accept]` を含む
    2. **いまの** head SHA (先頭 7 桁) を含む — push で自動失効させるため
    3. **投稿者がこのリポジトリの権限保持者** (author_association) — public リポジトリで
       第三者がコメントするだけで門が開くのを塞ぐ

    comments は (本文, author_association) の列。
    """
    short = head_sha[:SHORT_SHA_LEN]
    return any(
        PM_ACCEPT_MARKER in body
        and short in body
        and (association or "").upper() in TRUSTED_ASSOCIATIONS
        for body, association in comments
    )


def decide(
    head_sha: str,
    changed_paths: list[str],
    comments: list[tuple[str, str]],
    unresolved_threads: int,
    codex_present: bool,
    require_codex: bool,
) -> Verdict:
    missing: list[str] = []
    if not has_pm_accept(comments, head_sha):
        missing.append(f"PM 受け入れ ([pm-accept] + {head_sha[:SHORT_SHA_LEN]}) が無い")
    if unresolved_threads > 0:
        missing.append(f"未解決スレッド {unresolved_threads} 件")
    if require_codex and is_code_pr(changed_paths) and not codex_present:
        missing.append("Codex レビューが無い (コード PR)")
    return Verdict(ok=not missing, missing=missing)


# ---- ここから下は GitHub との入出力 (テスト対象は上の純粋ロジック) ----


def gh(*args: str) -> object:
    out = subprocess.run(
        ["gh", *args], capture_output=True, text=True, timeout=60, check=True
    )
    return json.loads(out.stdout) if out.stdout.strip() else {}


def fetch_unresolved_threads(owner: str, name: str, number: int) -> int:
    query = (
        "query($owner:String!,$name:String!,$number:Int!){"
        "repository(owner:$owner,name:$name){pullRequest(number:$number){"
        "reviewThreads(first:100){nodes{isResolved}}}}}"
    )
    data = gh(
        "api",
        "graphql",
        "-f",
        f"query={query}",
        "-F",
        f"owner={owner}",
        "-F",
        f"name={name}",
        "-F",
        f"number={number}",
    )
    nodes = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    return sum(1 for n in nodes if not n["isResolved"])


def fetch_codex_present(repo: str, number: int, pattern: str) -> bool:
    """Codex 連携アカウントの痕跡 (レビュー / レビューコメント) があるか。"""
    posts: list[dict] = []
    for path in (
        f"repos/{repo}/pulls/{number}/reviews",
        f"repos/{repo}/pulls/{number}/comments",
    ):
        posts.extend(gh("api", path, "--paginate"))  # type: ignore[arg-type]
    return any(
        pattern.lower() in (p.get("user") or {}).get("login", "").lower() for p in posts
    )


def fetch_head_pushed_at(repo: str, pr: dict) -> str:
    """「PR 作成または最新 push」の時刻 (ISO 8601)。

    push イベントそのものの時刻は PR API に無いため、head commit の committer date と
    PR 作成時刻の**遅い方**で近似する。rebase 等で commit date が古く出る分には
    経過が長く見えるだけ (再トリガーが早まる方向) で、created_at が下限になるため
    作成直後の誤射にはならない。
    """
    commit = gh("api", f"repos/{repo}/commits/{pr['head']['sha']}")
    committed = commit["commit"]["committer"]["date"]  # type: ignore[index]
    return max(str(committed), str(pr["created_at"]))


def post_comment(repo: str, number: int, body: str) -> None:
    gh("api", f"repos/{repo}/issues/{number}/comments", "-f", f"body={body}")


def fetch_comment_bodies(repo: str, number: int) -> list[str]:
    comments = gh("api", f"repos/{repo}/issues/{number}/comments", "--paginate")
    return [(c.get("body") or "") for c in comments]  # type: ignore[union-attr]


def post_advisory_once(repo: str, number: int, marker: str, body: str) -> bool:
    """投稿直前にコメントを**再フェッチ**してマーカー未投稿を確認してから投稿する。

    近接イベントの 2 run が両方「未投稿」と観測するレース (PR #238 P2 指摘) への
    二段防御の片翼。event 駆動の run は workflow の per-PR concurrency が直列化するが、
    schedule sweep の run は PR 単位の group にできないため、この再確認が必須。
    再フェッチ後〜投稿までの窓は残る (コメント API に条件付き書き込みが無い) が、
    直列化と併せて実用上の 2 重投稿を塞ぐ。
    """
    if not still_unposted(marker, fetch_comment_bodies(repo, number)):
        print(f"advisory: 再確認で {marker} を検出 — 並行 run が先行したため投稿しない")
        return False
    post_comment(repo, number, body)
    return True


def maybe_post_advisories(
    repo: str,
    number: int,
    pr: dict,
    changed_paths: list[str],
    comment_bodies: list[str],
    code_pr: bool,
    codex_present: bool,
) -> None:
    """合否とは別の advisory コメント 2 種 (ADR 0038)。判定は上の純粋関数、ここは I/O。"""
    if pr.get("state") != "open":
        return
    draft = bool(pr.get("draft"))
    threshold = float(
        os.environ.get("CODEX_RETRIGGER_MINUTES", "") or DEFAULT_RETRIGGER_MINUTES
    )
    retrigger_marker_posted = not still_unposted(CODEX_RETRIGGER_MARKER, comment_bodies)
    # 高い方 (API 1 往復) の取得は、安い条件が揃ったときだけ
    if code_pr and not draft and not codex_present and not retrigger_marker_posted:
        elapsed = minutes_between(
            fetch_head_pushed_at(repo, pr), datetime.now(timezone.utc).isoformat()
        )
        if should_retrigger_codex(
            code_pr=code_pr,
            draft=draft,
            codex_present=codex_present,
            marker_posted=retrigger_marker_posted,
            minutes_since_last_push=elapsed,
            threshold_minutes=threshold,
        ):
            # bot (github-actions) からの生メンションに Codex は応答しない —
            # 「To use Codex here, create a Codex account…」の定型返信が返るだけ
            # (2026-08-11 PR #238 で実測)。メンションはバッククォートで殺し、
            # 実際の投稿は接続済みユーザー (PM セッションの MCP 経由 = PO アカウント)
            # に依頼する形にする。PM は自 PR の webhook でこのコメントを受け取る。
            posted = post_advisory_once(
                repo,
                number,
                CODEX_RETRIGGER_MARKER,
                f"{CODEX_RETRIGGER_MARKER}\n"
                f"⏳ **Codex レビューが {threshold:.0f} 分以上未着です** (コード PR)。"
                "自動レビューのトリガーは欠落してもリトライされない"
                " (2026-08-10 PR #231 で 11 時間沈黙した実測) ため、"
                "**接続済みアカウントから `@codex review` を投稿してください**"
                " (bot からのメンションは Codex に無視される — ADR 0038 実測)。"
                " この投稿自体は review-gate の合否条件ではない。",
            )
            if posted:
                print(
                    f"advisory: Codex 未着 ({elapsed:.0f} 分) の再トリガー依頼を投稿した"
                )
    sensitive = sensitive_paths(changed_paths)
    if should_request_security_review(
        draft=draft,
        sensitive=sensitive,
        marker_posted=not still_unposted(SECURITY_RETRIGGER_MARKER, comment_bodies),
    ):
        listed = "\n".join(f"- `{p}`" for p in sensitive[:10])
        if len(sensitive) > 10:
            listed += f"\n- …他 {len(sensitive) - 10} 件"
        posted = post_advisory_once(
            repo,
            number,
            SECURITY_RETRIGGER_MARKER,
            f"{SECURITY_RETRIGGER_MARKER}\n"
            "🔒 **敏感パスに触れる PR です — security review を推奨します。**"
            "接続済みアカウントから `@codex security review` を投稿してください"
            " (bot からのメンションは Codex に無視される — ADR 0038 実測)。"
            " advisory であり review-gate の合否条件ではない。対象:\n" + listed,
        )
        if posted:
            print(
                f"advisory: 敏感パス {len(sensitive)} 件 → security review 依頼を投稿した"
            )


def run_advisory_sweep(repo: str) -> int:
    """open PR (base=main) に advisory だけを適用する (PR #238 P1 指摘への対処)。

    review-gate はイベント駆動のみのため、opened/synchronize 直後の run では
    経過が猶予 (既定 10 分) 未満で投稿されず、その後イベントが無ければ二度と
    評価されない — advisory が狙った失敗モード (Codex 沈黙 = イベントが来ない)
    でまさに発火しない。schedule (30 分毎) からこのモードを回して塞ぐ。
    status は貼らない (門の判定はイベント駆動の run が持つ)。
    API 呼び出しは open PR 数に比例 (PR あたり最大 4 往復 + 投稿)。
    """
    prs = gh(
        "api", f"repos/{repo}/pulls?state=open&base=main&per_page=100", "--paginate"
    )
    targets = sweep_targets(prs)  # type: ignore[arg-type]
    print(f"advisory sweep: open PR {len(prs)} 件中 対象 {len(targets)} 件")  # type: ignore[arg-type]
    for pr in targets:
        number = pr["number"]
        comment_bodies = fetch_comment_bodies(repo, number)
        # 両マーカー投稿済みならこの PR にやることは無い — files 取得を省く
        if not still_unposted(
            CODEX_RETRIGGER_MARKER, comment_bodies
        ) and not still_unposted(SECURITY_RETRIGGER_MARKER, comment_bodies):
            print(f"#{number}: 両 advisory 投稿済み — skip")
            continue
        files = gh("api", f"repos/{repo}/pulls/{number}/files", "--paginate")
        changed_paths = [f["filename"] for f in files]  # type: ignore[union-attr]
        code_pr = is_code_pr(changed_paths)
        codex_present = (
            fetch_codex_present(
                repo, number, os.environ.get("CODEX_LOGIN_PATTERN", "codex")
            )
            if (code_pr and still_unposted(CODEX_RETRIGGER_MARKER, comment_bodies))
            else False
        )
        maybe_post_advisories(
            repo=repo,
            number=number,
            pr=pr,
            changed_paths=changed_paths,
            comment_bodies=comment_bodies,
            code_pr=code_pr,
            codex_present=codex_present,
        )
    return 0


def post_status(repo: str, sha: str, state: str, description: str) -> None:
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    target = (
        f"https://github.com/{repo}/actions/runs/{run_id}"
        if run_id
        else f"https://github.com/{repo}"
    )
    gh(
        "api",
        f"repos/{repo}/statuses/{sha}",
        "-f",
        f"state={state}",
        "-f",
        f"context={STATUS_CONTEXT}",
        "-f",
        f"description={description}",
        "-f",
        f"target_url={target}",
    )


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    if sys.argv[1] == "--advisory-sweep":
        return run_advisory_sweep(repo)
    number = int(sys.argv[1])
    owner, name = repo.split("/")

    pr = gh("api", f"repos/{repo}/pulls/{number}")
    head_sha = pr["head"]["sha"]

    if pr["base"]["ref"] != "main":
        print(f"skip: base が main でない ({pr['base']['ref']}) — 門の対象外")
        return 0
    if pr.get("merged"):
        print("skip: マージ済み")
        return 0

    try:
        files = gh("api", f"repos/{repo}/pulls/{number}/files", "--paginate")
        changed_paths = [f["filename"] for f in files]  # type: ignore[union-attr]
        comments = gh("api", f"repos/{repo}/issues/{number}/comments", "--paginate")
        unresolved = fetch_unresolved_threads(owner, name, number)
        require_codex = (
            os.environ.get("REVIEW_GATE_REQUIRE_CODEX", "").lower() == "true"
        )
        code_pr = is_code_pr(changed_paths)
        # 合否 (require_codex) だけでなく自動再トリガー (ADR 0038) もコード PR で
        # Codex の既着を見るので、コード PR なら常に取得する
        codex_present = (
            fetch_codex_present(
                repo, number, os.environ.get("CODEX_LOGIN_PATTERN", "codex")
            )
            if (require_codex or code_pr)
            else False
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        KeyError,
        json.JSONDecodeError,
    ) as e:
        # 取れなかったものを「合格」と書かない — error を貼って赤のまま残す
        post_status(repo, head_sha, "error", f"評価に失敗: {e}"[:140])
        raise

    comment_pairs = [
        (c.get("body") or "", c.get("author_association") or "")  # type: ignore[union-attr]
        for c in comments
    ]
    verdict = decide(
        head_sha=head_sha,
        changed_paths=changed_paths,
        comments=comment_pairs,
        unresolved_threads=unresolved,
        codex_present=codex_present,
        require_codex=require_codex,
    )
    post_status(
        repo, head_sha, "success" if verdict.ok else "failure", verdict.description
    )
    print(
        f"review-gate → {'🟢' if verdict.ok else '🔴'} {verdict.description} (sha {head_sha[:7]})"
    )
    # 合否の後に advisory (ADR 0038)。ここで失敗しても status は貼れているが、
    # run は赤にして「投稿できなかったこと」を隠さない
    maybe_post_advisories(
        repo=repo,
        number=number,
        pr=pr,
        changed_paths=changed_paths,
        comment_bodies=[body for body, _ in comment_pairs],
        code_pr=code_pr,
        codex_present=codex_present,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
