"""[単体] 会話履歴の窓が **LLM へ実際に渡る messages** に効いていることを pin する (#486)。

`select_window` の判定そのものは tests/test_history_window.py が見る。ここで見るのは
**上限が本当に掛かっているか** — 窓を作っても呼び出し側で使い忘れたら 1 文字も守れない。

**なぜ単体の入場条件 (strategy.md §2.2) を満たすか** (Codex P1 / PR #514 への回答):
これは受け渡し・ルーティングの検証ではなく、**上限という不変条件が守られているか**の
検証で、外れたときの壊れ方が入場条件そのもの — 例外は出ず、応答も普通に返り、
**使い込んだセッションだけが数日かけて恒久 500 に育つ**。派手に落ちないので
「実環境の通し (ゴールデンパス) が守る」も効かない: ゴールデンパスの会話は数ターンで
終わるため、**窓の上限を跨ぐ会話が原理的に現れない**。ここが唯一の検出点になる。
検証の位置づけは design-gate 承認 (2026-08-17) の組む順②「両経路適用 +
『LLM へ渡る messages が上限内』を L2 で pin」が指定したもの (L2 → 単体 は §6.2 の読み替え)。

無いと何が静かに通るか:
- `converse` が `history.messages` を丸ごと渡す実装に戻り、窓が「あるのに効かない」
  状態になる (テストは全部緑のまま、実環境のセッションだけが恒久 500 に育つ)
- **`/approve` 再開の経路だけ**素通りする — 2 本目の扉。承認を使う会話ほど履歴が
  長くなるので、いちばん踏みやすい経路が守られないまま残る
- save 側の刈り込みが外れ、LLM への送信は守られたまま **Cosmos の 1 文書だけが
  無限に育つ** (2MB 上限に当たった日から、そのセッションは save 失敗で進まなくなる)
- 承認再開で `pending` (function_call ⇄ 承認要求の対) まで窓に巻き込んで削り、
  再開そのものが壊れる

仕様の出どころ: Issue #486 の design-gate 承認コメント (2026-08-17) —
「適用は workflow.py の 2 経路両方 — converse と /approve 再開」「save 時にも窓まで刈る」。
"""

import logging

import pytest

from app.config import get_settings
from app.history import ChatHistory
from app.workflow import (
    resume_after_approval,
    run_workflow,
)
from tests.fakes import ScriptedChatClient, text_step, tool_call_step

APPROVAL_ARGS = {"to": "a@example.com", "body": "hi"}


@pytest.fixture
def narrow_window(monkeypatch):
    """窓を意図的に狭くした構成 (件数 5 / 文字数 200)。

    既定 (40 通 / 40,000 字) のままだと、テストで作れる長さの会話では窓が発火せず
    「効いているつもり」のテストになる。
    """
    monkeypatch.setenv("HISTORY_WINDOW_MAX_MESSAGES", "5")
    monkeypatch.setenv("HISTORY_WINDOW_MAX_CHARS", "200")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _grow_session(
    session_id, session_repo, turns: int, *, trailing_user: str | None = None
) -> None:
    """既に長く育ったセッションを作る (窓の導入**前**に保存された文書の再現)。

    **`_save_session` を通さず repo へ直に書く** — 通してしまうとここで刈られ、
    「刈られていない履歴が読み出される」という検証したい状況が作れない。

    `trailing_user` を渡すと履歴の末尾を assistant 応答の無い user 発言にする
    (= 応答を返せずに落ちたターンの跡 / 再試行の判定に効く)。
    """
    history = ChatHistory()
    history.add_system_message("SYS")
    for index in range(turns):
        history.add_user_message(f"過去の質問 {index}")
        history.add_assistant_message(f"過去の答え {index}")
    if trailing_user is not None:
        history.add_user_message(trailing_user)
    await session_repo.save(session_id, history)


class TestConversePath:
    async def test_単体_converse_が_llm_に渡す_messages_は窓の上限内(
        self, narrow_window, session_repo, approval_repo
    ):
        """**保存側の刈り込みが効かないターン**で見る (これが唯一の再現条件)。

        通常のターンは RECEIVE の save が先に刈るので、`converse` 側の窓を外しても
        症状が出ない = テストが緑のまま通ってしまう (この構図に一度引っかかった)。
        保存を通らないのは **失敗ターンの再試行** (#120: ストリーミングが落ちて
        フロントが同じ文面で /chat へフォールバックした場合) で、
        `_append_user_message_once` が重複を避けて save ごと省く経路。長い会話ほど
        起きやすく、そこで丸ごと LLM へ飛ぶのを止めているのが `converse` の窓。
        """
        # 履歴の末尾が user 発言 = 応答を返せずに落ちたターンの跡 (窓の導入前に
        # 保存された文書でもあるので、刈られていない 41 通がそのまま読み出される)
        await _grow_session("s-win", session_repo, turns=20, trailing_user="いまの質問")
        stored = await session_repo.get("s-win")
        assert len(stored.messages) == 42  # 前提: 保存側では刈られていない

        client = ScriptedChatClient([text_step("はい。")])
        await run_workflow("s-win", "いまの質問", session_repo, approval_repo, client)

        sent = client.seen_messages[0]
        assert len(sent) <= 5
        assert sum(len(m.text) for m in sent) <= 200
        # 落としたのは古い方 / 残したのは system プロンプトと今のターン
        texts = [m.text for m in sent]
        assert texts[0] == "SYS"
        assert texts[-1] == "いまの質問"
        assert "過去の質問 0" not in texts

    async def test_単体_窓が広ければ全量が渡る(self, session_repo, approval_repo):
        """無いと: 窓が常に切っているだけの実装 (上限を読んでいない) でも上のテストが緑。"""
        await _grow_session("s-wide", session_repo, turns=20)
        client = ScriptedChatClient([text_step("はい。")])

        await run_workflow("s-wide", "いまの質問", session_repo, approval_repo, client)

        # 既定 (40 通 / 40,000 字) には 1 + 40 + 1 = 42 通は入らないが、
        # 20 ターン ぶんの文字数では切れない = 件数の側だけが効いている状態
        sent = client.seen_messages[0]
        assert len(sent) == 40
        assert "過去の質問 19" in [m.text for m in sent]


