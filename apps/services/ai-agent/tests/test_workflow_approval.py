"""[L1] MAF workflow の承認中断/再開 (checkpoint + HITL) を pin する。

無いと何が静かに通るか:
- approvalRequestId → MAF checkpoint の写像が壊れ、/approve が中断点から再開できない退行
- 承認/却下の分岐反転 (却下なのにツールが走る / 承認なのに走らない) が静かに通る
- 再開後の履歴積み忘れ (tool result / assistant 応答) で次ターンの文脈が壊れる退行
- 完了した run の checkpoint が解放されず、長寿命レプリカのメモリを食い潰す退行
  (PR #243 レビュー指摘)
- **再開後**にモデルが別の副作用ツールを要求したターンを完了扱いし、承認レコードを
  作らないまま空 reply で閉じる退行 (PR #417 P1 / TestChainedApproval)
- **承認再開中に LLM が落ちた**とき、実行済み副作用ツールの記録が履歴に残らず、
  次ターンで同じツールがもう一度呼ばれる退行 (judge #417 / TestResumeFailure)

ここで test しないこと:
- SSE / HTTP の枠組み (それは L2 /chat 系)
- MAF の checkpoint 実装自体 (フレームワークの領域)

fixture 置き換え (#320): 承認は自前の `is_side_effecting` 判定ではなく
`@tool(approval_mode="always_require")` を読む **MAF の function invocation loop** が
起こすようになった。したがって fake は「get_response をまるごと差し替えたもの」では
意味がなく、MAF の層を本物のまま通す `tests/fakes.ScriptedChatClient` を使う。
検証意図 (承認の中断 / 再開 / 却下 / 掃除) は不変。
"""

import asyncio
import logging
from datetime import datetime

import pytest

from app.workflow import (
    ApprovalAlreadyProcessedError,
    _pending_run_storages,
    get_pending_checkpoint_storage,
    resume_after_approval,
    run_workflow,
)
from tests.fakes import (
    ScriptedChatClient,
    error_step,
    text_step,
    tool_call_step,
    tool_calls_step,
)

pytestmark = pytest.mark.usefixtures("tools_enabled")

APPROVAL_ARGS = {"to": "a@example.com", "body": "hi"}
ARCHIVE_ARGS = {"message_id": "m-1"}


def approval_script(reply: str = "対応しました。") -> ScriptedChatClient:
    """副作用ツールを呼ぶ → (承認後) 応答テキスト、の 2 往復ぶんの台本。"""
    return ScriptedChatClient(
        [tool_call_step("send_reply", APPROVAL_ARGS), text_step(reply)]
    )


def chained_approval_script(reply: str = "両方やりました。") -> ScriptedChatClient:
    """`send_reply` を承認 → **その実行後にモデルが別の副作用ツールを要求する**台本。

    Codex 指摘 (PR #417 P1) の再現ケースそのもの。
    """
    return ScriptedChatClient(
        [
            tool_call_step("send_reply", APPROVAL_ARGS),
            tool_call_step("archive_message", ARCHIVE_ARGS, call_id="call-2"),
            text_step(reply),
        ]
    )


def pending_records(approval_repo) -> list:
    return [r for r in approval_repo._store.values() if r.status == "pending"]


class TestApprovalCheckpointMapping:
    async def test_l1_approval_request_id_maps_to_maf_checkpoint(
        self, session_repo, approval_repo
    ):
        client = approval_script()

        res = await run_workflow(
            "s-map", "返信して", session_repo, approval_repo, client
        )

        assert res.requires_approval is True
        record = await approval_repo.get(res.approval_request_id)
        assert record is not None
        assert record.status == "pending"
        assert record.checkpoint_id is not None

        # 写像の実体: checkpoint はこの run の storage に実在し、
        # この approvalRequestId の pending request を保持している
        storage = get_pending_checkpoint_storage(res.approval_request_id)
        assert storage is not None
        checkpoint = await storage.load(record.checkpoint_id)
        assert res.approval_request_id in checkpoint.pending_request_info_events

    async def test_l1_approval_plan_carries_model_supplied_tool_call(
        self, session_repo, approval_repo
    ):
        # 無いと: 承認 UI に「何を実行しようとしているか」が渡らず、
        # 中身を見ずに承認させる画面になる (G1 が形骸化する)
        client = approval_script()

        res = await run_workflow(
            "s-plan", "返信して", session_repo, approval_repo, client
        )

        record = await approval_repo.get(res.approval_request_id)
        assert record.plan.tool_name == "send_reply"
        assert record.plan.tool_args == APPROVAL_ARGS
        assert record.plan.is_side_effecting is True


