#!/usr/bin/env python3
"""マージの門 (review-gate) — PR がマージ可能かを判定して commit status を貼る (ADR 0036)。

使い方:
    python3 cicd/scripts/review-gate/check.py <pr_number>    # 門の判定 + advisory + マージ執行
    python3 cicd/scripts/review-gate/check.py --advisory-sweep
      # schedule 用: open PR (base=main) を列挙して advisory とマージ執行・
      # ストール検知を適用する。status は貼らない。イベント駆動だけだと
      # 「push 直後は猶予内 → その後イベントが来ない」で advisory が一度も
      # 発火しないため (PR #238 P1 指摘)
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
       Codex のレビューが付いている (login が CODEX_LOGIN_PATTERN にマッチする投稿。
       指摘ゼロの clean review は issue コメントにしか痕跡が残らないため、
       「Codex Review」ヘッダを持つ issue コメントも数える — PR #239 実測)

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

さらにマージまで執行する (ADR 0040 D1 / Issue #253):
    GitHub の auto-merge は「最後の required check を GITHUB_TOKEN が緑にする」と
    発火しない (GITHUB_TOKEN 起点のイベントは後続自動化に連鎖しない — PR #247 が
    全条件 🟢 のまま 18 分放置された実測)。review-gate の success status がまさに
    その「最後の緑」になるため、workflow 自身がマージまで執行する:
    - success status を貼った直後、**auto-merge が有効化されている PR に限り**
      squash マージ API を叩く (有効化 = PM の受け入れ意思表示。対象を広げない)。
      405/409 等 (他 check 未完・base 遅れ・競合) は黙ってスキップし、理由をログに出す
    - schedule sweep も同じマージ試行を行う (最悪 30 分でマージされる下限保証)
    - リリース PR (base ≠ main) / needs-human ラベル付き PR は対象外
    - GITHUB_TOKEN のマージ push は deploy.yml 等の push トリガーも起動しない
      (同じ連鎖抑止) ため、マージ後に workflow_dispatch (GITHUB_TOKEN の例外) で
      build-images / deploy を明示的に叩いて補償する

ストール検知 (同じ sweep 内 / ADR 0040 D1):
    - 全 check 🟢 + auto-merge 有効なのに 2 時間以上未マージ → PR にコメントで記録
      (マージ試行が失敗し続けている異常の可視化)
    - needs-human ラベルの open Issue / `Status: Proposed` の ADR が 48 時間以上
      更新なし → @yomote メンション付きで通知 (Issue へコメント / ADR は集約 Issue)
    - 再通知は「最後の通知から 24 時間」のクールダウンで抑止 (マーカー + 投稿時刻)

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
from pathlib import Path

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


def is_codex_login(login: str, pattern: str) -> bool:
    return pattern.lower() in (login or "").lower()


def is_codex_review_result(body: str) -> bool:
    """Codex bot の issue コメントが「レビュー結果」か。

    Codex は**指摘ゼロのとき review オブジェクトを作らず、issue コメント**
    (「Codex Review: Didn't find any major issues.」) だけを残す (PR #239 実測)。
    一方で同じ bot は非レビューの定型応答も投稿する — bot メンションへの
    アカウント案内「To use Codex here, create a Codex account…」と、エラーの
    「Codex couldn't complete this request. Try again later.」。これらを既着と
    数えると、再トリガー依頼 (advisory) が発火しなくなる。
    「Codex Review」ヘッダの有無で区別する (定型応答には現れない)。
    """
    return "codex review" in body.lower()


def codex_present_in(
    review_logins: list[str],
    issue_comments: list[tuple[str, str]],
    pattern: str,
) -> bool:
    """Codex レビュー既着の判定 (純関数)。

    review_logins: pulls/N/reviews + pulls/N/comments の投稿者 login 列
    (レビューオブジェクト・レビューコメントは存在自体がレビューの証拠)。
    issue_comments: issues/N/comments の (login, body) 列 — こちらは
    **レビュー結果の本文を持つものだけ**を数える (is_codex_review_result)。
    """
    if any(is_codex_login(login, pattern) for login in review_logins):
        return True
    return any(
        is_codex_login(login, pattern) and is_codex_review_result(body)
        for login, body in issue_comments
    )


# ---- マージ執行 / ストール検知 (ADR 0040 D1 / Issue #253) ----

MERGE_STALL_MARKER = "<!-- merge-stall-alert -->"
HUMAN_STALL_MARKER = "<!-- human-stall-alert -->"
ADR_STALL_MARKER = "<!-- adr-stall-tracker -->"
NEEDS_HUMAN_LABEL = "needs-human"
HUMAN_MENTION = "@yomote"
# 全 check 🟢 + auto-merge 有効のまま未マージをストールと見なすまでの時間
MERGE_STALL_HOURS = 2.0
# needs-human Issue / Proposed ADR を停滞と見なすまでの時間
HUMAN_STALL_HOURS = 48.0
# 時限系通知の再通知クールダウン (最後の通知からこの時間は黙る)
RENOTIFY_COOLDOWN_HOURS = 24.0
# check run をマージ可と数える conclusion (GitHub の分岐保護と同じ扱い)
GREEN_CONCLUSIONS = ("success", "neutral", "skipped")
# GITHUB_TOKEN のマージ push では起動しない push トリガー workflow の補償対象
IMAGE_BUILD_PREFIXES = ("apps/services/ai-agent/", "apps/services/voicevox/")
IMAGE_BUILD_WORKFLOW = ".github/workflows/build-images.yml"
# ADR の Status 行 (MADR 形式の「- Status: Proposed」のみ拾う。本文中の引用は拾わない)
_PROPOSED_STATUS_PATTERN = re.compile(r"(?m)^-\s*Status:\s*Proposed\b")


def pr_has_label(pr: dict, name: str) -> bool:
    return any((label or {}).get("name") == name for label in (pr.get("labels") or []))


def should_execute_merge(pr: dict) -> tuple[bool, str]:
    """この PR にマージ API を叩いてよいか (実行するか, 理由) を返す。

    auto-merge の有効化 = PM の受け入れ意思表示、という設計を守る —
    ここが True を返すのは **PM が auto-merge を武装した PR だけ**。
    武装していない PR をマージ対象に広げない (ADR 0040 D1)。
    """
    if pr.get("state") != "open":
        return (False, "open でない")
    if pr.get("merged") or pr.get("merged_at"):
        return (False, "マージ済み")
    if pr.get("draft"):
        return (False, "draft")
    if (pr.get("base") or {}).get("ref") != "main":
        return (False, "base が main でない (リリース PR 等は門の対象外)")
    if not pr.get("auto_merge"):
        return (False, "auto-merge 未武装 (PM の受け入れ意思表示が無い)")
    if pr_has_label(pr, NEEDS_HUMAN_LABEL):
        return (False, f"{NEEDS_HUMAN_LABEL} ラベル付き (人間が押す)")
    return (True, "auto-merge 有効")


def merge_failure_reason(error_text: str) -> str:
    """マージ API の失敗をログ向けの理由に翻訳する (純関数)。

    405/409 は「まだマージできない」だけの正常系 (他 check 未完・base 遅れ・競合)
    なので黙ってスキップする — ただし理由はログに残す (Issue #253)。
    """
    if "405" in error_text:
        return "405: まだマージできない (他 required check 未完 / 保護ルール未達)"
    if "409" in error_text:
        return "409: head SHA が動いた — 次のイベント / sweep が再評価する"
    if "404" in error_text:
        return "404: PR が見えない (権限不足 — permissions: contents: write を確認)"
    head = " ".join(error_text.split())[:200]
    return f"想定外の失敗: {head}"


def review_gate_success_at(statuses: list[dict]) -> str | None:
    """combined status の statuses 列から review-gate=success の updated_at を返す。

    success でない (pending / failure / 不在) なら None — 呼び出し側は
    マージ試行もストール判定もしない。
    """
    for status in statuses:
        if status.get("context") == STATUS_CONTEXT and status.get("state") == "success":
            return status.get("updated_at")
    return None


def all_check_runs_green(check_runs: list[dict]) -> bool:
    """head SHA の check run が全部緑 (success/neutral/skipped で完了) か。

    走行中 (status != completed) が 1 つでもあれば False — 「全 required check 🟢
    なのに未マージ」のストール判定に使うので、未完を緑側に倒さない。
    check run ゼロは True (required の本体 review-gate は commit status 側で見る)。
    """
    return all(
        run.get("status") == "completed" and run.get("conclusion") in GREEN_CONCLUSIONS
        for run in check_runs
    )


def latest_iso(timestamps: list[str | None]) -> str | None:
    """ISO 8601 時刻列の最新を返す (None / 空文字は無視)。"""
    present = [t for t in timestamps if t]
    if not present:
        return None
    return max(present, key=lambda t: datetime.fromisoformat(t.replace("Z", "+00:00")))


def is_stale(updated_at_iso: str, now_iso: str, threshold_hours: float) -> bool:
    """updated_at から threshold_hours 以上経過しているか (ストール / 停滞判定)。"""
    return minutes_between(updated_at_iso, now_iso) >= threshold_hours * 60.0


def should_notify_again(
    marker: str,
    comments: list[tuple[str, str]],
    now_iso: str,
    cooldown_hours: float = RENOTIFY_COOLDOWN_HOURS,
) -> bool:
    """時限系通知を再投稿してよいか。comments は (本文, created_at) の列。

    advisory の「1 PR 1 回きり」(still_unposted) と違い、ストール通知は状態が
    続く限り再通知したい — ただし 30 分毎の sweep がそのまま吠えると 1 日
    48 連投になるため、**最後の通知から cooldown_hours は黙る** (Issue #253)。
    """
    posted_times = [created_at for body, created_at in comments if marker in body]
    last = latest_iso(posted_times)  # type: ignore[arg-type]
    if last is None:
        return True
    return minutes_between(last, now_iso) >= cooldown_hours * 60.0


def is_proposed_adr(text: str) -> bool:
    """ADR 本文が `- Status: Proposed` (MADR の Status 行) を持つか。

    行頭の Status 行だけを見る — 本文・引用中の「Status: Proposed」で
    Accepted 済み ADR を停滞と誤検知しない。
    """
    return bool(_PROPOSED_STATUS_PATTERN.search(text))


def human_queue_issues(issues: list[dict]) -> list[dict]:
    """issues API の結果から Issue だけを返す (PR を除く)。

    needs-human の停滞通知の対象は「open Issue」(Issue #253)。issues API は
    PR も混ぜて返すため、pull_request キーの有無で落とす。
    """
    return [issue for issue in issues if "pull_request" not in issue]


def needs_image_build(changed_paths: list[str]) -> bool:
    """マージ後に build-images.yml の dispatch が要るか (push トリガーの paths を写像)。"""
    return any(
        p.startswith(IMAGE_BUILD_PREFIXES) or p == IMAGE_BUILD_WORKFLOW
        for p in changed_paths
    )


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
    """Codex 連携アカウントのレビュー痕跡があるか (判定は codex_present_in)。

    見る場所は 3 つ。pulls/N/reviews と pulls/N/comments に加えて
    issues/N/comments も走査する — **指摘ゼロの clean review はレビュー
    オブジェクトを作らず issue コメントだけを残す** (PR #239 実測。ここを
    見ないと clean な PR ほど門が永遠に赤のままになる)。issue コメント側は
    同 bot の非レビュー定型応答を除くため本文でも絞る。
    """
    posts: list[dict] = []
    for path in (
        f"repos/{repo}/pulls/{number}/reviews",
        f"repos/{repo}/pulls/{number}/comments",
    ):
        posts.extend(gh("api", path, "--paginate"))  # type: ignore[arg-type]
    issue_comments = gh("api", f"repos/{repo}/issues/{number}/comments", "--paginate")
    return codex_present_in(
        review_logins=[(p.get("user") or {}).get("login", "") for p in posts],
        issue_comments=[
            (
                ((c.get("user") or {}).get("login", "")),  # type: ignore[union-attr]
                (c.get("body") or ""),  # type: ignore[union-attr]
            )
            for c in issue_comments
        ],
        pattern=pattern,
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


def fetch_comments_with_times(repo: str, number: int) -> list[tuple[str, str]]:
    """(本文, created_at) の列。時限系通知のクールダウン判定 (should_notify_again) 用。"""
    comments = gh("api", f"repos/{repo}/issues/{number}/comments", "--paginate")
    return [
        ((c.get("body") or ""), (c.get("created_at") or ""))  # type: ignore[union-attr]
        for c in comments
    ]


def try_merge(repo: str, number: int, head_sha: str) -> tuple[bool, str]:
    """squash マージ API を叩く。(成功したか, 失敗理由) を返す。

    sha を渡す — 判定に使った head から動いていたら GitHub 側が 409 で弾く
    (見ていないコミットをマージしない)。405/409 等は正常系のスキップとして
    ログに理由を出すだけで run は落とさない (Issue #253)。
    """
    try:
        gh(
            "api",
            "-X",
            "PUT",
            f"repos/{repo}/pulls/{number}/merge",
            "-f",
            "merge_method=squash",
            "-f",
            f"sha={head_sha}",
        )
    except subprocess.CalledProcessError as e:
        reason = merge_failure_reason(f"{e.stderr or ''} {e.stdout or ''}")
        print(f"#{number}: マージせずスキップ — {reason}")
        return (False, reason)
    except subprocess.TimeoutExpired:
        # 結果不明のままリトライすると 2 重マージは無い (2 回目は 405) が、
        # ここでは静かに次の sweep へ譲る
        print(f"#{number}: マージ API がタイムアウト — 次の sweep に任せる")
        return (False, "マージ API タイムアウト")
    print(f"#{number}: 🔀 マージ実行 (squash / sha {head_sha[:SHORT_SHA_LEN]})")
    return (True, "")


def dispatch_workflow(
    repo: str, workflow: str, inputs: dict[str, str] | None = None
) -> None:
    args = [
        "api",
        "-X",
        "POST",
        f"repos/{repo}/actions/workflows/{workflow}/dispatches",
        "-f",
        "ref=main",
    ]
    for key, value in (inputs or {}).items():
        args += ["-f", f"inputs[{key}]={value}"]
    gh(*args)
    print(f"workflow_dispatch: {workflow} を起動した")


def post_merge_followup(repo: str, changed_paths: list[str]) -> None:
    """GITHUB_TOKEN のマージ push は push トリガー workflow を起動しない —
    まさに #253 の原因と同じ連鎖抑止が、今度はマージの下流 (build-images /
    deploy) で起きる。workflow_dispatch は GITHUB_TOKEN でも起動できる
    (公式の例外) ので、push トリガー相当を明示的に叩いて補償する。

    - build-images: マージした PR が image のソースに触れたときだけ (paths の写像)
    - deploy: AUTO_DEPLOY_ENABLED=true のときだけ (push 経路と同じゲートを尊重する。
      dispatch の action=up は本来ゲート対象外なので、ここで見ないと未解禁のまま
      公開 URL へ自動デプロイしてしまう)
    dispatch の失敗は隠さない — 例外を上げて run を赤にする (マージ自体は済んでいる)。
    """
    if needs_image_build(changed_paths):
        dispatch_workflow(repo, "build-images.yml")
    if (os.environ.get("AUTO_DEPLOY_ENABLED", "") or "").lower() == "true":
        dispatch_workflow(
            repo,
            "deploy.yml",
            {"action": "up", "environment": "dev", "voicevox": "cpu"},
        )
    else:
        print(
            "AUTO_DEPLOY_ENABLED != true — deploy の dispatch はしない (push 経路と同じゲート)"
        )


def maybe_execute_merge(
    repo: str, number: int, pr: dict, head_sha: str, changed_paths: list[str]
) -> bool:
    """マージ執行の入口 (イベント駆動 / sweep 共用)。マージできたら True。"""
    ok, reason = should_execute_merge(pr)
    if not ok:
        print(f"#{number}: マージ執行の対象外 — {reason}")
        return False
    merged, _ = try_merge(repo, number, head_sha)
    if merged:
        post_merge_followup(repo, changed_paths)
    return merged


def maybe_report_merge_stall(
    repo: str, number: int, head_sha: str, gate_success_at: str, now_iso: str
) -> None:
    """マージ試行が失敗した PR について「全 check 🟢 のまま 2h 超」を可視化する。

    マージ執行が黙ってスキップし続ける失敗モード (原因: 保護ルールの想定外・
    権限退行など) は、放置すると #247 の 18 分どころか無期限に戻る。
    check が全部緑になった時刻から MERGE_STALL_HOURS 過ぎても open なら
    PR にコメントで記録する (クールダウン RENOTIFY_COOLDOWN_HOURS)。
    """
    combined = gh("api", f"repos/{repo}/commits/{head_sha}/status")
    if combined.get("state") != "success":  # type: ignore[union-attr]
        return
    payload = gh("api", f"repos/{repo}/commits/{head_sha}/check-runs?per_page=100")
    check_runs = payload.get("check_runs") or []  # type: ignore[union-attr]
    if not all_check_runs_green(check_runs):
        return
    green_since = latest_iso(
        [gate_success_at] + [run.get("completed_at") for run in check_runs]
    )
    if green_since is None or not is_stale(green_since, now_iso, MERGE_STALL_HOURS):
        return
    comments = fetch_comments_with_times(repo, number)
    if not should_notify_again(MERGE_STALL_MARKER, comments, now_iso):
        return
    hours = minutes_between(green_since, now_iso) / 60.0
    post_comment(
        repo,
        number,
        f"{MERGE_STALL_MARKER}\n"
        f"🕰 **全 check 🟢 + auto-merge 有効のまま {hours:.1f} 時間マージされていません。**"
        " review-gate のマージ執行 (ADR 0040 D1 / #253) が失敗し続けている異常の可視化です。"
        " 直近のマージ試行の失敗理由は review-gate の run ログを参照してください"
        " (次の通知は 24 時間後)。",
    )
    print(f"#{number}: ストール通知を投稿した ({hours:.1f}h)")


def fetch_last_commit_iso(repo: str, path: str) -> str | None:
    """path の最終 commit 日時 (main)。checkout は depth=1 で git log が使えないため API で取る。"""
    commits = gh("api", f"repos/{repo}/commits?path={path}&sha=main&per_page=1")
    if not commits:
        return None
    return commits[0]["commit"]["committer"]["date"]  # type: ignore[index]


def local_proposed_adrs(adr_dir: str = "docs/adr") -> list[str]:
    """checkout (schedule 実行なら main) から Status: Proposed の ADR パスを集める。"""
    root = Path(adr_dir)
    if not root.is_dir():
        return []
    return sorted(
        str(p)
        for p in root.glob("*.md")
        if is_proposed_adr(p.read_text(encoding="utf-8"))
    )


def notify_stale_proposed_adrs(
    repo: str, stale: list[tuple[str, str]], open_issues: list[dict], now_iso: str
) -> None:
    """停滞 Proposed ADR を needs-human キューの集約 Issue で通知する。

    ADR には「該当 Issue」が無いため、マーカー付きの集約 Issue を 1 本
    find-or-create する (needs-human ラベル = 人間の宿題キューに載る)。
    直近 24h 以内に close された集約 Issue があれば作り直さない —
    PO が「見た」と閉じた直後に 30 分で再起票するノイズを防ぐ。
    """
    listed = "\n".join(f"- `{path}` (最終更新 {when})" for path, when in stale)
    body = (
        f"{ADR_STALL_MARKER}\n"
        f"⏰ {HUMAN_MENTION} `Status: Proposed` のまま **48 時間以上更新の無い ADR** があります。"
        "debrief で Accept/Reject してください (ADR 0014 / 検知は review-gate sweep — #253):\n"
        f"{listed}\n\n"
        "解消したらこの Issue を close してください (状態が続く限り 24h クールダウンで再通知します)。"
    )
    tracker = next(
        (
            i
            for i in human_queue_issues(open_issues)
            if ADR_STALL_MARKER in (i.get("body") or "")
        ),
        None,
    )
    if tracker is not None:
        number = tracker["number"]
        comments = fetch_comments_with_times(repo, number)
        # Issue 本文のマーカーは起票時刻の証拠にならないので、本文の created_at も
        # クールダウンに数える (起票直後の sweep がコメントを重ねるのを防ぐ)
        comments.append((tracker.get("body") or "", tracker.get("created_at") or ""))
        if should_notify_again(ADR_STALL_MARKER, comments, now_iso):
            post_comment(repo, number, body)
            print(f"#{number}: Proposed ADR 停滞の再通知を投稿した ({len(stale)} 件)")
        return
    recently_closed = gh(
        "api",
        f"repos/{repo}/issues?state=closed&per_page=30&sort=updated&direction=desc",
    )
    for issue in human_queue_issues(recently_closed):  # type: ignore[arg-type]
        closed_at = issue.get("closed_at")
        if (
            ADR_STALL_MARKER in (issue.get("body") or "")
            and closed_at
            and not is_stale(closed_at, now_iso, RENOTIFY_COOLDOWN_HOURS)
        ):
            print(
                f"#{issue['number']}: 集約 Issue が 24h 以内に close 済み — 再起票しない"
            )
            return
    created = gh(
        "api",
        f"repos/{repo}/issues",
        "-f",
        "title=⏰ Proposed ADR の停滞 (48h 超) — debrief 待ち",
        "-f",
        f"body={body}",
        "-f",
        f"labels[]={NEEDS_HUMAN_LABEL}",
    )
    print(
        f"#{created.get('number')}: Proposed ADR 停滞の集約 Issue を起票した ({len(stale)} 件)"
    )  # type: ignore[union-attr]


def sweep_human_queue(repo: str, now_iso: str) -> None:
    """needs-human Issue / Proposed ADR の 48h 停滞を検知して通知する (ADR 0040 D1)。"""
    open_needs_human = gh(
        "api",
        f"repos/{repo}/issues?state=open&labels={NEEDS_HUMAN_LABEL}&per_page=100",
        "--paginate",
    )
    for issue in human_queue_issues(open_needs_human):  # type: ignore[arg-type]
        number = issue["number"]
        if not is_stale(issue.get("updated_at") or now_iso, now_iso, HUMAN_STALL_HOURS):
            continue
        comments = fetch_comments_with_times(repo, number)
        if not should_notify_again(HUMAN_STALL_MARKER, comments, now_iso):
            continue
        post_comment(
            repo,
            number,
            f"{HUMAN_STALL_MARKER}\n"
            f"⏰ {HUMAN_MENTION} この needs-human Issue は **48 時間以上更新がありません**。"
            "人間にしかできない宿題が停滞しています (検知: review-gate sweep — #253。"
            "対応不要なら close してください。次の通知は 24 時間後)。",
        )
        print(f"#{number}: needs-human 停滞の通知を投稿した")
    stale_adrs: list[tuple[str, str]] = []
    for path in local_proposed_adrs():
        last = fetch_last_commit_iso(repo, path)
        if last is None:
            print(
                f"{path}: main に commit が見つからない (未マージの ADR?) — 停滞判定せず skip"
            )
            continue
        if is_stale(last, now_iso, HUMAN_STALL_HOURS):
            stale_adrs.append((path, last))
    if stale_adrs:
        notify_stale_proposed_adrs(repo, stale_adrs, open_needs_human, now_iso)  # type: ignore[arg-type]
    else:
        print("Proposed ADR の 48h 停滞なし")


def run_advisory_sweep(repo: str) -> int:
    """open PR (base=main) に advisory とマージ執行・ストール検知を適用する。

    review-gate はイベント駆動のみのため、opened/synchronize 直後の run では
    経過が猶予 (既定 10 分) 未満で投稿されず、その後イベントが無ければ二度と
    評価されない — advisory が狙った失敗モード (Codex 沈黙 = イベントが来ない)
    でまさに発火しない。schedule (30 分毎) からこのモードを回して塞ぐ。
    status は貼らない (門の判定はイベント駆動の run が持つ)。

    マージ執行 (ADR 0040 D1 / #253) もここで再試行する — イベント駆動の
    マージ試行は「review-gate が最後の緑になった」場合しか成功しない
    (test.yml が後から緑になってもイベントは来ない) ため、sweep の再試行が
    「最悪 30 分でマージされる」下限保証を持つ。あわせて機械判定できる
    ストール (全 check 🟢 のまま 2h / needs-human・Proposed ADR の 48h) を通知する。
    API 呼び出しは open PR 数に比例 (PR あたり最大 6 往復 + 投稿)。
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    prs = gh(
        "api", f"repos/{repo}/pulls?state=open&base=main&per_page=100", "--paginate"
    )
    targets = sweep_targets(prs)  # type: ignore[arg-type]
    print(f"advisory sweep: open PR {len(prs)} 件中 対象 {len(targets)} 件")  # type: ignore[arg-type]
    for pr in targets:
        number = pr["number"]
        # --- マージ執行 + ストール検知 (auto-merge 武装済み + review-gate 🟢 のみ) ---
        armed, skip_reason = should_execute_merge(pr)
        if armed:
            head_sha = pr["head"]["sha"]
            combined = gh("api", f"repos/{repo}/commits/{head_sha}/status")
            gate_success = review_gate_success_at(combined.get("statuses") or [])  # type: ignore[union-attr]
            if gate_success is None:
                print(f"#{number}: review-gate が success でない — マージ試行せず")
            else:
                merged, _ = try_merge(repo, number, head_sha)
                if merged:
                    files = gh(
                        "api", f"repos/{repo}/pulls/{number}/files", "--paginate"
                    )
                    post_merge_followup(repo, [f["filename"] for f in files])  # type: ignore[union-attr]
                    continue  # マージ済み PR に advisory はもう要らない
                maybe_report_merge_stall(repo, number, head_sha, gate_success, now_iso)
        else:
            print(f"#{number}: マージ執行の対象外 — {skip_reason}")
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
    sweep_human_queue(repo, now_iso)
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
    if verdict.ok:
        # マージ執行 (ADR 0040 D1 / #253): この success status が「最後の required
        # check の緑」だった場合、GITHUB_TOKEN 起点のため GitHub の auto-merge は
        # 発火しない。auto-merge を武装済みの PR に限り、自分でマージまで執行する。
        # 405 (他 check 未完) 等は黙ってスキップ — sweep (30 分毎) が再試行する。
        merged = maybe_execute_merge(repo, number, pr, head_sha, changed_paths)
        if merged:
            return 0  # マージ済み — advisory 投稿は不要
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
