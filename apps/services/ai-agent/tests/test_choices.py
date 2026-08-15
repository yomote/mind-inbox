"""[L1] 選択肢の提示 (`offer_choices` / #432-b) を MAF の実ループを通して pin する。

裁定 (Issue #432 issuecomment-5300560497) のうち、コードで守れるのはこの 2 つ:

  2. **完了型** — 承認 (中断 + checkpoint) の機構を再利用しない。選択肢を出しても
     サーバに待ち状態は残らず、ターンはそのまま完了する
  3. **LLM 往復 2 回で逐次表示を守る** (ADR 0024 維持) — ツール呼び出しの後に
     本文生成の往復がもう 1 回走り、その本文が delta で流れる

無いと何が静かに通るか:
- 選択肢が `requires_approval` に相乗りし、**承認カードの「承認するまで実行され
  ません」という文言が会話の分岐に付く** (押さないと会話が止まると誤解させる)
- 承認要求のターンに選択肢が同時に出て、押した文言が実行の可否に効くのか会話に
  効くのか読めない画面になる
- 上限 (3 件 / 1 件 40 字) を超えた選択肢が**黙って切り詰められて**提示され、
  タップしたユーザーが「自分が書いていない中途半端な発話」を送る
- ツール呼び出しの後に本文の往復が走らなくなり (終端型への退行)、選択肢ターンだけ
  逐次表示が消える (ADR 0024 が壊れたことに気づけない)
- 選択肢の文言 (= 相談内容から作られる) がログに出る (Issue #313)

ここで test しないこと:
- どういうときに選択肢を出すか (プロンプトの領域 / LLM を採点者にしない)
- 画面の見た目 (frontend の単体テスト)
"""

import logging

import pytest

from app.schemas import ChatStreamDelta, ChatStreamDone
from app.tools import MAX_CHOICE_LENGTH, MAX_CHOICES, is_side_effecting
from app.workflow import run_workflow, run_workflow_stream
from tests.fakes import (
    ScriptedChatClient,
    text_step,
    tool_call_step,
    tool_calls_step,
)

pytestmark = pytest.mark.usefixtures("tools_enabled")

# 台本 1: モデルが選択肢を出す / 台本 2: その後の本文 (= 往復 2 回目)
OPTIONS = ["仕事のこと", "家族のこと", "自分の体調のこと"]


def offering_client(options: list[str], reply: str = "どれか近いものはありますか。"):
    return ScriptedChatClient(
        [tool_call_step("offer_choices", {"options": options}), text_step(reply)]
    )


class TestChoicesOnResponse:
    async def test_l1_提示した選択肢が応答に載り_llm_往復は_2_回(
        self, session_repo, approval_repo
    ):
        # 裁定 3 (逐次表示のために往復 2 回) の実測。1 回に減っていたら終端型
        # (ツール引数をそのまま本文にする) への退行で、逐次表示が消えている
        client = offering_client(OPTIONS)

        res = await run_workflow(
            "s-choices", "うまく言えない", session_repo, approval_repo, client
        )

        assert res.choices == OPTIONS
        assert res.reply == "どれか近いものはありますか。"
        assert client.inner_calls == 2

    async def test_l1_選択肢は承認フラグに相乗りしない(
        self, session_repo, approval_repo
    ):
        # 無いと: 選択肢を出しただけで承認カード (「承認するまで、この操作は
        # 行われません」) が画面に出る
        res = await run_workflow(
            "s-not-approval",
            "うまく言えない",
            session_repo,
            approval_repo,
            offering_client(OPTIONS),
        )

        assert res.requires_approval is False
        assert res.approval_request_id is None
        assert is_side_effecting("offer_choices") is False

    async def test_l1_選択肢を出さないターンの応答は空のまま(
        self, session_repo, approval_repo
    ):
        # 無いと: 空チップの帯が全ターンに出る / フロントが「選択肢あり」を
        # 誤判定しても気づけない
        client = ScriptedChatClient([text_step("そうだったんですね。")])

        res = await run_workflow(
            "s-none", "疲れました", session_repo, approval_repo, client
        )

        assert res.choices == []
        assert client.inner_calls == 1

    async def test_l1_承認要求のターンには選択肢を載せない(
        self, session_repo, approval_repo
    ):
        # 無いと: 承認カードと選択肢が同時に出て、押した文言が実行の可否に効くのか
        # 会話に効くのか読めない画面ができる
        client = ScriptedChatClient(
            [
                tool_calls_step(
                    ("offer_choices", {"options": OPTIONS}),
                    ("send_reply", {"to": "a@example.com", "body": "はい"}),
                )
            ]
        )

        res = await run_workflow(
            "s-approval", "返信しておいて", session_repo, approval_repo, client
        )

        assert res.requires_approval is True
        assert res.choices == []