class TestResumeAfterApproval:
    async def test_l1_approved_resume_executes_tool_and_responds(
        self, session_repo, approval_repo
    ):
        client = approval_script()

        res = await run_workflow(
            "s-ok", "返信して", session_repo, approval_repo, client
        )
        reply = await resume_after_approval(
            res.approval_request_id, True, session_repo, approval_repo, client
        )

        assert reply == "対応しました。"

        record = await approval_repo.get(res.approval_request_id)
        assert record.status == "approved"

        history = await session_repo.get("s-ok")
        contents = [m.text for m in history.messages]
        assert any("Tool result (send_reply)" in c for c in contents)
        assert history.messages[-1].role == "assistant"
        assert contents[-1] == "対応しました。"

    async def test_l1_rejected_resume_cancels_without_tool(
        self, session_repo, approval_repo
    ):
        client = approval_script()

        res = await run_workflow(
            "s-ng", "返信して", session_repo, approval_repo, client
        )
        reply = await resume_after_approval(
            res.approval_request_id, False, session_repo, approval_repo, client
        )

        assert reply == "操作はキャンセルされました。他にご用件はありますか？"

        record = await approval_repo.get(res.approval_request_id)
        assert record.status == "rejected"

        history = await session_repo.get("s-ng")
        contents = [m.text for m in history.messages]
        assert not any("Tool result" in c for c in contents)
        assert history.messages[-1].role == "assistant"
        assert contents[-1] == reply

    async def test_l1_rejection_costs_no_llm_roundtrip(
        self, session_repo, approval_repo
    ):
        # 無いと: 却下でも応答生成のために LLM を叩く実装に戻り、
        # 「キャンセルしただけなのにトークンを燃やす」に静かに退行する
        client = approval_script()

        res = await run_workflow(
            "s-ng-cost", "返信して", session_repo, approval_repo, client
        )
        calls_before = client.inner_calls
        await resume_after_approval(
            res.approval_request_id, False, session_repo, approval_repo, client
        )

        assert client.inner_calls == calls_before

    async def test_l1_resolved_approval_releases_checkpoint_storage(
        self, session_repo, approval_repo
    ):
        client = approval_script()

        res = await run_workflow(
            "s-release", "返信して", session_repo, approval_repo, client
        )
        assert get_pending_checkpoint_storage(res.approval_request_id) is not None

        await resume_after_approval(
            res.approval_request_id, True, session_repo, approval_repo, client
        )

        assert get_pending_checkpoint_storage(res.approval_request_id) is None

    async def test_l1_resume_unknown_id_raises(self, session_repo, approval_repo):
        client = approval_script()

        with pytest.raises(ValueError, match="Approval not found"):
            await resume_after_approval(
                "no-such-id", True, session_repo, approval_repo, client
            )

    @pytest.mark.parametrize(
        "first_decision,expected_status",
        [(True, "approved"), (False, "rejected")],
    )
    async def test_単体_二回目の承認は現在状態つきの専用例外になる(
        self, session_repo, approval_repo, first_decision, expected_status
    ):
        """二重送信は「レコードが無い」(ValueError → 404) と**別の型**で上がる (#82)。

        無いと何が静かに通るか: 二重送信が未知 ID と同じ 404 に戻り、「レコードが
        消えた」のか「もう解決済み」なのかがクライアントから読めなくなる。
        **確実に未実行と言える却下済みまで**「実行されたか分かりません」に落ち、
        ユーザーは会話を遡って自分で確かめるしかなくなる。
        """
        client = approval_script()

        res = await run_workflow(
            "s-twice", "返信して", session_repo, approval_repo, client
        )
        await resume_after_approval(
            res.approval_request_id, first_decision, session_repo, approval_repo, client
        )

        with pytest.raises(ApprovalAlreadyProcessedError) as excinfo:
            await resume_after_approval(
                res.approval_request_id, True, session_repo, approval_repo, client
            )

        # 型だけでなく **現在状態も** 運べていること (承認済み / 却下済みを言い分ける材料)
        assert excinfo.value.status == expected_status
        assert excinfo.value.processed_at is not None
        # ValueError (= 404 に写る側) に混ざっていないこと。継承で通ってしまうと
        # main.py の except の順序次第で静かに 404 へ戻る
        assert not isinstance(excinfo.value, ValueError)

    async def test_単体_決定を受け付けた時刻が承認レコードに記録される(
        self, session_repo, approval_repo
    ):
        """409 body の `processed_at` の出どころを pin する (#82)。

        無いと何が静かに通るか: 時刻を書き忘れても例外の形は変わらないので、
        409 の `processed_at` が**常に null** になっても誰も気づけない。

        なおこの時刻は **決定を受け付けた時刻**で、副作用の完了時刻ではない
        (遷移は checkpoint 再開の前に書く / PR #430 Codex P1)。
        """
        client = approval_script()

        res = await run_workflow(
            "s-processed-at", "返信して", session_repo, approval_repo, client
        )
        assert (await approval_repo.get(res.approval_request_id)).processed_at is None

        await resume_after_approval(
            res.approval_request_id, True, session_repo, approval_repo, client
        )

        record = await approval_repo.get(res.approval_request_id)
        assert record.status == "approved"
        # ISO 8601 (UTC) として解釈できること。文字列の存在だけを見ると、
        # 表現がローカル時刻や独自形式に変わっても通ってしまう
        assert datetime.fromisoformat(record.processed_at).tzinfo is not None


