"""[L1] UX 観測のデータブランチ蓄積 (append / git 運搬 / 移行) — ADR 0041。

無いと何が静かに通るか:
    - kind の振り分けを間違えても気づかず、probes/ と evals/ が混ざって
      読み出し (ux_eval / status-page) が観測を取りこぼす
    - 移行や workflow 再試行の重複追記が通り、トレンドの警告数・件数が水増しされる
    - orphan 作成の git 手順が壊れていても、初回の毎朝 run (書き込み側) まで
      発覚しない — ブランチが無い初回だけ通る特殊経路なのでテストでしか踏めない
    - 旧 Issue コメントからの変換で recordedAt (時系列の基準) が欠け、
      鮮度判定と月振り分けが全部狂う
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APPEND = HERE / "append.py"
SHELL = HERE / "append-observation.sh"
MIGRATE = HERE / "migrate-issue-comments.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


append_mod = _load("ux_data_append", APPEND)
migrate_mod = _load("ux_data_migrate", MIGRATE)


def _payload(kind: str = "ux-probe-record", **overrides) -> dict:
    base = {
        "kind": kind,
        "recordedAt": "2026-08-11T04:40:39+00:00",
        "probeId": "ux-probe-2026-08-11T04-40-00-955Z",
        "runId": "31459063773",
    }
    base.update(overrides)
    return base


def _write(tmp_path: Path, name: str, obj: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return path


# --- append.py -----------------------------------------------------------


def test_l1_kindとrecordedAtで月別ファイルに振り分ける(tmp_path) -> None:
    data = tmp_path / "data"
    probe = _write(tmp_path, "probe.json", _payload())
    mech = _write(
        tmp_path,
        "mech.json",
        _payload(
            "ux-eval-mech",
            recordedAt="2026-07-31T23:00:00Z",
            probeRunId="1",
            runId=None,
        ),
    )
    assert append_mod.append(data, probe) == 0
    assert append_mod.append(data, mech) == 0
    assert (data / "probes" / "2026-08.jsonl").exists()
    assert (data / "evals" / "2026-07.jsonl").exists()
    line = json.loads((data / "probes" / "2026-08.jsonl").read_text().strip())
    assert line["probeId"] == _payload()["probeId"]


def test_l1_同一観測は追記せずスキップする(tmp_path) -> None:
    """移行の再実行・workflow の再試行で同じ観測が二重に積まれない。"""
    data = tmp_path / "data"
    probe = _write(tmp_path, "probe.json", _payload())
    assert append_mod.append(data, probe) == 0
    assert append_mod.append(data, probe) == 0  # スキップも成功 (exit 0)
    lines = (data / "probes" / "2026-08.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1


def test_l1_再採点は別観測として残る(tmp_path) -> None:
    """同じプローブへの ux-judge-score でも scoredAt が違えば重複ではない。"""
    data = tmp_path / "data"
    s1 = _write(
        tmp_path,
        "s1.json",
        _payload("ux-judge-score", scoredAt="2026-08-11T06:00:00Z", runId=None),
    )
    s2 = _write(
        tmp_path,
        "s2.json",
        _payload("ux-judge-score", scoredAt="2026-08-11T09:00:00Z", runId=None),
    )
    assert append_mod.append(data, s1) == 0
    assert append_mod.append(data, s2) == 0
    lines = (data / "evals" / "2026-08.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2


def test_l1_不明なkindやrecordedAt欠落は追記しない(tmp_path) -> None:
    data = tmp_path / "data"
    bad_kind = _write(tmp_path, "bad-kind.json", _payload("free-chat"))
    no_ts = _write(tmp_path, "no-ts.json", _payload(recordedAt=None))
    naive_ts = _write(
        tmp_path, "naive-ts.json", _payload(recordedAt="2026-08-11T04:40:39")
    )
    assert append_mod.append(data, bad_kind) == 1
    assert append_mod.append(data, no_ts) == 1
    assert append_mod.append(data, naive_ts) == 1  # TZ 無しは月振り分けが曖昧
    assert not data.exists()


# --- append-observation.sh (git 運搬) ------------------------------------


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _setup_repo(tmp_path: Path) -> tuple[Path, Path]:
    """origin (bare) と、それを remote に持つ作業 repo を作る。"""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        check=True,
        capture_output=True,
    )
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-b", "main", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    _git("config", "user.email", "t@example.test", cwd=work)
    (work / "app.txt").write_text("main の中身\n")
    _git("add", "-A", cwd=work)
    _git("commit", "-m", "init", cwd=work)
    _git("remote", "add", "origin", str(origin), cwd=work)
    _git("push", "origin", "main", cwd=work)
    return origin, work


def _run_shell(work: Path, *payloads: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SHELL), *[str(p) for p in payloads]],
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
    )


def test_l1_ブランチが無ければorphanで作り_2回目は先端に追記する(tmp_path) -> None:
    origin, work = _setup_repo(tmp_path)
    p1 = _write(tmp_path, "p1.json", _payload())
    p2 = _write(
        tmp_path,
        "p2.json",
        _payload(probeId="probe-2", recordedAt="2026-08-12T04:40:00Z", runId="2"),
    )

    r1 = _run_shell(work, p1)
    assert r1.returncode == 0, r1.stderr
    r2 = _run_shell(work, p2)
    assert r2.returncode == 0, r2.stderr

    # origin 側のブランチに 2 コミット、JSONL に 2 行
    branch = "data/ux-observations"
    count = _git("rev-list", "--count", branch, cwd=origin)
    assert count == "2"
    content = _git("show", f"{branch}:probes/2026-08.jsonl", cwd=origin)
    ids = [json.loads(line)["probeId"] for line in content.strip().splitlines()]
    assert ids == [_payload()["probeId"], "probe-2"]

    # orphan であること: root コミットに親が無く、main と履歴を共有しない
    root = _git("rev-list", "--max-parents=0", branch, cwd=origin)
    assert len(root.splitlines()) == 1
    merge_base = subprocess.run(
        ["git", "merge-base", branch, "main"],
        cwd=origin,
        capture_output=True,
        text=True,
        check=False,
    )
    assert merge_base.returncode != 0, "orphan のはずが main と履歴を共有している"

    # 説明の README が入っている (人間が迷い込んだとき用)
    readme = _git("show", f"{branch}:README.md", cwd=origin)
    assert "append-observation.sh" in readme


def test_l1_全payloadが重複ならpushしない(tmp_path) -> None:
    """再実行で空コミットを積まない (毎朝の履歴がノイズで埋まらない)。"""
    origin, work = _setup_repo(tmp_path)
    p1 = _write(tmp_path, "p1.json", _payload())
    assert _run_shell(work, p1).returncode == 0
    before = _git("rev-list", "--count", "data/ux-observations", cwd=origin)
    r = _run_shell(work, p1)
    assert r.returncode == 0, r.stderr
    after = _git("rev-list", "--count", "data/ux-observations", cwd=origin)
    assert before == after


def test_l1_壊れたpayloadでは何もpushしない(tmp_path) -> None:
    origin, work = _setup_repo(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{ broken", encoding="utf-8")
    r = _run_shell(work, bad)
    assert r.returncode == 1
    branches = _git("branch", "--list", "data/ux-observations", cwd=origin)
    assert branches == ""


def test_l1_呼び出し元にpre_commitフックがあっても追記はpushされる(tmp_path) -> None:
    """#466 の再発防止。

    無いと何が静かに通るか:
        worktree は呼び出し元 repo の config を共有するので、core.hooksPath が
        絶対パスで入っている checkout では親の pre-commit がこの commit でも発火する。
        データブランチに lint-staged の設定は無いので必ず落ち、**commit も push も
        されないまま**その朝の観測が消える (2026-08-15 に採点 2 本を実際に失った)。
        commit から `-c core.hooksPath=/dev/null` を外すとこのテストが落ちる。
    """
    origin, work = _setup_repo(tmp_path)
    hooks = work / "fake-husky"
    hooks.mkdir()
    pre_commit = hooks / "pre-commit"
    pre_commit.write_text("#!/bin/sh\necho 'lint-staged なんて無い' >&2\nexit 1\n")
    pre_commit.chmod(0o755)
    _git("config", "core.hooksPath", str(hooks), cwd=work)

    # フックが実際に効いている checkout であることをまず確かめる (対照実験)。
    # これが通ってしまうと、下の assert は「フックが元から無い」でも緑になる
    (work / "canary.txt").write_text("x\n")
    _git("add", "-A", cwd=work)
    blocked = subprocess.run(
        ["git", "commit", "-m", "フックに止められるはず"],
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
    )
    assert blocked.returncode != 0, "pre-commit フックが効いていない — 前提が崩れている"

    p1 = _write(tmp_path, "p1.json", _payload())
    r = _run_shell(work, p1)
    assert r.returncode == 0, r.stderr
    content = _git("show", "data/ux-observations:probes/2026-08.jsonl", cwd=origin)
    assert json.loads(content.strip())["probeId"] == _payload()["probeId"]


def test_l1_pushできなかったときに成功を名乗らない(tmp_path) -> None:
    """#466 の本体。「取れなかったものを異常なしと書かない」をスクリプトに効かせる。

    無いと何が静かに通るか:
        append.py がローカル書き込みの時点で「追記しました」と出すため、後段の
        commit / push が落ちても**画面には成功が出る**。運用者は蓄積が載ったと
        読み、データブランチには何も無い、という取り違えが起きる (2026-08-15 の実例)。

    禁止語 (`追記しました` が出ないこと) だけを見ると、`追記が完了しました` の
    ような**言い換えで #466 をそのまま復活させても緑のまま**通る (PR #475 の
    代役レビューで実測)。そこで**出るべき行の側**を固定する — 期待文言はここに
    直書きし、共有定数にはしない (定数にすると文言と一緒にテストの期待値も
    動いてしまい、言い換えを止められない)。
    """
    _, work = _setup_repo(tmp_path)
    _git("remote", "set-url", "origin", str(tmp_path / "居ないリモート.git"), cwd=work)
    p1 = _write(tmp_path, "p1.json", _payload())

    r = _run_shell(work, p1)
    assert r.returncode != 0, "push できていないのに成功で終わっている"
    combined = r.stdout + r.stderr

    # 出るべき行: ローカルに書いただけで push はこれからだと分かる文言
    assert "ローカルに書きました (commit/push はこの後)" in combined, combined
    # 出てはいけない行: 蓄積が確定したことを名乗るのは push 成功後だけ
    assert "push しました" not in combined, combined


# --- migrate-issue-comments.py -------------------------------------------


def test_l1_旧コメントを時系列順のpayloadに変換しrecordedAtを付ける(tmp_path) -> None:
    older = {
        "body": "## UX プローブ記録\n\n```json\n"
        + json.dumps({"kind": "ux-probe-record", "probeId": "p1", "record": {}})
        + "\n```\n",
        "created_at": "2026-08-09T22:37:35Z",
        "login": "github-actions[bot]",
    }
    newer = {
        "body": "```json\n"
        + json.dumps({"kind": "ux-eval-mech", "probeId": "p1", "probeRunId": "1"})
        + "\n```",
        "created_at": "2026-08-10T15:34:50Z",
        "login": "github-actions[bot]",
    }
    noise = {"body": "人間の雑談", "created_at": "2026-08-10T00:00:00Z"}
    other_kind = {
        "body": '```json\n{"kind": "release-note"}\n```',
        "created_at": "2026-08-10T01:00:00Z",
    }
    # 逆順で渡しても created_at で並べ直す
    comments = _write(tmp_path, "comments.json", [newer, noise, other_kind, older])
    out = tmp_path / "payloads"

    assert migrate_mod.run(out, [comments]) == 0
    files = sorted(out.glob("*.json"))
    assert [f.name for f in files] == [
        "0001-ux-probe-record.json",
        "0002-ux-eval-mech.json",
    ]
    first = json.loads(files[0].read_text(encoding="utf-8"))
    assert first["recordedAt"] == "2026-08-09T22:37:35Z"


def test_l1_変換したpayloadはappendでそのまま取り込める(tmp_path) -> None:
    """移行 (変換) と蓄積 (append) の結合 — 片方だけ形を変えると落ちる。"""
    comment = {
        "body": "```json\n"
        + json.dumps(
            {
                "kind": "ux-judge-score",
                "probeId": "p1",
                "scoredAt": "2026-08-10T15:40:52Z",
            }
        )
        + "\n```",
        "created_at": "2026-08-10T15:42:32Z",
        "author_association": "OWNER",
    }
    comments = _write(tmp_path, "comments.json", [comment])
    out = tmp_path / "payloads"
    assert migrate_mod.run(out, [comments]) == 0

    data = tmp_path / "data"
    payloads = sorted(out.glob("*.json"))
    assert payloads, "変換結果が空"
    for p in payloads:
        assert append_mod.append(data, p) == 0
    line = json.loads((data / "evals" / "2026-08.jsonl").read_text().strip())
    assert line["kind"] == "ux-judge-score"
    assert line["recordedAt"] == "2026-08-10T15:42:32Z"


def test_l1_フェンス内のu0060置換はjson_loadsで元に戻る(tmp_path) -> None:
    """旧 format (probe-record-comment.py) の fence-safe 置換を含むコメントが読める。"""
    body = (
        "```json\n"
        '{"kind": "ux-probe-record", "probeId": "p1", '
        '"record": {"turns": [{"assistantText": "\\u0060code\\u0060"}]}}'
        "\n```"
    )
    comments = _write(
        tmp_path,
        "c.json",
        [
            {
                "body": body,
                "created_at": "2026-08-09T00:00:00Z",
                "login": "github-actions[bot]",
            }
        ],
    )
    out = tmp_path / "payloads"
    assert migrate_mod.run(out, [comments]) == 0
    payload = json.loads(next(out.glob("*.json")).read_text(encoding="utf-8"))
    assert payload["record"]["turns"][0]["assistantText"] == "`code`"


def test_l1_信頼できない投稿者の観測コメントは移行しない(tmp_path) -> None:
    """無いと何が静かに通るか: #162/#127 は誰でもコメントできるため、第三者が
    kind 付き JSON を投稿するだけで偽の観測が正史 (データブランチ) に永続化される
    (PR #260 Codex P1)。"""
    forged = {
        "body": "```json\n"
        + json.dumps({"kind": "ux-eval-mech", "probeId": "evil", "probeRunId": "666"})
        + "\n```",
        "created_at": "2026-08-10T12:00:00Z",
        "login": "attacker",
        "author_association": "NONE",
    }
    comments = _write(tmp_path, "comments.json", [forged])
    out = tmp_path / "payloads"
    assert migrate_mod.run(out, [comments]) == 0
    assert list(out.glob("*.json")) == []


def test_l1_型を偽装したmech観測は移行しない(tmp_path) -> None:
    """無いと何が静かに通るか: avgMs を文字列にした観測が通ると、status-page の
    描画が数値比較で TypeError になりページ生成が止まり続ける (PR #260 Codex P1)。"""
    forged = {
        "body": "```json\n"
        + json.dumps(
            {
                "kind": "ux-eval-mech",
                "probeId": "p1",
                "probeRunId": "2",
                "metrics": {
                    "latency": {
                        "sendToReplyVisibleMs": {"avgMs": "4100", "maxMs": 9000}
                    }
                },
            }
        )
        + "\n```",
        "created_at": "2026-08-10T12:00:00Z",
        "login": "github-actions[bot]",
    }
    comments = _write(tmp_path, "comments.json", [forged])
    out = tmp_path / "payloads"
    assert migrate_mod.run(out, [comments]) == 0
    assert list(out.glob("*.json")) == []