class TestChoiceLimits:
    """上限違反は**切り詰めずに断る**。切り詰めると、タップした文言がそのまま次の
    発話として送られる仕様 (§5.10) と噛み合わず、ユーザーが書いていない発話になる。"""

    async def test_l1_上限を超えた件数は提示しない(self, session_repo, approval_repo):
        client = offering_client(["A", "B", "C", "D"])

        res = await run_workflow(
            "s-too-many", "選ばせて", session_repo, approval_repo, client
        )

        assert res.choices == []
        # 断ったことはモデルに伝わっている (作り直せる) = 履歴に結果が残る
        history = await session_repo.get("s-too-many")
        assert any(
            f"選択肢は {MAX_CHOICES} 件までです" in m.text for m in history.messages
        )

    async def test_l1_長すぎる選択肢は提示しない(self, session_repo, approval_repo):
        client = offering_client(["あ" * (MAX_CHOICE_LENGTH + 1)])

        res = await run_workflow(
            "s-too-long", "選ばせて", session_repo, approval_repo, client
        )

        assert res.choices == []

    async def test_l1_空白だけの選択肢は提示しない(self, session_repo, approval_repo):
        client = offering_client(["  ", ""])

        res = await run_workflow(
            "s-empty", "選ばせて", session_repo, approval_repo, client
        )

        assert res.choices == []

    async def test_l1_重複した選択肢は_1_つに畳む(self, session_repo, approval_repo):
        client = offering_client(["仕事のこと", "仕事のこと", "家族のこと"])

        res = await run_workflow(
            "s-dup", "選ばせて", session_repo, approval_repo, client
        )

        assert res.choices == ["仕事のこと", "家族のこと"]

    async def test_l1_同じターンでの_2_回目の提示は追加しない(
        self, session_repo, approval_repo
    ):
        # 無いと: 1 ターンで 2 回呼ばれると上限 (3 件) を超えた帯が画面に出る
        client = ScriptedChatClient(
            [
                tool_call_step("offer_choices", {"options": OPTIONS}, call_id="c-1"),
                tool_call_step("offer_choices", {"options": ["別の案"]}, call_id="c-2"),
                text_step("どれか近いものはありますか。"),
            ]
        )

        res = await run_workflow(
            "s-twice", "選ばせて", session_repo, approval_repo, client
        )

        assert res.choices == OPTIONS


class TestChoicesStreaming:
    async def test_l1_選択肢ターンでも本文は逐次で流れ_done_に選択肢が載る(
        self, session_repo, approval_repo
    ):
        # 裁定 3 (ADR 0024 維持) の実測。往復 2 回目の本文が delta で流れなければ、
        # 選択肢ターンだけ「無言で待たされて一括表示」に戻っている
        client = ScriptedChatClient(
            [
                tool_call_step("offer_choices", {"options": OPTIONS}),
                text_step("どれか", "近いものは", "ありますか。"),
            ]
        )

        events = [
            e
            async for e in run_workflow_stream(
                "s-stream", "うまく言えない", session_repo, approval_repo, client
            )
        ]

        deltas = [e.text for e in events if isinstance(e, ChatStreamDelta)]
        assert deltas == ["どれか", "近いものは", "ありますか。"]
        done = events[-1]
        assert isinstance(done, ChatStreamDone)
        assert done.response.choices == OPTIONS
        assert done.response.requires_approval is False


class TestChoicesObservability:
    async def test_l1_選択肢の文言はログに出ない(
        self, session_repo, approval_repo, caplog
    ):
        # 無いと: 相談内容から作られた文字列が Log Analytics に 30 日残る (#313)
        secret = "上司に本音を伝えること"
        client = offering_client([secret])

        with caplog.at_level(logging.DEBUG):
            await run_workflow("s-log", "選ばせて", session_repo, approval_repo, client)

        ours = [r.getMessage() for r in caplog.records if r.name.startswith("app.")]
        assert ours, "アプリのログを 1 行も拾えていない (検査になっていない)"
        assert not any(secret in line for line in ours)
        # 提示した事実そのものは残す (件数だけ)
        assert any("Tool[offer_choices] invoked" in line for line in ours)