class _ConcurrentReadApprovalRepository:
    """「2 本が同時に pending を読んだ」状態を決定的に再現する薄い委譲 (PR #430 Codex P1)。

    **pending の窓はテストが明示的に決める**: 先頭 `stale_reads` 回の `get` は
    pending の写しを返し (= 同時に読んだ 2 本がどちらも `resume_after_approval`
    冒頭の status チェックを通過する)、それ以降は実データを返す (= 競合に負けた
    側が現在状態を読み直す経路)。

    窓の閉じ方を「claim が呼ばれたら」にすると、**2 本目が早期チェックで止まり
    claim まで到達しない** (judge 実測: 敗者分岐 0 回)。それでは
    「排他が claim にあるのか早期チェックにあるのか」を区別できず、
    claim を外す変異も checkpoint 解放位置の変異も検出できない。

    `claim_calls` / `lost_claims` は**どの経路を通ったかの実測値**。テストは
    これを assert して「敗者が claim まで来たこと」を証拠として残す。
    """

    def __init__(self, inner, pending, stale_reads: int):
        self._inner = inner
        self._pending = pending
        self._stale_remaining = stale_reads
        self.claim_calls = 0
        self.lost_claims = 0

    async def get(self, approval_id):
        if self._stale_remaining > 0 and approval_id == self._pending.id:
            self._stale_remaining -= 1
            return self._pending.model_copy()
        return await self._inner.get(approval_id)

    async def save(self, record):
        await self._inner.save(record)

    async def claim(self, approval_id, status, processed_at):
        self.claim_calls += 1
        claimed = await self._inner.claim(approval_id, status, processed_at)
        if claimed is None:
            self.lost_claims += 1
        return claimed


