#!/usr/bin/env python3
"""マージの門 (review-gate) — PR がマージ可能かを判定して commit status を貼る (ADR 0036)。

使い方:
    python3 cicd/scripts/review-gate/check.py <pr_number>    # 門の判定 + advisory + マージ執行
    python3 cicd/scripts/review-gate/check.py --advisory-sweep
      # schedule 用: open PR (base=main) を列挙して advisory とマージ執行・
      # ストール検知を適用する。status は貼らない。イベント駆動だけだと
      # 「push 直後は猶予内 → その後イベントが来ない」で advisory が一度も
      # 発火しないため (PR #238 P1 指摘)
    python3 cicd/scripts/review-gate/check.py --merge-group
      # merge_group イベント用 (ADR 0042): env MERGE_GROUP_HEAD_REF から対象 PR を
      # 解決し、同じ判定を行って **merge group の head SHA** (env MERGE_GROUP_HEAD_SHA)
      # に status を貼る (merge queue の required check はそこを見る)。
      # PR を解決できない場合は failure を貼る (安全側 — 静かに通さない)。
      # advisory もマージ執行もしない (マージは queue が実行する — ADR 0042 D4)
      要: `gh` が認証済み (Actions では GH_TOKEN を渡す) / GITHUB_REPOSITORY

仕組み:
    貼るのは commit status `review-gate` (ブランチ保護が required check として読む)。
    workflow run の緑は「評価できたこと」しか意味しない — 判定の 🟢/🔴 は status 側に出る。

条件 (全部揃うまで failure):
    1. PM の受け入れコメント — **行頭の `[pm-accept] <head SHA 先頭 7 桁以上>` 行**が
       信頼できる投稿者のコメントにある。**SHA を含める規約により push で自動的に
       無効化される** (受け入れ後に積まれた未レビューコードのマージを防ぐ)。
       判定は構文一致 (Issue #380): マーカーと SHA が本文のどこかに「含まれる」
       だけでは受け入れと数えない — 「[pm-accept] は押していません」+ 見出しの SHA
       という否定文が受け入れとして読まれた実害 (PR #379) への対処。
       例外 (ADR 0042 — pm-accept の引き継ぎ): 最新の `[pm-accept] <sha>` の <sha> から
       現 head までの追加コミットが **base (main) からのマージのみ** で、かつ
       **PR の実装差分 (base...head) が受け入れ時点と同一** なら、受け入れは現 head に
       引き継がれる。実装差分が 1 文字でも変わる push は従来どおり再受け入れが要る。
       判定は evaluate_gate → decide の共通経路にあるため、**マージ執行 (下) の
       「受け入れ済み」判定にも同じ引き継ぎが効く**
    2. レビュースレッドが全部解決している
    3. コード PR (apps/ か cicd/ に触れる) なら、**独立レビューが 1 本ある** —
       これは**常時要求**で、環境変数では切れない (Issue #400 D1。旧
       REVIEW_GATE_REQUIRE_CODEX は「上限が来たら false にする」経路として
       機能してしまい、実際に false のまま誰も気づかなかった)。
       担い手は次のどちらでもよい (ADR 0052 D7):
       (a) Codex のレビュー (login が CODEX_LOGIN_PATTERN にマッチする投稿。
           指摘ゼロの clean review は issue コメントにしか痕跡が残らないため、
           「Codex Review」ヘッダを持つ issue コメントも数える — PR #239 実測)
       (b) 代役 judge (code-reviewer subagent) のレビュー — 権限保持者が投稿した
           行頭の `<!-- standin-review -->` マーカー + 現 head SHA を含むコメント。
           **Codex と違い SHA を縛る** (代役の投稿は実装者と同じアカウントから
           出るため / has_standin_review)。代役で通した PR には degrade を可視化する
           コメントを 1 回だけ貼り、status の文言にも担い手を出す (Issue #400 D3)

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
      build-images / deploy を明示的に叩いて補償する。deploy は build (が要るなら)
      の完了 run を確認してから出す (古い image の据え置き防止)。補償は冪等
      (run 存在確認が鍵) で、失敗しても sweep が直近マージ分を打ち直す

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

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PM_ACCEPT_MARKER = "[pm-accept]"
# 受け入れ行の構文 (Issue #380): **行頭にマーカー、直後に SHA (7〜40 桁 hex)**。
# 「含む」判定 (`marker in body and sha in body`) は、マーカーに言及しただけの
# 地の文 (「[pm-accept] は押していません」) と見出しの SHA の組み合わせを
# 受け入れとして読んだ (PR #379 で実発生 / 13 秒後に success が貼られた)。
# 正典の書式 (`/merge` skill): `[pm-accept] a1b2c3d — <一言の判定理由>`。
# SHA はこのリポジトリの慣習であるバッククォート囲み (`` `a1b2c3d` ``) も受ける
# (PR #404 代役レビュー major-2: 慣習どおり書いた PM の受け入れを弾くと、
# PM と機械が「受け入れたのに赤」で食い違い続ける)。
# SHA の後ろは自由文 (判定理由) を許す — 否定文の検出はしない (言い回しは
# 無限にあるので脆い / #380 案 C の却下理由)。守るのは「マーカーの直後が
# 現 head の SHA である」ことだけで、これは偶然の文章では満たしにくい。
# **受け入れの取り消しは文章ではなくコメントの削除で行う** (PR #404 代役レビュー
# minor-4: 「[pm-accept] <sha> を取り消します」の文型は構文一致で受け入れと
# 読まれる。削除は sweep のマージ直前再評価 / reverify_and_merge が検出する)。
# **行頭 = 桁 0 (字下げ一切なし)** (Codex round 3 P1 / PR #404): リスト項目内の
# コードフェンス (`- ```text`) はフェンス検出に掛からないが、その中身は必ず
# 2 スペース以上字下げされる — 字下げを許さないことで、リスト文脈ごと
# 「言及を宣言と読む」クラスから外れる (Markdown のリスト文法を追いかけない)。
# 全角空白等の Unicode 空白開始も同時に締まる (security-reviewer info-1)。
PM_ACCEPT_LINE_RE = re.compile(r"^\[pm-accept\]\s+`?([0-9a-fA-F]{7,40})\b`?")
# 受け入れとして数えるコメントの投稿者。**このリポジトリは public なので、
# 誰でも PR にコメントできる** — 投稿者を見ないと第三者が `[pm-accept] <sha>` と
# 書くだけで門が開く (2026-08-10 の受け入れレビューで発見)。
# GitHub が付ける author_association で絞る (本文からは詐称できない)。
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
CODE_PREFIXES = ("apps/", "cicd/")
STATUS_CONTEXT = "review-gate"
# 独立レビューは**常時要求** (Issue #400 D1)。旧 REVIEW_GATE_REQUIRE_CODEX
# (repository variable) は読まない — フラグが残る限り「上限が来たら false にする」
# 経路が生き続け、実際に false のまま門がレビューを見ていなかった (2026-08-13 の
# Actions ログで実測)。担い手 (Codex / 代役 judge) の差し替えで吸収し、
# 外部サービスの生死で要求そのものを緩めない。
REQUIRE_INDEPENDENT_REVIEW = True
# 独立レビューの担い手 (Verdict.reviewer / status 文言と degrade 可視化に使う)
REVIEWER_CODEX = "Codex"
REVIEWER_STANDIN = "代役 judge"
# 代役 judge の独立レビュー (ADR 0050)。Codex が使えない間、門の「独立レビュー」
# 条件をこちらでも満たせるようにする。**Codex より条件を強くする** — 代役は
# 実装者と同じ GitHub アカウントから投稿されるため、SHA を縛らないと
# 「1 回レビューを貼ってから何でも push できる」門になる (pm-accept と同形の失効)。
STANDIN_REVIEW_MARKER = "<!-- standin-review -->"
# raw HTML のコードブロック (GitHub はこれもコード表示にする / Codex round 4 P1)。
# 開き・閉じの判定は行単位: 同一行で閉じないタグをブロック開始とみなす。
# 解除は**開始タグと同種の閉じタグのみ** (Codex round 5 P1: `<pre>` を
# `</code>` で解除すると、不一致タグを含むコメントで `<pre>` の中身が
# 通常行に漏れる)
_HTML_CODE_OPEN_RE = re.compile(r"<(pre|code)\b", re.IGNORECASE)
_HTML_CODE_CLOSE_RES = {
    "pre": re.compile(r"</pre\s*>", re.IGNORECASE),
    "code": re.compile(r"</code\s*>", re.IGNORECASE),
}
# 代役マーカーも行頭 = 桁 0 に限る (pm-accept と同じ構文厳密化 / Issue #380 と
# 同型 + Codex round 3 P1 のリスト内フェンス対策)。地の文・インラインコード
# (`<!-- standin-review -->` を貼ってください 等) の言及はレビューではない。
# rubric (review-rubric.md Part 6) の必須ヘッダはマーカーを 1 行目に字下げなしで
# 単独で置くので、正規の投稿はこの条件を常に満たす。
STANDIN_MARKER_LINE_RE = re.compile(r"^<!--\s*standin-review\s*-->")
# 代役 judge で門を通した PR に 1 回だけ貼る degrade 告知の冪等マーカー (#400 D3-2)
STANDIN_PASS_MARKER = "<!-- standin-pass-notice -->"
# 正規の告知の投稿者 (workflow の GITHUB_TOKEN)。**告知済みの判定は本文の
# マーカーだけでなく投稿者もこの login に限る** (Codex round 2 P2 / PR #404):
# public リポジトリでは第三者が不可視のマーカーだけを先に投稿でき、本文だけで
# 判定すると正規の degrade 告知が「投稿済み」扱いで永久に抑止される。
GATE_ACTIONS_LOGIN = "github-actions[bot]"
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
#
# 2026-08-12 追加の 3 つは、いずれも「特権 workflow の *中身* は敏感なのに、
# それが呼ぶスクリプトと宣言は敏感でない」という穴を塞ぐもの (PO 指摘):
#   - cicd/github/           … ブランチ保護・required check 等の**宣言**。
#                              ここが弱まると apply でリポジトリの保護が下がる
#   - cicd/scripts/github-settings/ … 管理系 API を叩き、device flow で
#                              アカウント全体 repo スコープのトークンを扱う実体。
#                              PR #347 はこの穴のため無審査で main に入った
#   - cicd/scripts/review-gate/ … マージの門そのもの。**PR が自分の門を緩められる**
#                              経路 (#331) なので、変更は必ず人の目を通す
SENSITIVE_PREFIXES = (
    "cicd/iac/",
    "cicd/github/",
    "cicd/scripts/github-settings/",
    "cicd/scripts/review-gate/",
    ".github/workflows/",
    ".github/actions/",
)
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


def should_notice_standin_pass(
    *, verdict_ok: bool, reviewer: str, marker_posted: bool
) -> bool:
    """代役 judge で門を通した PR に degrade 告知を投稿すべきか (Issue #400 D3-2)。

    「黙って代役で通る」を潰す — 代役の投稿は実装者と同じアカウントから出るため
    独立性は回復しておらず (#400 D4)、それが status の 1 行以外に痕跡なく流れると
    PO は degrade に気づけない。1 PR 1 回だけ (marker 冪等 — advisory と同じ)。
    marker_posted は standin_notice_posted (投稿者を見る) で判定して渡すこと。
    """
    return verdict_ok and reviewer == REVIEWER_STANDIN and not marker_posted


def standin_notice_posted(comments: list[tuple[str, str]]) -> bool:
    """(本文, 投稿者 login) の列に**正規の**代役通過告知が既にあるか。

    本文のマーカーだけで判定しない (Codex round 2 P2 / PR #404): public
    リポジトリでは第三者が不可視の `<!-- standin-pass-notice -->` を先に
    投稿でき、本文だけの判定だと正規の告知が「投稿済み」扱いになって
    可視の degrade 痕跡が永久に残らない。正規の告知は workflow
    (GITHUB_TOKEN = github-actions[bot]) だけが投稿するので、投稿者も縛る。
    他の advisory (再トリガー依頼等) は抑止されても合否・痕跡の保証に
    関わらないため、この縛りは告知マーカーだけに掛ける (門を重くしない)。
    """
    return any(
        STANDIN_PASS_MARKER in body and login == GATE_ACTIONS_LOGIN
        for body, login in comments
    )


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
# 補償 dispatch の再試行で遡る時間 (sweep が「最近マージされた PR」を見る幅)。
# dispatch の一過性失敗を拾い直すのが目的で、これより古い取りこぼしは
# status page (deploy watcher) と golden-path が異常として見せる
FOLLOWUP_LOOKBACK_HOURS = 24.0
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


def github_error_message(error_text: str) -> str:
    """`gh api` の失敗出力から GitHub 自身の説明文だけを抜く (純関数)。

    `gh api` は失敗時に `gh: <message> (HTTP 405)` を stderr に出し、JSON body が
    そのまま乗ることもある。**この 1 文が「なぜ弾かれたか」の唯一の一次情報**
    (「At least 1 approving review is required」「Required status check ... is
    expected」等) なので、分類のために捨ててはいけない。抜けなければ空文字。
    """
    # JSON body が乗っているならパーサに解かせる。正規表現で `"message": "..."` を
    # 拾うと、値の中のエスケープ済み引用符 (`Required status check \"foo\" is expected.`)
    # で途中で切れ、**まさに調べたい check 名が落ちる** (PR #330 Codex P2)。
    decoder = json.JSONDecoder()
    for brace in re.finditer(r"\{", error_text):
        try:
            obj, _ = decoder.raw_decode(error_text[brace.start() :])
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("message"), str):
            return " ".join(obj["message"].split())
    match = re.search(r"gh:\s*(.+?)\s*\(HTTP \d{3}\)", error_text, re.DOTALL)
    if match:
        return " ".join(match.group(1).split())
    return ""


def merge_failure_reason(error_text: str) -> str:
    """マージ API の失敗をログ向けの理由に翻訳する (純関数)。

    405/409 は「まだマージできない」だけの正常系 (他 check 未完・base 遅れ・競合)
    なので黙ってスキップする — ただし理由はログに残す (Issue #253)。

    **405 は GitHub の説明文をそのまま添える** (Issue #327): 2026-08-12 に PR #286 で
    「全 check 緑・auto-merge 武装済みなのに 405 が 3 回続き、OWNER 権限では即マージ
    できる」状態が起きた。原因の切り分けに必要な一次情報 (承認不足なのか / 保護ルール
    なのか / check 未完なのか) を、この関数が分類名に丸めて捨てていたため、
    「まだマージできない」だけが 3 回ログに残り、機構が死んでいることに気づけなかった。
    """
    if "405" in error_text:
        detail = github_error_message(error_text)
        if detail:
            return f"405: まだマージできない — GitHub の理由: {detail}"
        return "405: まだマージできない (GitHub の理由を取り出せず — 出力の形が変わった可能性)"
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
    comments の各要素は先頭 2 つが (本文, created_at) ならよい (login 付きの
    3 要素も受ける)。
    """
    posted_times = [c[1] for c in comments if marker in c[0]]
    last = latest_iso(posted_times)  # type: ignore[arg-type]
    if last is None:
        return True
    return minutes_between(last, now_iso) >= cooldown_hours * 60.0


def is_bot_login(login: str) -> bool:
    """GitHub App の投稿者か (github-actions[bot] / chatgpt-codex-connector[bot] 等)。"""
    return (login or "").endswith("[bot]")


def should_notify_human_stall(
    updated_at: str,
    comments: list[tuple[str, str, str]],
    now_iso: str,
    stall_hours: float = HUMAN_STALL_HOURS,
    cooldown_hours: float = RENOTIFY_COOLDOWN_HOURS,
) -> tuple[bool, str]:
    """needs-human Issue の停滞通知を出すべきか (出すか, 理由)。

    comments は (本文, created_at, 投稿者 login) の列。

    停滞時計から**自分の通知コメントを除外する**のが要 (Codex P2 / PR #258):
    初回通知の投稿自体が Issue の updated_at を現在へ進めるため、素朴に
    updated_at で 48h を測ると次回以降の sweep で必ず「停滞していない」に
    倒れ、本文で約束している 24h 後の再通知が永久に起きない。判定を分岐する:

    - マーカー無し (未通知): updated_at で 48h 停滞を判定 (従来どおり)
    - マーカー有り: updated_at をそのまま停滞時計にはしない。
      最後の通知から cooldown_hours (24h) 経過していて、かつ通知の後に
      **人間の反応が無い**なら再通知する。人間の反応があれば時計はそこから
      測り直し、反応からさらに stall_hours 停滞したら再通知する (反応 1 回で
      永久に沈黙しない)。人間の反応と数えるのは:
        a. bot 以外のコメント (自分の通知・Codex 等の bot コメントは数えない —
           自分の通知を反応と誤認すると再通知が永久に止まる)
        b. **コメントに現れない Issue 本体の更新** (本文・タイトル編集等 —
           Codex P2 追指摘 / PR #258)。updated_at が既知の活動 (全コメント +
           通知) の最新より**厳密に後 (> 0 秒) なら人間の更新**として
           updated_at を反応時刻に使う。**コメント投稿は issue の updated_at を
           created_at と秒まで一致させる** (2026-08-11 実測: #262 #253 で
           完全一致 / 逆にコメント 0 件の #254 は本文編集で 20 分進んでいた)
           ため、固定マージン無しの厳密比較で汚染と編集を切り分けられる。
           万一 production で汚染が数秒ずれても、誤分類は「人間活動と誤認 →
           再通知が 24h でなく 48h 後になる」方向で、通知の目的 (届くこと) に
           対して軽微 — 通知が消えることはない。
           残る穴: 人間の編集の**後**に bot コメントが付くと updated_at が
           bot コメントで説明でき、編集が見えない (body 編集は REST timeline に
           出ず、GraphQL userContentEdits が要る — 導入は PO 判断待ち)
    """
    last_marker = latest_iso(
        [t for body, t, _login in comments if HUMAN_STALL_MARKER in body]
    )
    if last_marker is None:
        if is_stale(updated_at, now_iso, stall_hours):
            return (True, f"{stall_hours:.0f}h 停滞 (初回通知)")
        return (False, "停滞していない")
    if not is_stale(last_marker, now_iso, cooldown_hours):
        return (False, f"前回通知から {cooldown_hours:.0f}h 未満 (クールダウン)")
    human_times = [
        t
        for body, t, login in comments
        if HUMAN_STALL_MARKER not in body
        and not is_bot_login(login)
        and minutes_between(last_marker, t) > 0
    ]
    known = latest_iso([t for _body, t, _login in comments] + [last_marker])
    if known is not None and minutes_between(known, updated_at) > 0:
        human_times.append(updated_at)
    last_human = latest_iso(human_times)  # type: ignore[arg-type]
    if last_human is None:
        return (
            True,
            f"前回通知から {cooldown_hours:.0f}h 経過・人間の反応なし (再通知)",
        )
    if is_stale(last_human, now_iso, stall_hours):
        return (True, f"人間の反応の後さらに {stall_hours:.0f}h 停滞 (再通知)")
    return (False, "通知後に人間の反応あり")


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


def runs_since(runs: list[dict], since_iso: str) -> list[dict]:
    """main の workflow run のうち since_iso 以降に作成されたものを返す。

    補償 dispatch の冪等キー: 「merged_at 以降に run がある = その merge の
    コードを含む tip で起動済み (push 経路 / 手動 / 過去の補償のいずれでも)」。
    run は常に main の tip から走るため、後続 merge の run でもこの merge を
    含んでいる — head_sha の一致は要求しない。
    """
    return [
        r
        for r in runs
        if r.get("head_branch") == "main"
        and r.get("created_at")
        and minutes_between(since_iso, r["created_at"]) >= 0
    ]


def earliest_iso(timestamps: list[str | None]) -> str | None:
    """ISO 8601 時刻列の最古を返す (None / 空文字は無視)。"""
    present = [t for t in timestamps if t]
    if not present:
        return None
    return min(present, key=lambda t: datetime.fromisoformat(t.replace("Z", "+00:00")))


def deploy_gate_anchor(
    merged_at: str, needs_build: bool, build_runs_since_merge: list[dict]
) -> tuple[str | None, str]:
    """deploy 済み判定の基準時刻 (anchor, 状態説明) を返す。None = まだ出さない。

    - image 変更なしの PR: anchor = merged_at (マージ以降の deploy run が補償)
    - image 変更ありの PR: anchor = **対象 build の完了時刻** (merged_at 以降に
      completed になった最初の build run の updated_at)。merged_at 基準にすると、
      並行マージで「A の build 進行中に image 変更なしの B が deploy を出す」とき
      B の deploy (古い image) を A の補償と誤認し、**A の build 完了後の deploy が
      永久に出ない** (Codex P1 再指摘 / PR #258)。build と deploy の順序を
      anchor で直接結び付ける。
    - build が未起動 / 進行中なら anchor 無し — deploy を出すと deploy.yml の
      IMAGE_TAG 解決が「当該 SHA の run 無し → 直近の成功 run」に落ち、古い
      image を正常デプロイして smoke も通ってしまう (静かな劣化)。
      completed の conclusion は問わない (push 経路と同じ: build 失敗時の deploy は
      「直近の成功 image で続行 + warning」が deploy.yml の既定)。
    """
    if not needs_build:
        return (merged_at, "image 変更なし (merged_at 基準)")
    if not build_runs_since_merge:
        return (None, "build run が未起動")
    completed_at = earliest_iso(
        [
            # updated_at ≈ 完了時刻 (無ければ created_at で近似 — 遅い側に倒れ
            # ないよう欠落時も run 自体は completed であることを status で確認済み)
            r.get("updated_at") or r.get("created_at")
            for r in build_runs_since_merge
            if r.get("status") == "completed"
        ]
    )
    if completed_at is None:
        return (None, "build 進行中")
    return (completed_at, "build 完了")


def recently_merged(
    prs: list[dict], now_iso: str, lookback_hours: float = FOLLOWUP_LOOKBACK_HOURS
) -> list[dict]:
    """closed PR のうち lookback 内にマージされたものを返す (補償の再試行対象)。

    merged_at が無い closed PR (クローズのみ) は対象外。マージ実行者は
    問わない — 人間 / auto-merge のマージは push 経路の run が既に存在し、
    冪等チェック (runs_since) が no-op にする。
    """
    return [
        pr
        for pr in prs
        if pr.get("merged_at")
        and not is_stale(pr["merged_at"], now_iso, lookback_hours)
    ]


@dataclass
class Verdict:
    ok: bool
    missing: list[str] = field(default_factory=list)
    # 緑の内訳 (受け入れの由来・独立レビューの担い手)。**固定文言を使わない** —
    # 「OK: 受け入れ・スレッド・レビューが揃った」の固定文は、レビューを一度も
    # 見ていない PR にも同じ文が出て嘘になった (Issue #400 P1 / PR #338 実測)。
    note: str = ""
    # 独立レビューの担い手 (REVIEWER_CODEX / REVIEWER_STANDIN / 空 = 要求対象外)。
    # 代役で通した PR の degrade 可視化コメント (#400 D3-2) がここを読む
    reviewer: str = ""

    @property
    def description(self) -> str:
        if self.ok:
            # note は decide() が必ず組み立てる。空なら「何で通ったか不明」であり、
            # それを「揃った」と書かない (取れなかったものを異常なしと書かない)
            text = f"OK: {self.note}" if self.note else "OK (判定内訳なし)"
        else:
            text = " / ".join(self.missing)
        return text[:137] + "…" if len(text) > 140 else text


@dataclass
class GateEval:
    """門の評価一式 (イベント経路 main() と sweep のマージ直前再評価で共用)。"""

    verdict: Verdict
    changed_paths: list[str]
    comment_pairs: list[tuple[str, str]]  # (本文, author_association)
    code_pr: bool
    codex_present: bool
    # 正規の代役通過告知 (github-actions[bot] 投稿) が既にあるか (#400 D3-2 /
    # Codex round 2 P2 — 本文マーカーだけでなく投稿者で判定した結果)
    standin_notice_posted: bool = False


def is_code_pr(changed_paths: list[str]) -> bool:
    return any(p.startswith(CODE_PREFIXES) for p in changed_paths)


def _machine_lines(body: str) -> list[str]:
    """機械判定の対象になる行だけを返す (引用とコードフェンスを落とす)。

    落とす理由はどちらも「本人が書いた宣言ではないものを宣言として読まない」:

    - **引用行 (`>` 始まり)**: マーカーが HTML コメント = **画面に見えない**ため、
      第三者が不可視の `<!-- standin-review --> <sha>` を投稿し、権限保持者が
      GitHub の "Quote reply" で返信すると、raw markdown がそのまま `> ` 付きで
      複製されて **association が NONE → OWNER に化け、誰もレビューしていないのに
      門が開く** (security-reviewer が実測: 第三者単体は False → OWNER の
      引用返信で True)。
    - **コードブロックの中**: 書式の説明で rubric や skill の例文
      (`[pm-accept] <sha>` / 必須ヘッダ) をそのまま貼ると、例文が受け入れ・
      レビューとして読まれる (#380 と同型の「言及を宣言と読む」誤爆)。
      GitHub Markdown でコードになる 3 形式すべてを外す (Codex P1 / PR #404 —
      ``` フェンスだけ外しても ~~~ フェンスとインデントコードが残る):
      ``` フェンス / ~~~ フェンス / 行頭 4 スペース以上・タブのインデントコード。
      フェンスの閉じは**開いたのと同じ文字・同数以上・後続が空白のみ**
      (CommonMark §fenced code blocks / PR #404 代役レビュー major-1 と
      Codex round 2 P1: 文字だけ見ると ```` 包みの中の ``` 行が、長さまで見ても
      ``` の中の ```python 行 (info string 付き — CommonMark では閉じにならない)
      が閉じ扱いになり、以降のブロック内容が判定対象に漏れる)。
      ``` の中の ~~~ 行も中身。
      インデントコードの厳密な文法 (直前に空行が要る等) は追わず、4 スペース
      以上は一律で外す — 過剰除外は門が閉じる側 (安全側) にしか倒れない
      (正典の書式はどちらも行頭から書く)。
      **リスト項目内のフェンス** (`- ```text`) はここでは検出しない (Codex
      round 3 P1) — 対策はマーカー正規表現側の「桁 0 のみ」で持つ: リスト内
      コードの中身は必ず 2 スペース以上字下げされるため、字下げを許さない
      マーカー判定には掛からない (Markdown のリスト文法をここで追いかけない)。
      **raw HTML のコードブロック** (`<pre>` / `<code>` の複数行ブロック) も
      外す (Codex round 4 P1 — GitHub はこれもコード表示にするため、
      `<pre>` 包みの書式例が宣言として読まれる)。同一行で閉じるインライン
      span (`<code>…</code>`) はブロックにしない — どのみち行頭が `<` なので
      桁 0 のマーカー判定には掛からない。閉じタグの無いブロックは
      コメント末尾まで除外 (過剰除外 = 門が閉じる安全側)。
    """
    lines: list[str] = []
    fence: str | None = None  # 開いているフェンスの文字 ("`" か "~")。None = 外
    fence_len = 0  # 開きフェンスの文字数 (閉じは同数以上が要る)
    # <pre> / <code> の複数行ブロックの開始タグ名。None = ブロック外。
    # 解除は同種の閉じタグのみ (Codex round 5 P1 — 不一致タグで漏らさない)
    in_html_code: str | None = None
    for line in body.splitlines():
        stripped = line.lstrip()
        if in_html_code is not None:
            if _HTML_CODE_CLOSE_RES[in_html_code].search(line):
                in_html_code = None
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            char = stripped[0]
            run = len(stripped) - len(stripped.lstrip(char))
            rest = stripped[run:]
            if fence is None:
                # 開きは info string (```python 等) を許す (CommonMark)
                fence, fence_len = char, run
            elif char == fence and run >= fence_len and not rest.strip():
                # 閉じは同じ文字・同数以上・後続が空白のみ。info string 付きの
                # 同文字行は閉じではなくブロックの中身 (Codex round 2 P1)
                fence, fence_len = None, 0
            continue
        if fence is not None or stripped.startswith(">"):
            continue
        opened = _HTML_CODE_OPEN_RE.search(line)
        if opened:
            tag = opened.group(1).lower()
            if not _HTML_CODE_CLOSE_RES[tag].search(line, opened.end()):
                # 同じ行で同種タグが閉じない <pre> / <code> — ブロック開始
                # (この行ごと除外)。同一行で閉じるインライン span は通常行だが、
                # 行頭が `<` なので桁 0 のマーカー判定には掛からない
                in_html_code = tag
                continue
        if line.startswith("\t") or line.startswith("    "):
            # インデントコード (4 スペース / タブ) — 書式例の貼り付け (Codex P1)
            continue
        lines.append(line)
    return lines


def _pm_accept_tokens(body: str) -> list[str]:
    """本文中の**構文的に正しい受け入れ行**から SHA トークンを取り出す (Issue #380)。

    受け入れ行 = 行頭 `[pm-accept]` + 直後に hex 7〜40 桁 (PM_ACCEPT_LINE_RE)。
    地の文でのマーカー言及 (「[pm-accept] は押していません」/ 行の途中の
    `[pm-accept]`) は受け入れ行ではないので拾わない。小文字化して返す。
    """
    tokens: list[str] = []
    for line in _machine_lines(body):
        m = PM_ACCEPT_LINE_RE.match(line)
        if m:
            tokens.append(m.group(1).lower())
    return tokens


def has_pm_accept(comments: list[tuple[str, str]], head_sha: str) -> bool:
    """受け入れと数える条件は 3 つ全部。

    1. **行頭の受け入れ行** `[pm-accept] <sha>` がある (構文一致 / Issue #380 —
       マーカーと SHA が本文の別々の場所に「含まれる」だけでは数えない)
    2. その SHA が **いまの** head SHA の先頭一致 (7 桁以上) — push で自動失効させるため
    3. **投稿者がこのリポジトリの権限保持者** (author_association) — public リポジトリで
       第三者がコメントするだけで門が開くのを塞ぐ

    comments は (本文, author_association) の列。
    """
    head = head_sha.lower()
    return any(
        (association or "").upper() in TRUSTED_ASSOCIATIONS
        and any(head.startswith(token) for token in _pm_accept_tokens(body))
        for body, association in comments
    )


def has_standin_review(
    comments: list[tuple[str, str]],
    head_sha: str,
    carried_short: str | None = None,
) -> bool:
    """代役 judge の独立レビューが**現 head に対して**投稿されているか (ADR 0052 D7)。

    数える条件は 3 つ全部 — has_pm_accept と同形にしている:

    1. **行頭の**マーカー `<!-- standin-review -->` 行がある
    2. **いまの** head SHA (先頭 7 桁) を含む — push で自動失効させるため
    3. **投稿者がこのリポジトリの権限保持者** (author_association)

    Codex の判定 (codex_present_in) は SHA を見ない。**代役だけ条件を強くするのは
    非対称だが意図的**で、理由は投稿者が誰かにある: Codex は別アカウント
    (chatgpt-codex-connector[bot]) なので実装者は自分でレビューを貼れないが、
    代役の投稿はレビュー対象を書いた本人と同じアカウントから出る。SHA を縛らないと
    「1 回レビューを貼ってから、以後は何を push しても門が開いたまま」になる。

    マーカーは**行頭にある行だけ**を数え、引用・コードフェンス内の言及は数えない
    (_machine_lines / STANDIN_MARKER_LINE_RE — #380 と同型の構文厳密化。rubric の
    必須ヘッダはマーカーを 1 行目に単独で置くので、正規の投稿は常に満たす)。
    SHA は本文中の hex トークン (7 桁以上) が現 head の先頭 7 桁から始まるかで見る —
    rubric の書式は SHA をマーカーと別の行 (`**代役レビュー (`sha`)**`) に置くため、
    マーカー行そのものには縛らない。
    """
    accepted = {head_sha[:SHORT_SHA_LEN].lower()}
    # pm-accept と同じ引き継ぎを効かせる (ADR 0042 / PR #330 の代役レビュー指摘)。
    # 引き継ぎが成立している = 「実装差分が不変の base 追随」なので、レビュー対象の
    # コードは 1 文字も変わっていない。ここを揃えないと「PM 受け入れは carryover で
    # 生き残るのに独立レビューだけ失効する」非対称が出て、main を追随するたびに
    # 門が赤へ戻る (実測: 両 PR を 3-way マージした tree で再現)。
    if carried_short:
        accepted.add(carried_short.lower())
    for body, association in comments:
        if (association or "").upper() not in TRUSTED_ASSOCIATIONS:
            continue
        lines = _machine_lines(body)
        if not any(STANDIN_MARKER_LINE_RE.match(line) for line in lines):
            continue
        tokens = [t.lower() for t in HEX_TOKEN_RE.findall("\n".join(lines))]
        if any(t.startswith(short) for t in tokens for short in accepted):
            return True
    return False


def latest_pm_accept_token(comments: list[tuple[str, str]]) -> str | None:
    """最新の信頼できる pm-accept コメントから受け入れ SHA トークンを取る (ADR 0042)。

    comments は API 取得順 (created_at 昇順) の (本文, author_association)。
    末尾から走査し、**構文的に正しい受け入れ行** (_pm_accept_tokens / Issue #380)
    を持つ最初の信頼できるコメントの SHA を小文字で返す。
    **最新の受け入れだけを見る** — 古い受け入れへ遡って引き継がない
    (PM が受け入れをやり直したら新しい方が意思)。

    行頭にマーカーがあるのに受け入れ行として成立していないコメント
    (SHA 書き忘れ / 「[pm-accept] は押していません」等の行頭宣言) は
    **None = 引き継ぎ判定不能**で止める — 古い受け入れへ黙って遡ると、
    PM の最新の意思 (やり直し・保留) を機械が上書きする。行の途中や
    引用・コードフェンス内の言及 (地の文) は受け入れの意思と無関係なので
    素通しして、さらに古いコメントを見に行く。
    """
    for body, association in reversed(comments):
        if (association or "").upper() not in TRUSTED_ASSOCIATIONS:
            continue
        tokens = _pm_accept_tokens(body)
        if tokens:
            return tokens[-1]
        if any(
            # 桁 0 のみ (PM_ACCEPT_LINE_RE と同じ基準 — 字下げされたマーカーは
            # リスト内コード等の言及であり、受け入れの意思表示ではない)
            line.startswith(PM_ACCEPT_MARKER)
            for line in _machine_lines(body)
        ):
            # 行頭マーカーだが SHA が続かない — 受け入れの試み (書き忘れ) か
            # 明示の保留。どちらでも「最新の意思が受け入れでない」ので引き継がない
            return None
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
    require_independent_review: bool,
    carryover: Carryover | None = None,
) -> Verdict:
    missing: list[str] = []
    # 緑のときの内訳 (Issue #400 D3-1)。固定文言は「レビューを見ていないのに
    # 揃ったと書く」嘘の源だった (PR #338 実測) — 何で通ったかを毎回組み立てる
    notes: list[str] = []
    if has_pm_accept(comments, head_sha):
        notes.append("受け入れ済み")
    elif carryover is not None and carryover.ok:
        # ADR 0042: 実装差分が不変の main 追随なら受け入れを引き継ぐ
        notes.append(f"pm-accept を {carryover.accepted_short} から引き継ぎ (差分不変)")
    else:
        item = f"PM 受け入れ ([pm-accept] + {head_sha[:SHORT_SHA_LEN]}) が無い"
        if carryover is not None and carryover.detail:
            item += f" (引き継ぎ不成立: {carryover.detail})"
        missing.append(item)
    if unresolved_threads > 0:
        missing.append(f"未解決スレッド {unresolved_threads} 件")
    # 門が要求するのは「独立レビューが 1 本あること」で、担い手は Codex でも
    # 代役 judge でもよい (ADR 0052 D7)。Codex が上限で黙っている間、門を開ける
    # (要求を止める) のではなく担い手を差し替えることで、
    # 「有限資源の都合で門が開く」前例を作らずに済む。
    # require_independent_review は本番では常に True (REQUIRE_INDEPENDENT_REVIEW /
    # Issue #400 D1) — 引数として残すのはテストが条件分岐を突けるようにするため。
    reviewer = ""
    if require_independent_review and is_code_pr(changed_paths):
        carried = carryover.accepted_short if (carryover and carryover.ok) else None
        if codex_present:
            reviewer = REVIEWER_CODEX
            notes.append(f"独立レビュー: {REVIEWER_CODEX}")
        elif has_standin_review(comments, head_sha, carried):
            reviewer = REVIEWER_STANDIN
            # degrade を status の文言から読めるようにする (#400 D3-1)
            notes.append(f"独立レビュー: {REVIEWER_STANDIN} (独立性は未回復)")
        else:
            missing.append("独立レビューが無い (コード PR)")
    elif require_independent_review:
        notes.append("コード PR でない (独立レビュー対象外)")
    else:
        # 本番では通らない分岐だが、通った場合に「揃った」と書かない
        notes.append("独立レビュー未評価")
    return Verdict(
        ok=not missing, missing=missing, note=" / ".join(notes), reviewer=reviewer
    )


# ---- ここから下は GitHub との入出力 (テスト対象は上の純粋ロジック) ----


def parse_gh_json(text: str) -> object:
    """gh の stdout を JSON として読む (`gh api --paginate` 対応の純関数)。

    --paginate は複数ページのとき**トップレベルの JSON 値を連続出力**する
    (`[...][...]` — ページごとに 1 配列)。素朴な json.loads は 2 ページ目の
    先頭で `Extra data` になり、コメント 100 件超の Issue が 1 つあるだけで
    schedule sweep 全体が毎回中断する (Codex P2-a / PR #258)。連続する JSON 値を
    raw_decode で全部読み、**すべて配列なら 1 本に連結**して返す (このリポジトリの
    --paginate は配列を返す endpoint のみ)。配列以外が混ざる複数値は黙って
    間違った形で返さず ValueError にする。
    """
    text = text.strip()
    if not text:
        return {}
    decoder = json.JSONDecoder()
    values: list[object] = []
    idx = 0
    while idx < len(text):
        value, end = decoder.raw_decode(text, idx)
        values.append(value)
        idx = end
        while idx < len(text) and text[idx] in " \t\r\n":
            idx += 1
    if len(values) == 1:
        return values[0]
    if all(isinstance(v, list) for v in values):
        return [item for page in values for item in page]  # type: ignore[union-attr]
    raise ValueError(
        "gh の出力に配列以外の JSON 値が複数含まれる — "
        "--paginate はこのコードでは配列 endpoint 専用 (object を返す endpoint に付けない)"
    )


def gh(*args: str) -> object:
    out = subprocess.run(
        ["gh", *args], capture_output=True, text=True, timeout=60, check=True
    )
    return parse_gh_json(out.stdout)


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


def maybe_post_standin_notice(
    repo: str, number: int, verdict: Verdict, notice_posted: bool
) -> None:
    """代役 judge で門を通した PR に degrade 告知を 1 回だけ貼る (Issue #400 D3-2)。

    呼び出しは**マージを実行しうる全経路のマージより前** — イベント経路
    (evaluate_pr) と sweep の再評価経路 (reverify_and_merge) の両方 (Codex P2 /
    PR #404: イベント経路の投稿が一過性で失敗すると、次の sweep が告知なしで
    マージし「代役で通った痕跡」が永久に残らない)。投稿失敗の例外はここで
    握らない — 呼び出し元 (sweep の隔離 / evaluate_pr の run 赤) が失敗として
    数え、マージは次の周回に譲られる (痕跡なしのマージより遅いマージを取る)。

    notice_posted は standin_notice_posted (投稿者 = github-actions[bot] に
    限った判定 / Codex round 2 P2) の結果。投稿直前の再フェッチ確認も同じ
    投稿者縛りで行う — 本文だけの確認 (post_advisory_once) を使うと、第三者の
    不可視マーカーが正規の告知を「投稿済み」に見せかけて抑止できる。
    """
    if not should_notice_standin_pass(
        verdict_ok=verdict.ok,
        reviewer=verdict.reviewer,
        marker_posted=notice_posted,
    ):
        return
    # 投稿直前の再フェッチ確認 (2 重投稿レースの防御 b — post_advisory_once と
    # 同じ規律を、投稿者を見る判定で行う)
    fresh = fetch_comments_with_times(repo, number)
    if standin_notice_posted([(body, login) for body, _t, login in fresh]):
        print("advisory: 再確認で正規の standin-pass-notice を検出 — 投稿しない")
        return
    post_comment(
        repo,
        number,
        f"{STANDIN_PASS_MARKER}\n"
        "🟡 **この PR の独立レビュー条件は代役 judge (standin review) で"
        "満たされました。** 代役の投稿は実装者と同じアカウントから出るため、"
        "Codex のような別アカウントによる構造的な独立性は回復していません"
        " (Issue #400 D3 / D4)。担い手は review-gate status の文言にも出ます。",
    )
    print("advisory: 代役 judge で通過 — degrade 告知を投稿した (#400 D3-2)")


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


def fetch_comments_with_times(repo: str, number: int) -> list[tuple[str, str, str]]:
    """(本文, created_at, 投稿者 login) の列。時限系通知のクールダウン判定と
    人間の反応検出 (should_notify_again / should_notify_human_stall) 用。"""
    comments = gh("api", f"repos/{repo}/issues/{number}/comments", "--paginate")
    return [
        (
            (c.get("body") or ""),  # type: ignore[union-attr]
            (c.get("created_at") or ""),  # type: ignore[union-attr]
            ((c.get("user") or {}).get("login", "")),  # type: ignore[union-attr]
        )
        for c in comments
    ]


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


def evaluate_gate(
    repo: str, number: int, head_sha: str, pr: dict | None = None
) -> GateEval:
    """門の 3 条件 (pm-accept + head SHA / スレッド解決 / 独立レビュー) を現在の
    API 状態から評価する。イベント経路 (main) と sweep のマージ直前再評価 (Codex
    P1 / PR #258) の共通実装 — 判定材料の取り方を 1 箇所にする。

    pm-accept の引き継ぎ判定 (ADR 0042) もここに置く — **マージ執行の経路
    (reverify_and_merge) も同じ関数を通る**ので、「追随しただけの PR」が
    マージ直前の再評価で門を閉じられることがない。pr は base ref / head を
    見るために使う (省略時は取得する)。
    """
    owner, name = repo.split("/")
    files = gh("api", f"repos/{repo}/pulls/{number}/files", "--paginate")
    changed_paths = [f["filename"] for f in files]  # type: ignore[union-attr]
    comments = gh("api", f"repos/{repo}/issues/{number}/comments", "--paginate")
    unresolved = fetch_unresolved_threads(owner, name, number)
    # 独立レビューは常時要求 (Issue #400 D1)。旧 REVIEW_GATE_REQUIRE_CODEX
    # (repository variable) はもう読まない — 変数の消し忘れ・typo が「静かに門が
    # 緩む」側に倒れる設計だった上、実測で false のまま門がレビューを見ていなかった。
    # 変数自体の削除は PO の web UI 作業だが、コードが読まなければ害は無い
    code_pr = is_code_pr(changed_paths)
    # 合否だけでなく自動再トリガー (ADR 0038) もコード PR で
    # Codex の既着を見るので、コード PR なら常に取得する
    codex_present = (
        fetch_codex_present(
            repo, number, os.environ.get("CODEX_LOGIN_PATTERN", "codex")
        )
        if code_pr
        else False
    )
    comment_pairs = [
        (c.get("body") or "", c.get("author_association") or "")  # type: ignore[union-attr]
        for c in comments
    ]
    # 直接の受け入れが無いときだけ引き継ぎ (ADR 0042) を試す — API 節約
    carryover = None
    if not has_pm_accept(comment_pairs, head_sha):
        if pr is None:
            pr = gh("api", f"repos/{repo}/pulls/{number}")  # type: ignore[assignment]
        carryover = compute_carryover(repo, pr, comment_pairs)  # type: ignore[arg-type]
    verdict = decide(
        head_sha=head_sha,
        changed_paths=changed_paths,
        comments=comment_pairs,
        unresolved_threads=unresolved,
        codex_present=codex_present,
        require_independent_review=REQUIRE_INDEPENDENT_REVIEW,
        carryover=carryover,
    )
    return GateEval(
        verdict=verdict,
        changed_paths=changed_paths,
        comment_pairs=comment_pairs,
        code_pr=code_pr,
        codex_present=codex_present,
        standin_notice_posted=standin_notice_posted(
            [
                (
                    (c.get("body") or ""),  # type: ignore[union-attr]
                    ((c.get("user") or {}).get("login", "")),  # type: ignore[union-attr]
                )
                for c in comments
            ]
        ),
    )


def reverify_and_merge(repo: str, number: int, head_sha: str) -> bool:
    """sweep 経路のマージ試行 (Codex P1 / PR #258)。マージできたら True。

    sweep は**保存済みの success status を信じない** — status を貼ってから
    マージまでの間に `[pm-accept]` コメントの削除・スレッドの unresolve が
    起きても、issue_comment.deleted 等は購読外で status は緑のまま残るため、
    そのまま try_merge すると受け入れの無い PR がマージされる。門の 3 条件を
    その場でフル再評価し、崩れていれば **failure を貼り直して** (緑のまま
    残すと同じ穴が次の sweep でも開く) マージしない。
    """
    ev = evaluate_gate(repo, number, head_sha)
    if not ev.verdict.ok:
        post_status(repo, head_sha, "failure", ev.verdict.description)
        print(
            f"#{number}: マージ直前の再評価で門が閉じた — {ev.verdict.description}"
            " (保存済み success を failure に訂正)"
        )
        return False
    # 代役で通っているなら告知をマージより先に (Codex P2 / PR #404): イベント
    # 経路の投稿が失敗していた場合、ここが最後の砦。投稿失敗は例外で上へ —
    # sweep_one_pr の隔離が失敗として数え、マージは次の周回 (≤30 分) に譲る
    maybe_post_standin_notice(repo, number, ev.verdict, ev.standin_notice_posted)
    merged, _ = try_merge(repo, number, head_sha)
    if merged:
        # 補償の基準時刻は**マージ成功の後**に取る (Codex P1 / PR #258): 再評価の
        # API 数往復の間に**別 PR** の build run が開始されると、マージ前に取った
        # 時刻では runs_since がその run (この PR の変更を含まない) を「補償済み」と
        # 誤認し、この PR の image build が永久に dispatch されない。
        # イベント経路 (maybe_execute_merge) と同じ「マージ直後の now」で統一
        ensure_merge_followup(
            repo, datetime.now(timezone.utc).isoformat(), ev.changed_paths
        )
    return merged


def try_merge(repo: str, number: int, head_sha: str) -> tuple[bool, str]:
    """squash マージ API を叩く。(成功したか, 失敗理由) を返す。

    **投稿直前の再フェッチ確認と同じ規律をマージにも適用する** (Codex P1 / PR #258):
    呼び出し側の判定は run 冒頭のスナップショットで、コメント・レビューの取得を
    挟むうちに PM が auto-merge を解除 / needs-human を付けたかもしれない。
    merge API が強制するのは sha= (head 変更で 409) だけで、auto-merge /
    needs-human という本 workflow 固有の保留条件は強制しない — ここで PR を
    取り直して should_execute_merge を再評価し、通らなければマージしない。
    再取得に失敗したときもマージしない (取れなかったものを「合格」と書かない)。
    405/409 等は正常系のスキップとしてログに理由を出すだけで run は落とさない。
    """
    try:
        fresh = gh("api", f"repos/{repo}/pulls/{number}")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        reason = f"直前再確認の PR 再取得に失敗 — マージしない ({type(e).__name__})"
        print(f"#{number}: {reason}")
        return (False, reason)
    ok, hold_reason = should_execute_merge(fresh)  # type: ignore[arg-type]
    if not ok:
        reason = f"直前再確認で保留 — {hold_reason}"
        print(f"#{number}: {reason}")
        return (False, reason)
    if ((fresh.get("head") or {}).get("sha")) != head_sha:  # type: ignore[union-attr]
        reason = "直前再確認で head が動いていた — 次のイベント / sweep が再評価する"
        print(f"#{number}: {reason}")
        return (False, reason)
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


def fetch_workflow_runs(repo: str, workflow: str) -> list[dict]:
    """workflow の直近 run 一覧 (新しい順)。補償 dispatch の冪等チェック用。"""
    payload = gh("api", f"repos/{repo}/actions/workflows/{workflow}/runs?per_page=30")
    return payload.get("workflow_runs") or []  # type: ignore[union-attr]


def ensure_merge_followup(repo: str, merged_at: str, changed_paths: list[str]) -> None:
    """GITHUB_TOKEN のマージ push は push トリガー workflow を起動しない —
    まさに #253 の原因と同じ連鎖抑止が、今度はマージの下流 (build-images /
    deploy) で起きる。workflow_dispatch は GITHUB_TOKEN でも起動できる
    (公式の例外) ので、push トリガー相当を明示的に叩いて補償する。

    **冪等 (何度呼んでも安全)**: dispatch の前に「merged_at 以降の run の存在」を
    確認する — マージ直後とその後の sweep (sweep_merged_followups) が同じ経路を
    通り、一過性の dispatch 失敗は最悪 30 分後の sweep が打ち直す (Codex P2)。

    - build-images: PR が image のソースに触れたときだけ (paths の写像)
    - deploy: **build (が要るなら) の完了 run を確認してから** dispatch する
      (Codex P1)。build 前に出すと deploy.yml の IMAGE_TAG 解決が「当該 SHA の
      run 無し → 直近の成功 run」に落ち、古い image を正常デプロイして smoke も
      通ってしまう。完了待ちの poll はしない — runner を数十分保持する代わりに
      次の sweep (≤30 分後) に譲る (マージの下限保証と同じ粒度)
    - deploy 済み判定の基準は deploy_gate_anchor — image 変更ありの PR は
      「**build 完了時刻より後**に作成された deploy run」だけを補償済みと数える
      (並行マージで build 進行中に出た他 PR の deploy を誤認しない — Codex P1 再指摘)
    - deploy は AUTO_DEPLOY_ENABLED=true のときだけ (push 経路と同じゲートを尊重。
      dispatch の action=up は本来ゲート対象外なので、ここで見ないと未解禁のまま
      公開 URL へ自動デプロイしてしまう)
    """
    needs_build = needs_image_build(changed_paths)
    build_runs: list[dict] = []
    if needs_build:
        build_runs = runs_since(
            fetch_workflow_runs(repo, "build-images.yml"), merged_at
        )
        if not build_runs:
            dispatch_workflow(repo, "build-images.yml")
            print("deploy は build 完了後の sweep が出す (古い image の据え置き防止)")
            return
    anchor, state = deploy_gate_anchor(merged_at, needs_build, build_runs)
    if anchor is None:
        print(f"{state} — deploy は次の sweep に譲る")
        return
    if (os.environ.get("AUTO_DEPLOY_ENABLED", "") or "").lower() != "true":
        print(
            "AUTO_DEPLOY_ENABLED != true — deploy の dispatch はしない (push 経路と同じゲート)"
        )
        return
    if runs_since(fetch_workflow_runs(repo, "deploy.yml"), anchor):
        print(f"deploy run が既にある ({state} 後) — 打ち直さない (冪等)")
        return
    dispatch_workflow(
        repo, "deploy.yml", {"action": "up", "environment": "dev", "voicevox": "cpu"}
    )


def page_exhausts_lookback(prs: list[dict], now_iso: str) -> bool:
    """更新順 (desc) のページ全体が lookback より古いか (以降のページを読む必要なし)。

    merged_at <= updated_at なので、ページ内の全 PR の updated_at が lookback より
    古ければ、以降のページに lookback 内のマージ済み PR は存在しない (純関数)。
    updated_at が欠けた PR は「古い」と断定できないため False (読み続ける側に倒す)。
    """
    return bool(prs) and all(
        pr.get("updated_at")
        and is_stale(pr["updated_at"], now_iso, FOLLOWUP_LOOKBACK_HOURS)
        for pr in prs
    )


def fetch_recent_closed_prs(repo: str, now_iso: str) -> list[dict]:
    """closed PR (base=main) を更新順に、lookback を出るまでページングして集める。

    1 ページ固定だと 24h に 30 件超の close や古い closed PR の更新 (コメント等)
    で lookback 内のマージ済み PR がページ外へ押し出され、失敗した補償が永久に
    残る (Codex P2 / PR #258)。かといって全ページ走査は closed PR の全履歴に
    比例して 30 分毎に効いてくるため、「ページ全体が lookback より古くなったら
    打ち切る」(page_exhausts_lookback — merged_at <= updated_at による安全な
    早期終了)。安全弁として最大 10 ページ (1000 件)。
    """
    collected: list[dict] = []
    for page in range(1, 11):
        batch = gh(
            "api",
            f"repos/{repo}/pulls?state=closed&base=main&sort=updated"
            f"&direction=desc&per_page=100&page={page}",
        )
        if not batch:
            break
        collected.extend(batch)  # type: ignore[arg-type]
        if page_exhausts_lookback(batch, now_iso) or len(batch) < 100:  # type: ignore[arg-type]
            break
    return collected


def sweep_merged_followups(repo: str, now_iso: str) -> bool:
    """直近 FOLLOWUP_LOOKBACK_HOURS にマージされた PR の補償 dispatch を打ち直す。

    マージ後の dispatch が一過性エラーで落ちると、その PR はマージ済みのため
    イベント駆動の run は `merged` skip で終わり、open PR しか見ない advisory
    sweep も素通りする — 補償が永久に失われる (Codex P2 / PR #258)。closed
    (merged) PR を lookback 内で見直し、必要な run が無ければ dispatch し直す。
    冪等性は run 存在確認 (runs_since) が持つ。個別 PR の失敗は残りを止めず、
    最後に False を返して run を赤にする (できなかったことを隠さない)。
    """
    prs = fetch_recent_closed_prs(repo, now_iso)
    targets = recently_merged(prs, now_iso)
    print(
        f"merged followup sweep: 直近 {FOLLOWUP_LOOKBACK_HOURS:.0f}h のマージ {len(targets)} 件"
    )
    all_ok = True
    for pr in targets:
        number = pr["number"]
        try:
            files = gh("api", f"repos/{repo}/pulls/{number}/files", "--paginate")
            changed_paths = [f["filename"] for f in files]  # type: ignore[union-attr]
            print(f"#{number}: 補償 dispatch の確認 (merged {pr['merged_at']})")
            ensure_merge_followup(repo, pr["merged_at"], changed_paths)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            # 1 件の失敗で残りの補償を道連れにしない。ただし成功と偽らない —
            # 呼び出し元が run を赤にする (次の sweep が再試行する)
            all_ok = False
            print(f"#{number}: 補償 dispatch に失敗 — {e}")
    return all_ok


def maybe_execute_merge(
    repo: str, number: int, pr: dict, head_sha: str, changed_paths: list[str]
) -> bool:
    """マージ執行の入口 (イベント駆動 / sweep 共用)。マージできたら True。

    REVIEW_GATE_EXECUTE_MERGE=false の run では執行しない (Codex P1 / PR #258):
    pull_request イベントの run は **PR 側の check.py** を実行するため、workflow
    側でマージ権限 (contents:write) を渡していない — 権限が無いのに叩いて
    毎回エラーログを出すより、設計として執行しないことを明示する。マージは
    issue_comment (pm-accept 投稿) / schedule sweep の main 側コードが行う。
    """
    if os.environ.get("REVIEW_GATE_EXECUTE_MERGE", "true").lower() != "true":
        print(
            f"#{number}: マージ執行はこの run では無効 (REVIEW_GATE_EXECUTE_MERGE=false"
            " — pull_request run は PR 側コードで走るため権限を持たない。"
            " issue_comment / sweep の main 側 run が執行する)"
        )
        return False
    ok, reason = should_execute_merge(pr)
    if not ok:
        print(f"#{number}: マージ執行の対象外 — {reason}")
        return False
    merged, _ = try_merge(repo, number, head_sha)
    if merged:
        # merged_at はこの瞬間 (直後の存在確認が拾うのは既存 run だけで正しい)
        ensure_merge_followup(
            repo, datetime.now(timezone.utc).isoformat(), changed_paths
        )
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
        comments.append(
            (
                tracker.get("body") or "",
                tracker.get("created_at") or "",
                ((tracker.get("user") or {}).get("login", "")),
            )
        )
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
        # 事前フィルタは置かない (Codex P2-b / PR #258): updated_at の 24h
        # スクリーニングは投稿者を見ないため、cooldown 明け直前に bot (Codex 等)
        # がコメントすると再通知がさらに 24h 遅れ、bot の定期投稿が続くと永久に
        # 発火しない。needs-human Issue は高々数十件で、全件コメントを取って
        # should_notify_human_stall (唯一の判定) に通すコストは許容範囲。
        comments = fetch_comments_with_times(repo, number)
        notify, reason = should_notify_human_stall(
            issue.get("updated_at") or now_iso, comments, now_iso
        )
        if not notify:
            print(f"#{number}: needs-human 停滞通知なし — {reason}")
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


def sweep_one_pr(repo: str, pr: dict, now_iso: str) -> None:
    """sweep の PR 1 件分: マージ執行 + ストール検知 + advisory。"""
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
            # 保存済み success を信じず門をフル再評価してからマージ (Codex P1)。
            # 補償の基準時刻は reverify_and_merge がマージ成功後に自分で取る
            merged = reverify_and_merge(repo, number, head_sha)
            if merged:
                return  # マージ済み PR に advisory はもう要らない
            # 再評価で failure に訂正された場合、stall 判定は combined status を
            # 見るので自然に発火しない (全緑 + 未マージだけが stall)
            maybe_report_merge_stall(repo, number, head_sha, gate_success, now_iso)
    else:
        print(f"#{number}: マージ執行の対象外 — {skip_reason}")
    comment_bodies = fetch_comment_bodies(repo, number)
    # 両マーカー投稿済みならこの PR にやることは無い — files 取得を省く
    if not still_unposted(
        CODEX_RETRIGGER_MARKER, comment_bodies
    ) and not still_unposted(SECURITY_RETRIGGER_MARKER, comment_bodies):
        print(f"#{number}: 両 advisory 投稿済み — skip")
        return
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

    **失敗の隔離は「PR 1 件」「フェーズ 1 つ」単位** (Codex P2 / PR #258):
    1 つの PR で API が失敗し続けても残りの PR と後続フェーズ
    (sweep_merged_followups / sweep_human_queue) は必ず実行する — ここで
    sweep 全体が中断し続けると、補償対象が 24h lookback を外れて build/deploy
    の欠落が永久に残る。失敗は握りつぶさない — 1 件でもあれば非ゼロ exit で
    run を赤にする (watchers.json の review-gate watcher が拾う)。
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    failures = 0
    # 一覧取得もフェーズ隔離の内側に置く (Codex P2 / PR #258): ここで raise すると
    # failures の集計にも後続フェーズにも到達せず、followups / human queue が
    # 毎回スキップされる — 隔離したはずの中断が入口 1 行だけ残る
    targets: list[dict] = []
    try:
        prs = gh(
            "api", f"repos/{repo}/pulls?state=open&base=main&per_page=100", "--paginate"
        )
        targets = sweep_targets(prs)  # type: ignore[arg-type]
        print(f"advisory sweep: open PR {len(prs)} 件中 対象 {len(targets)} 件")  # type: ignore[arg-type]
    except Exception as e:  # noqa: BLE001 — 隔離が目的。失敗は exit code で顕在化する
        failures += 1
        print(f"open PR 一覧の取得に失敗 — PR フェーズを skip して続行: {e!r}")
    for pr in targets:
        try:
            sweep_one_pr(repo, pr, now_iso)
        except Exception as e:  # noqa: BLE001 — 隔離が目的。失敗は exit code で顕在化する
            failures += 1
            print(
                f"#{pr.get('number')}: sweep 処理に失敗 — この PR を skip して続行: {e!r}"
            )
    try:
        if not sweep_merged_followups(repo, now_iso):
            failures += 1
    except Exception as e:  # noqa: BLE001
        failures += 1
        print(f"merged followup sweep が失敗 — 他フェーズは続行: {e!r}")
    try:
        sweep_human_queue(repo, now_iso)
    except Exception as e:  # noqa: BLE001
        failures += 1
        print(f"human queue sweep が失敗 — {e!r}")
    if failures:
        # 続行はするが成功と偽らない — run を赤にして「できなかったこと」を残す
        # ::error はサマリー上部に出る Actions アノテーション — 赤の理由をログを
        # 掘らずに読めるようにする (watchers.json の review-gate watcher も拾う)
        print(f"::error::review-gate sweep: {failures} 件の失敗あり — run を失敗にする")
        return 1
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


def evaluate_pr(
    repo: str,
    number: int,
    *,
    status_sha: str | None = None,
    advisories: bool = True,
    execute_merge: bool = True,
) -> int:
    """PR を判定して commit status を貼る (イベント経路の本体)。

    status_sha: status の貼り先。省略時は PR の head (従来どおり)。merge_group
    (ADR 0042) では merge group の head SHA を渡す — 判定材料は PR 側のまま、
    **required check が読む場所**だけが merge group 側になる。
    advisories / execute_merge: merge_group では両方 False (advisory は PR 側
    イベントが持つ / マージは queue が実行する — ADR 0042 D4)。
    """
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
        ev = evaluate_gate(repo, number, head_sha, pr=pr)  # type: ignore[arg-type]
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        KeyError,
        json.JSONDecodeError,
    ) as e:
        # 取れなかったものを「合格」と書かない — error を貼って赤のまま残す
        post_status(repo, target_sha, "error", f"評価に失敗: {e}"[:140])
        raise

    verdict = ev.verdict
    changed_paths = ev.changed_paths
    post_status(
        repo, target_sha, "success" if verdict.ok else "failure", verdict.description
    )
    print(
        f"review-gate → {'🟢' if verdict.ok else '🔴'} {verdict.description}"
        f" (PR head {head_sha[:7]} / status 先 {target_sha[:7]})"
    )
    # 代役 judge で通した PR には degrade 告知を 1 回だけ貼る (Issue #400 D3-2)。
    # status の 1 行 (D3-1) だけだと一覧を開かない限り見えない — PR 本体に痕跡を残す。
    # **マージ執行より前**に貼る: 執行が即マージすると advisory 段に到達せず、
    # 「代役で通ってそのままマージされた」痕跡が status にしか残らなくなる
    if advisories:
        maybe_post_standin_notice(repo, number, verdict, ev.standin_notice_posted)
    if verdict.ok and execute_merge:
        # マージ執行 (ADR 0040 D1 / #253): この success status が「最後の required
        # check の緑」だった場合、GITHUB_TOKEN 起点のため GitHub の auto-merge は
        # 発火しない。auto-merge を武装済みの PR に限り、自分でマージまで執行する。
        # 405 (他 check 未完) 等は黙ってスキップ — sweep (30 分毎) が再試行する。
        merged = maybe_execute_merge(repo, number, pr, head_sha, changed_paths)
        if merged:
            return 0  # マージ済み — advisory 投稿は不要
    if not advisories:
        return 0
    # 合否の後に advisory (ADR 0038)。ここで失敗しても status は貼れているが、
    # run は赤にして「投稿できなかったこと」を隠さない
    maybe_post_advisories(
        repo=repo,
        number=number,
        pr=pr,
        changed_paths=changed_paths,
        comment_bodies=[body for body, _ in ev.comment_pairs],
        code_pr=ev.code_pr,
        codex_present=ev.codex_present,
    )
    return 0


def run_merge_group(repo: str) -> int:
    """merge_group イベント: 対象 PR を解決して同じ判定を merge group SHA に貼る。

    安全側の挙動 (ADR 0042): PR 番号を解決できない場合は **failure を merge group
    SHA に貼る** — 静かに緑を貼ると、受け入れの無い PR が queue を素通りする。
    failure なら queue から外れて PR 側に戻るだけで不可逆でない。

    マージ執行 (ADR 0040 D1) はここでは**行わない** — queue がマージまで実行する
    ので二重機構になる (ADR 0042 D4)。PR 側の経路 (イベント / sweep) の執行は
    そのまま残す: queue 未有効の間 (#269 前) はそちらが唯一のマージ経路であり、
    有効化後も「auto-merge 武装 = queue 投入」の入口として整合する。
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
    return evaluate_pr(
        repo, number, status_sha=mg_sha, advisories=False, execute_merge=False
    )


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        # イベント payload から PR 番号を解決できなかった (yml の式の取り漏れ等 —
        # Codex P1-b: pull_request_review には issue.number が無い)。int("") の
        # スタックトレースではなく、何が起きたかをログに残して赤にする
        print("::error::PR 番号が渡されていない — イベント payload の解決式を確認")
        return 1
    if sys.argv[1] == "--advisory-sweep":
        return run_advisory_sweep(repo)
    if sys.argv[1] == "--merge-group":
        return run_merge_group(repo)
    return evaluate_pr(repo, int(sys.argv[1]))


if __name__ == "__main__":
    sys.exit(main())
