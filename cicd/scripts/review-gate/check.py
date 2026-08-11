#!/usr/bin/env python3
"""マージの門 (review-gate) — PR がマージ可能かを判定して commit status を貼る (ADR 0036)。

使い方:
    python3 cicd/scripts/review-gate/check.py <pr_number>    # 門の判定 + advisory
    python3 cicd/scripts/review-gate/check.py --advisory-sweep
      # schedule 用: open PR (base=main) を列挙して advisory だけ適用する。
      # status は貼らない。イベント駆動だけだと「push 直後は猶予内 → その後
      # イベントが来ない」で advisory が一度も発火しないため (PR #238 P1 指摘)
    python3 cicd/scripts/review-gate/check.py --merge-group
      # merge_group イベント用 (ADR 0042): env MERGE_GROUP_HEAD_REF から対象 PR を
      # 解決し、同じ判定を行って **merge group の head SHA** (env MERGE_GROUP_HEAD_SHA)
      # に status を貼る (merge queue の required check はそこを見る)。
      # PR を解決できない場合は failure を貼る (安全側 — 静かに通さない)。
      # advisory は投稿しない (PR 側のイベント / sweep が持つ)
      要: `gh` が認証済み (Actions では GH_TOKEN を渡す) / GITHUB_REPOSITORY

仕組み:
    貼るのは commit status `review-gate` (ブランチ保護が required check として読む)。
    workflow run の緑は「評価できたこと」しか意味しない — 判定の 🟢/🔴 は status 側に出る。

条件 (全部揃うまで failure):
    1. PM の受け入れコメント — `[pm-accept]` マーカー + いまの head SHA (先頭 7 桁) を
       含むコメントが PR にある。**SHA を含める規約により push で自動的に無効化される**
       (受け入れ後に積まれた未レビューコードのマージを防ぐ)。
       例外 (ADR 0042 — pm-accept の引き継ぎ): 最新の `[pm-accept] <sha>` の <sha> から
       現 head までの追加コミットが **base (main) からのマージのみ** で、かつ
       **PR の実装差分 (base...head) が受け入れ時点と同一** なら、受け入れは現 head に
       引き継がれる。実装差分が 1 文字でも変わる push は従来どおり再受け入れが要る
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

規律 (status-page / ops-inspect と同じ):
    取れなかったものを「合格」と書かない。取得に失敗したら error status を貼って終わる。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
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

# ---- pm-accept の引き継ぎ / merge_group (ADR 0042) ----

# pm-accept コメント本文から受け入れ SHA を拾うトークン (7〜40 桁の hex)
HEX_TOKEN_RE = re.compile(r"\b[0-9a-fA-F]{7,40}\b")
# merge queue の一時 branch ref: gh-readonly-queue/<base>/pr-<番号>-<sha>
MERGE_GROUP_REF_RE = re.compile(r"(?:^|/)gh-readonly-queue/.+?/pr-(\d+)-")
# compare API の files は 300 件で打ち切られる (ページングなし)。
# 打ち切られた diff を「同一」と誤判定しないための上限。
COMPARE_FILES_CAP = 300
# pulls/N/commits は 250 コミットで打ち切られる。全量が見えない PR は引き継ぎ判定不能。
PR_COMMITS_CAP = 250

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


@dataclass
class Verdict:
    ok: bool
    missing: list[str] = field(default_factory=list)
    # 緑の補足 (例: pm-accept 引き継ぎ)。判定理由を status description に可視化する
    note: str = ""

    @property
    def description(self) -> str:
        if self.ok:
            text = (
                f"OK: {self.note}"
                if self.note
                else "OK: 受け入れ・スレッド・レビューが揃った"
            )
        else:
            text = " / ".join(self.missing)
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


def latest_pm_accept_token(comments: list[tuple[str, str]]) -> str | None:
    """最新の信頼できる pm-accept コメントから受け入れ SHA トークンを取る (ADR 0042)。

    comments は API 取得順 (created_at 昇順) の (本文, author_association)。
    末尾から走査して最初に見つかった信頼できる `[pm-accept]` コメントの、
    マーカーより後ろにある最初の hex トークン (7〜40 桁) を小文字で返す。
    **最新の受け入れだけを見る** — 古い受け入れへ遡って引き継がない
    (PM が受け入れをやり直したら新しい方が意思)。
    """
    for body, association in reversed(comments):
        if (association or "").upper() not in TRUSTED_ASSOCIATIONS:
            continue
        if PM_ACCEPT_MARKER not in body:
            continue
        tail = body[body.index(PM_ACCEPT_MARKER) + len(PM_ACCEPT_MARKER) :]
        m = HEX_TOKEN_RE.search(tail)
        return m.group(0).lower() if m else None
    return None


def parse_merge_group_pr(head_ref: str) -> int | None:
    """merge_group の head_ref (`gh-readonly-queue/<base>/pr-<N>-<sha>`) から PR 番号を返す。

    解決できなければ None — 呼び出し側は **failure を貼る** (安全側。
    静かに緑を貼ると未受け入れの PR が queue を通ってしまう)。
    """
    m = MERGE_GROUP_REF_RE.search(head_ref or "")
    return int(m.group(1)) if m else None


def diff_digest_from_files(files: list[dict]) -> str | None:
    """compare API の files[] から「PR の実装差分」の指紋を作る (ADR 0042)。

    filename / status / rename 元 / blob SHA / patch 本文を全部畳む —
    **1 文字でも変われば指紋が変わる** (引き継ぎ判定を緩めない要)。
    compare API は files を 300 件で打ち切る (ページング不可) ため、
    打ち切りの可能性がある場合は None = 判定不能を返す (「見えなかったものを
    同一と書かない」— status-page と同じ規律)。
    """
    if len(files) >= COMPARE_FILES_CAP:
        return None
    entries = sorted(
        (
            f.get("filename") or "",
            f.get("status") or "",
            f.get("previous_filename") or "",
            f.get("sha") or "",
            f.get("patch") or "",
        )
        for f in files
    )
    return hashlib.sha256(json.dumps(entries, ensure_ascii=False).encode()).hexdigest()


@dataclass
class Carryover:
    """pm-accept 引き継ぎ (ADR 0042) の判定結果。"""

    ok: bool
    accepted_short: str = ""
    detail: str = ""  # 不成立の理由 (status description に出して可視化する)


def evaluate_carryover(
    accepted_token: str,
    head_sha: str,
    pr_commits: list[dict],
    is_from_base: Callable[[str], bool],
    diff_digest: Callable[[str], str | None],
) -> Carryover:
    """受け入れ SHA から現 head への pm-accept 引き継ぎを判定する (ADR 0042 の核)。

    成立条件 (**全部**):
      1. accepted_token が PR のコミット列の中の 1 つに一意に解決できる
      2. 現 head から受け入れコミットまで **第一親を辿って到達できる**
         (rebase / force-push でコミットが書き換わっていない)
      3. その間の追加コミットが **すべて「base からのマージコミット」**
         (ちょうど 2 親、第二親が base に到達済み)。実装コミットが 1 つでも
         混ざれば不成立
      4. 実装差分 (base...head の compare) の指紋が受け入れ時点と **完全一致**。
         evil merge (マージに紛れた実装変更) はここで落ちる

    pr_commits: [{"sha": str, "parents": [str, ...]}, ...] (pulls/N/commits の写像)。
    is_from_base / diff_digest は I/O (compare API) を注入する — 構造条件が
    落ちたら呼ばれない (API 節約 + テスト可能性)。
    """
    shas = [c["sha"] for c in pr_commits]
    if head_sha.startswith(accepted_token):
        # 現 head そのものへの受け入れ — 引き継ぎ不要 (直接判定が通っているはず)
        return Carryover(ok=True, accepted_short=accepted_token[:SHORT_SHA_LEN])
    matches = [s for s in shas if s.startswith(accepted_token)]
    if len(matches) != 1:
        return Carryover(
            ok=False, detail="受け入れ SHA を PR コミット列に一意解決できない"
        )
    accepted = matches[0]
    parents_by_sha = {c["sha"]: list(c.get("parents") or []) for c in pr_commits}

    # head → accepted へ第一親で辿る。辿れなければ (rebase 等) 不成立
    chain: list[str] = []
    cur = head_sha
    while cur != accepted:
        if cur not in parents_by_sha or len(chain) > len(pr_commits):
            return Carryover(
                ok=False, detail="head から受け入れ SHA へ第一親で辿れない"
            )
        chain.append(cur)
        parents = parents_by_sha[cur]
        if not parents:
            return Carryover(
                ok=False, detail="head から受け入れ SHA へ第一親で辿れない"
            )
        cur = parents[0]

    for sha in chain:
        parents = parents_by_sha[sha]
        if len(parents) != 2 or not is_from_base(parents[1]):
            return Carryover(
                ok=False,
                detail=f"{sha[:SHORT_SHA_LEN]} が base からのマージでない",
            )

    digest_accepted = diff_digest(accepted)
    digest_head = diff_digest(head_sha)
    if digest_accepted is None or digest_head is None:
        return Carryover(ok=False, detail="実装差分を取得しきれない (files 300 件超)")
    if digest_accepted != digest_head:
        return Carryover(ok=False, detail="実装差分が受け入れ時点から変化")
    return Carryover(ok=True, accepted_short=accepted[:SHORT_SHA_LEN])


def decide(
    head_sha: str,
    changed_paths: list[str],
    comments: list[tuple[str, str]],
    unresolved_threads: int,
    codex_present: bool,
    require_codex: bool,
    carryover: Carryover | None = None,
) -> Verdict:
    missing: list[str] = []
    note = ""
    if has_pm_accept(comments, head_sha):
        pass  # 現 head への直接の受け入れ (従来どおり)
    elif carryover is not None and carryover.ok:
        note = f"pm-accept を {carryover.accepted_short} から引き継ぎ (差分不変)"
    else:
        item = f"PM 受け入れ ([pm-accept] + {head_sha[:SHORT_SHA_LEN]}) が無い"
        if carryover is not None and carryover.detail:
            item += f" (引き継ぎ不成立: {carryover.detail})"
        missing.append(item)
    if unresolved_threads > 0:
        missing.append(f"未解決スレッド {unresolved_threads} 件")
    if require_codex and is_code_pr(changed_paths) and not codex_present:
        missing.append("Codex レビューが無い (コード PR)")
    return Verdict(ok=not missing, missing=missing, note=note)


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


def compute_carryover(
    repo: str, pr: dict, comment_pairs: list[tuple[str, str]]
) -> Carryover | None:
    """pm-accept 引き継ぎ (ADR 0042) の I/O 部。判定は evaluate_carryover (純関数)。

    最新の pm-accept から SHA トークンが取れなければ None (引き継ぎの試行自体なし)。
    compare API は遅延評価 — 構造条件 (マージのみ) が落ちたら差分比較まで行かない。
    """
    token = latest_pm_accept_token(comment_pairs)
    if not token:
        return None
    number = pr["number"]
    base_ref = pr["base"]["ref"]
    commits = gh("api", f"repos/{repo}/pulls/{number}/commits", "--paginate")
    if len(commits) >= PR_COMMITS_CAP:  # type: ignore[arg-type]
        # エンドポイントの打ち切り — 全量が見えない PR は判定不能 (安全側)
        return Carryover(ok=False, detail=f"コミット {PR_COMMITS_CAP} 件超で判定不能")
    pr_commits = [
        {
            "sha": c["sha"],  # type: ignore[index, union-attr]
            "parents": [p["sha"] for p in (c.get("parents") or [])],  # type: ignore[union-attr]
        }
        for c in commits  # type: ignore[union-attr]
    ]

    def is_from_base(sha: str) -> bool:
        # base...sha の ahead_by == 0 ⇔ sha は base に到達済み (= main 由来)
        data = gh("api", f"repos/{repo}/compare/{base_ref}...{sha}")
        return data.get("ahead_by") == 0  # type: ignore[union-attr]

    def diff_digest(sha: str) -> str | None:
        # base...sha = merge-base からの三点比較 = PR の実装差分 (Files changed 相当)
        data = gh("api", f"repos/{repo}/compare/{base_ref}...{sha}")
        return diff_digest_from_files(data.get("files") or [])  # type: ignore[union-attr]

    return evaluate_carryover(
        accepted_token=token,
        head_sha=pr["head"]["sha"],
        pr_commits=pr_commits,
        is_from_base=is_from_base,
        diff_digest=diff_digest,
    )


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


def evaluate_pr(
    repo: str, number: int, *, status_sha: str | None = None, advisories: bool = True
) -> int:
    """PR を判定して commit status を貼る。

    status_sha: 貼り先 SHA。省略時は PR の head (pull_request イベント)。
    merge_group イベントでは merge group の head SHA を渡す — 判定材料
    (pm-accept / スレッド / Codex) は PR 側のまま、**required check が読む場所**
    だけが merge group 側になる (ADR 0042)。
    advisories: merge_group では False (advisory は PR 側イベント / sweep が持つ)。
    """
    owner, name = repo.split("/")

    pr = gh("api", f"repos/{repo}/pulls/{number}")
    head_sha = pr["head"]["sha"]
    target_sha = status_sha or head_sha

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
        comment_pairs = [
            (c.get("body") or "", c.get("author_association") or "")  # type: ignore[union-attr]
            for c in comments
        ]
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
        # 直接の受け入れが無いときだけ引き継ぎ (ADR 0042) を試す — API 節約
        carryover = (
            None
            if has_pm_accept(comment_pairs, head_sha)
            else compute_carryover(repo, pr, comment_pairs)
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        KeyError,
        json.JSONDecodeError,
    ) as e:
        # 取れなかったものを「合格」と書かない — error を貼って赤のまま残す
        post_status(repo, target_sha, "error", f"評価に失敗: {e}"[:140])
        raise

    verdict = decide(
        head_sha=head_sha,
        changed_paths=changed_paths,
        comments=comment_pairs,
        unresolved_threads=unresolved,
        codex_present=codex_present,
        require_codex=require_codex,
        carryover=carryover,
    )
    post_status(
        repo, target_sha, "success" if verdict.ok else "failure", verdict.description
    )
    print(
        f"review-gate → {'🟢' if verdict.ok else '🔴'} {verdict.description}"
        f" (PR head {head_sha[:7]} / status 先 {target_sha[:7]})"
    )
    if not advisories:
        return 0
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


def run_merge_group(repo: str) -> int:
    """merge_group イベント: 対象 PR を解決して同じ判定を merge group SHA に貼る。

    安全側の挙動 (ADR 0042): PR 番号を解決できない / PR が取れない場合は
    **failure を merge group SHA に貼る** — 静かに緑を貼ると、受け入れの無い
    PR が queue を素通りする。failure なら queue から外れて PR 側に戻るだけ。
    """
    head_ref = os.environ.get("MERGE_GROUP_HEAD_REF", "")
    mg_sha = os.environ["MERGE_GROUP_HEAD_SHA"]
    number = parse_merge_group_pr(head_ref)
    if number is None:
        post_status(
            repo,
            mg_sha,
            "failure",
            f"merge_group ref から PR を解決できない ({head_ref})"[:140],
        )
        print(f"review-gate → 🔴 merge_group ref から PR を解決できない: {head_ref}")
        return 1
    return evaluate_pr(repo, number, status_sha=mg_sha, advisories=False)


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    if sys.argv[1] == "--advisory-sweep":
        return run_advisory_sweep(repo)
    if sys.argv[1] == "--merge-group":
        return run_merge_group(repo)
    return evaluate_pr(repo, int(sys.argv[1]))


if __name__ == "__main__":
    sys.exit(main())