class TestConcurrentApproval:
    """同じ承認 ID がほぼ同時に 2 本届いたターン (PR #430 Codex P1)。

    無いと何が静かに通るか: 冒頭の status チェックだけで排他したつもりになると、
    両方が pending を読んだときに**両方が checkpoint 再開へ進み、副作用が二重に
    実行される** (メールが 2 通出る)。承認 UI (G1) が守っているはずの
    「人間が 1 回許可した操作は 1 回だけ起きる」が崩れるが、画面はどちらも
    普通に成功して見えるので、送られた側の証跡が出るまで気づけない。
    """

    async def test_単体_同時に届いた二本のうち副作用を実行するのは一本だけ(
        self, session_repo, approval_repo, caplog
    ):
        client = approval_script()
        res = await run_workflow(
            "s-race", "返信して", session_repo, approval_repo, client
        )
        pending = await approval_repo.get(res.approval_request_id)
        # 2 本とも冒頭の status チェックを通す = **排他は claim だけが持つ**状況
        repo = _ConcurrentReadApprovalRepository(approval_repo, pending, stale_reads=2)

        with caplog.at_level(logging.INFO, logger="app.tools"):
            results = await asyncio.gather(
                resume_after_approval(
                    res.approval_request_id, True, session_repo, repo, client
                ),
                resume_after_approval(
                    res.approval_request_id,
                    True,
                    session_repo,
                    repo,
                    approval_script(),
                ),
                return_exceptions=True,
            )

        # **副作用の実行が 1 回** — ここが 2 になるのがこのテストの本体
        # (原子的な claim を条件なし save に戻すと実測 2 回になる)
        executed = [
            r for r in caplog.records if "Tool[send_reply] invoked" in r.getMessage()
        ]
        assert len(executed) == 1

        # **2 本とも claim まで来て、1 本だけが獲得した** — 排他が「早期 status
        # チェック」ではなく claim にあることの実測。敗者が claim に到達しない形
        # (= 早期チェックで止まる / checkpoint 参照が先に消える) ではここが崩れ、
        # 「解放位置を戻す」ような退行を取り逃がす
        assert repo.claim_calls == 2
        assert repo.lost_claims == 1

        replies = [r for r in results if isinstance(r, str)]
        conflicts = [r for r in results if isinstance(r, ApprovalAlreadyProcessedError)]
        assert len(replies) == 1
        assert len(conflicts) == 1
        # 負けた側は 409 (二重送信) として説明される — 404 (もう無い) に化けない
        assert conflicts[0].status == "approved"
        assert not isinstance(conflicts[0], ValueError)

        # 勝者が解放したので registry は空 (敗者が消したのではない — 下のテスト)
        assert get_pending_checkpoint_storage(res.approval_request_id) is None

    async def test_単体_負けた側は_checkpoint_を解放しない(
        self, session_repo, approval_repo
    ):
        """獲得できなかった側が in-memory registry を掃除してしまわないこと。

        無いと何が静かに通るか: 負けた側が storage を pop すると、**勝った側が
        再開に使っている checkpoint が実行中に消える**。承認は受け付けたのに
        ツールが走らない (しかも 409 は返っているので二重送信として片づく) 状態が
        起きうる。
        """
        client = approval_script()
        res = await run_workflow(
            "s-race-cleanup", "返信して", session_repo, approval_repo, client
        )
        pending = await approval_repo.get(res.approval_request_id)
        # 敗者ぶんの 1 回だけ pending を見せる = 早期チェックを通過して claim で負ける
        repo = _ConcurrentReadApprovalRepository(approval_repo, pending, stale_reads=1)

        # 勝者は既に claim を取っており、checkpoint を使って再開中とする
        # (この時点で registry はまだ解放されていない)
        assert await approval_repo.claim(
            res.approval_request_id, "approved", "2026-08-15T02:00:00+00:00"
        )
        assert get_pending_checkpoint_storage(res.approval_request_id) is not None

        with pytest.raises(ApprovalAlreadyProcessedError):
            await resume_after_approval(
                res.approval_request_id, True, session_repo, repo, client
            )

        # 敗者は claim まで到達して負けた (早期チェックで止まっていない)
        assert repo.claim_calls == 1
        assert repo.lost_claims == 1
        # そのうえで解放していない (勝者の再開が使う checkpoint を消さない)
        assert get_pending_checkpoint_storage(res.approval_request_id) is not None