class TestApprovalResumePath:
    async def test_単体_approve_再開が_llm_に渡す_messages_も窓の上限内(
        self, narrow_window, tools_enabled, session_repo, approval_repo
    ):
        """**2 本目の扉**。承認の往復を挟む会話ほど履歴が長くなるので、ここが素通り
        すると「承認を使う人のセッションだけが恒久 500 に育つ」。

        再開は保存を挟まずに履歴を読んで LLM を呼ぶ経路なので、刈られていない文書
        (窓の導入前 / env で上限を下げた直後) をそのまま渡しうる。承認要求を作った
        あとに repo へ直接 41 通を書き戻して、その状況を作る。
        """
        client = ScriptedChatClient(
            [tool_call_step("send_reply", APPROVAL_ARGS), text_step("送りました。")]
        )
        res = await run_workflow(
            "s-appr", "返信して", session_repo, approval_repo, client
        )

        # 刈られていない文書に差し替える (窓の導入前に保存された文書の再現)
        await _grow_session("s-appr", session_repo, turns=20)
        assert len((await session_repo.get("s-appr")).messages) == 41

        await resume_after_approval(
            res.approval_request_id, True, session_repo, approval_repo, client
        )

        # 2 往復目 = 承認後の再開。履歴部分 (窓の内側) が上限内であること
        sent = client.seen_messages[1]
        history_part = [
            m
            for m in sent
            if m.role in ("system", "user", "assistant")
            and all(c.type == "text" for c in m.contents)
        ]
        texts = [m.text for m in history_part]
        assert len(history_part) <= 5
        assert sum(len(t) for t in texts) <= 200
        assert texts[0] == "SYS"
        assert "過去の質問 0" not in texts

    async def test_単体_再開に要る_pending_は窓で削らない(
        self, narrow_window, tools_enabled, session_repo, approval_repo
    ):
        """無いと: 窓を `history + pending` 全体に掛ける実装が通り、承認要求ごと
        削れて「承認したのに何も起きない」になる (窓が狭いときだけ再現する)。"""
        await _grow_session("s-pending", session_repo, turns=20)
        client = ScriptedChatClient(
            [tool_call_step("send_reply", APPROVAL_ARGS), text_step("送りました。")]
        )

        res = await run_workflow(
            "s-pending", "返信して", session_repo, approval_repo, client
        )
        reply = await resume_after_approval(
            res.approval_request_id, True, session_repo, approval_repo, client
        )

        assert reply == "送りました。"
        sent = client.seen_messages[1]
        # MAF は承認応答を function invocation loop の内側で実行に変えるので、
        # この層に届くのは function_call ⇄ function_result の対。**窓がここを削ると
        # 対が割れて再開が壊れる**ので、両方が残っていることを見る
        content_types = {c.type for m in sent for c in m.contents}
        assert "function_call" in content_types
        assert "function_result" in content_types


class TestSavePruning:
    async def test_単体_保存される履歴も窓まで刈られる(
        self, narrow_window, session_repo, approval_repo
    ):
        """無いと: LLM への送信だけ守られ、Cosmos の 1 文書が 2MB 上限まで育ち続ける。"""
        await _grow_session("s-save", session_repo, turns=20)
        client = ScriptedChatClient([text_step("はい。")])

        await run_workflow("s-save", "いまの質問", session_repo, approval_repo, client)

        saved = await session_repo.get("s-save")
        assert len(saved.messages) <= 5
        assert sum(len(m.text) for m in saved.messages) <= 200
        assert saved.messages[0].text == "SYS"
        assert saved.messages[-1].text == "はい。"


class TestDegenerateWindowIsAudible:
    async def test_単体_窓が最新ターンのみまで縮退したら警告を出す(
        self, monkeypatch, caplog, session_repo, approval_repo
    ):
        """無いと: 上限を system プロンプト長より小さく設定した瞬間、エージェントが
        会話の記憶を丸ごと失うのに**応答は普通に返る**ので、画面からも CI からも
        「なんとなく噛み合わない」としか見えない (設定ミスが永久に見つからない)。
        """
        monkeypatch.setenv("HISTORY_WINDOW_MAX_MESSAGES", "3")
        monkeypatch.setenv("HISTORY_WINDOW_MAX_CHARS", "10")
        get_settings.cache_clear()
        try:
            await _grow_session(
                "s-degenerate", session_repo, turns=5, trailing_user="いまの質問"
            )
            client = ScriptedChatClient([text_step("はい。")])
            with caplog.at_level(logging.WARNING):
                await run_workflow(
                    "s-degenerate", "いまの質問", session_repo, approval_repo, client
                )
        finally:
            get_settings.cache_clear()

        assert any(
            "縮退" in record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.WARNING
        ), "窓の縮退が警告として出ていない"