class TestChainedApproval:
    """承認済みツールの実行後に**別の副作用ツール**が要求されたターン (PR #417 P1)。

    無いと何が静かに通るか: 再開後の応答に含まれる新しい `function_approval_request`
    を検査せずに完了扱いし、**承認レコードも作らず空の reply でターンが閉じる**。
    1 本目の副作用だけ実行済みで、2 本目は承認も実行もされないまま消える。
    """

    async def test_l1_再開後の追加承認要求は新しい承認レコードとして立て直される(
        self, session_repo, approval_repo
    ):
        client = chained_approval_script()

        first = await run_workflow(
            "s-chain", "返信して整理して", session_repo, approval_repo, client
        )
        reply = await resume_after_approval(
            first.approval_request_id, True, session_repo, approval_repo, client
        )

        # 空 reply で完了しない (壊れていたときの実測値は "")
        assert (
            reply
            == "「archive_message」を実行するには承認が必要です。実行してよろしいですか？"
        )

        # 1 本目は解決済み、2 本目は**新しい ID の** pending として立っている
        assert (await approval_repo.get(first.approval_request_id)).status == "approved"
        pending = pending_records(approval_repo)
        assert len(pending) == 1
        follow_up = pending[0]
        assert follow_up.id != first.approval_request_id
        assert follow_up.plan.tool_name == "archive_message"
        assert follow_up.plan.tool_args == ARCHIVE_ARGS
        assert follow_up.checkpoint_id is not None

        # 2 本目は**まだ実行されていない** (承認前に副作用が走っていない)
        history = await session_repo.get("s-chain")
        contents = [m.text for m in history.messages]
        assert not any("Tool result (archive_message)" in c for c in contents)

    async def test_l1_中断前に実行済みツールの結果が履歴に残る(
        self, session_repo, approval_repo
    ):
        # 無いと: 承認要求で打ち切られた応答からは MAF が function_result を落とす
        # ため (実測)、**実行された send_reply が履歴に残らない**。次のターンで
        # モデルが同じ副作用ツールをもう一度呼びうる (二重送信)。
        client = chained_approval_script()

        first = await run_workflow(
            "s-chain-hist", "返信して整理して", session_repo, approval_repo, client
        )
        await resume_after_approval(
            first.approval_request_id, True, session_repo, approval_repo, client
        )

        history = await session_repo.get("s-chain-hist")
        contents = [m.text for m in history.messages]
        assert any(
            "Tool result (send_reply): [stub] Reply sent to a@example.com." in c
            for c in contents
        )
        # 空の assistant 応答を履歴に積まない (中断であって完了ではない)
        assert not any(m.role == "assistant" and m.text == "" for m in history.messages)

    async def test_l1_追加承認を承認すると2本目のツールが実行されて連鎖が閉じる(
        self, session_repo, approval_repo
    ):
        # 無いと: 立て直した承認レコードが checkpoint に繋がっておらず、
        # 「承認したのに何も起きない」ままターンが終わる
        client = chained_approval_script()

        first = await run_workflow(
            "s-chain-done", "返信して整理して", session_repo, approval_repo, client
        )
        await resume_after_approval(
            first.approval_request_id, True, session_repo, approval_repo, client
        )
        follow_up = pending_records(approval_repo)[0]

        reply = await resume_after_approval(
            follow_up.id, True, session_repo, approval_repo, client
        )

        assert reply == "両方やりました。"
        history = await session_repo.get("s-chain-done")
        contents = [m.text for m in history.messages]
        assert any("Tool result (send_reply)" in c for c in contents)
        assert any("Tool result (archive_message)" in c for c in contents)
        assert contents[-1] == "両方やりました。"

    async def test_l1_追加承認へ_checkpoint_storage_が引き継がれる(
        self, session_repo, approval_repo
    ):
        # 無いと (in-memory 構成): 解決時に storage を手放したまま新しい承認だけが
        # 残り、その /approve が必ず「checkpoint not found」になる。
        # あるいは古い ID の参照が解放されずに溜まり続ける (PR #243 指摘)
        client = chained_approval_script()

        first = await run_workflow(
            "s-chain-store", "返信して整理して", session_repo, approval_repo, client
        )
        await resume_after_approval(
            first.approval_request_id, True, session_repo, approval_repo, client
        )
        follow_up = pending_records(approval_repo)[0]

        assert get_pending_checkpoint_storage(first.approval_request_id) is None
        assert get_pending_checkpoint_storage(follow_up.id) is not None

    async def test_l1_1ターンに並んだ承認要求のうち承認していない側は実行されない(
        self, session_repo, approval_repo, caplog
    ):
        # 「先頭のみ + warning」の暫定 (#321 で題材を決めるまで) が**安全側に倒れて
        # いる**ことを pin する。無いと: 2 本目まで実行する実装に変わっても
        # 気づけない = 「承認したつもりのない副作用」が通る。
        # 実測 (#417): 残りは MAF が再開時に黙って捨てるため実行されない。
        client = ScriptedChatClient(
            [
                tool_calls_step(
                    ("send_reply", APPROVAL_ARGS),
                    ("archive_message", ARCHIVE_ARGS),
                ),
                text_step("両方やりました。"),
            ]
        )

        with caplog.at_level(logging.WARNING, logger="app.workflow"):
            res = await run_workflow(
                "s-two", "返信して整理して", session_repo, approval_repo, client
            )
        await resume_after_approval(
            res.approval_request_id, True, session_repo, approval_repo, client
        )

        history = await session_repo.get("s-two")
        contents = [m.text for m in history.messages]
        assert any("Tool result (send_reply)" in c for c in contents)
        assert not any("Tool result (archive_message)" in c for c in contents)
        # 捨てたことがログに残る (静かに落とさない)
        assert any("承認要求が 2 件届いた" in r.getMessage() for r in caplog.records)


class TestResumeFailure:
    """承認を再開した LLM 呼び出しが落ちたターン (judge #417)。

    無いと何が静かに通るか: 再開経路だけ `converse` と違って try/except が無く、
    **承認済みツールは実行されたのに履歴には 1 行も残らない**まま例外が上がる。
    /approve は 500 を返し、ユーザーは同じ依頼をもう一度出す — 履歴に実行の痕跡が
    無いのでモデルは同じ副作用ツールを呼び直す (承認済みメールの二重送信)。
    """

    async def test_l1_承認再開中にLLMが落ちても実行済みツールの記録が履歴に残る(
        self, session_repo, approval_repo
    ):
        # judge の再現手順そのまま: send_reply を承認 → ツールは実行される →
        # その直後の内側 LLM 呼び出しが RuntimeError
        client = ScriptedChatClient(
            [tool_call_step("send_reply", APPROVAL_ARGS), error_step()]
        )

        res = await run_workflow(
            "s-resume-fail", "返信して", session_repo, approval_repo, client
        )

        with pytest.raises(RuntimeError, match="LLM connection lost"):
            await resume_after_approval(
                res.approval_request_id, True, session_repo, approval_repo, client
            )

        history = await session_repo.get("s-resume-fail")
        contents = [m.text for m in history.messages]
        assert any(
            "Tool result (send_reply): [stub] Reply sent to a@example.com." in c
            for c in contents
        )
        # 落ちたターンで assistant 応答は積まない (完了していない)
        assert history.messages[-1].role == "system"


class TestCheckpointCleanup:
    async def test_l1_completed_run_without_approval_retains_no_checkpoints(
        self, session_repo, approval_repo
    ):
        client = ScriptedChatClient([text_step("こんにちは。")])
        before = dict(_pending_run_storages)

        res = await run_workflow(
            "s-clean", "こんにちは", session_repo, approval_repo, client
        )

        assert res.requires_approval is False
        # 承認と無関係な run はプロセス側に checkpoint 参照を一切残さない
        assert _pending_run_storages == before
