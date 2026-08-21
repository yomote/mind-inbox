"""[単体] 会話履歴の窓 (`select_window`) の判定を pin する (#486)。

無いと何が静かに通るか:
- **上限が効かなくなる** — 窓の計算がどこかで壊れても全量が返り、TTL 7 日の間に
  育ったセッションが context 超過で恒久 500 になるまで誰も気づかない (症状が出るのは
  「よく使っている人のセッションだけ」なので、開発中の短い会話では絶対に踏まない)
- **ターン境界を割る** — ツール結果 (system) や assistant 応答だけが残り、対応する
  user 発言が消える。LLM には「誰も聞いていない答え」が並んだ文脈が渡り、応答が
  静かに劣化する (例外は出ない)
- **先頭の system プロンプトが落ちる** — エージェントの人格・制約ごと入れ替わる
- **最新ターンが落ちる** — 予算より大きい 1 ターンを投げた瞬間、いま答えるべき
  user 発言が消えた文脈で LLM が呼ばれる

仕様の出どころ: Issue #486 の design-gate 承認コメント (2026-08-17)。
「ターン境界で切る / system プロンプトは窓の外で常時保持 / 件数 + 文字数の二本立て」。
"""

from agent_framework import Message
from hypothesis import given, settings
from hypothesis import strategies as st

from app.history import ChatHistory, select_window

SYSTEM_PROMPT = "あなたは Mind Inbox のアシスタントです。"


def _msg(role: str, text: str) -> Message:
    return Message(role=role, contents=[text])


def _turn(index: int, *, tool_results: int = 0) -> list[Message]:
    """user 発言 + ツール結果 (system) + assistant 応答 の 1 ターン。"""
    return [
        _msg("user", f"user-{index}"),
        *[
            _msg("system", f"Tool result (t{index}-{i}): ok")
            for i in range(tool_results)
        ],
        _msg("assistant", f"assistant-{index}"),
    ]


def _history(turns: int, *, tool_results: int = 0) -> list[Message]:
    messages = [_msg("system", SYSTEM_PROMPT)]
    for index in range(turns):
        messages.extend(_turn(index, tool_results=tool_results))
    return messages


def _texts(messages: list[Message]) -> list[str]:
    return [m.text for m in messages]


class TestWindowLimits:
    def test_単体_件数の上限を超えたら古いターンから落ちる(self) -> None:
        messages = _history(10)  # system + 10 ターン x 2 通 = 21 通

        window = select_window(messages, max_messages=7, max_chars=10**6)

        # system 1 + 3 ターン (6 通) = 7 通。4 ターン目を足すと 9 通で超える
        assert len(window) == 7
        assert _texts(window) == [
            SYSTEM_PROMPT,
            "user-7",
            "assistant-7",
            "user-8",
            "assistant-8",
            "user-9",
            "assistant-9",
        ]

    def test_単体_文字数の予算を超えたら古いターンから落ちる(self) -> None:
        messages = [
            _msg("system", "s" * 10),
            *[_msg("user", "u" * 20), _msg("assistant", "a" * 20)],
            *[_msg("user", "U" * 20), _msg("assistant", "A" * 20)],
            *[_msg("user", "x" * 20), _msg("assistant", "y" * 20)],
        ]

        # 予算 100 = system 10 + 2 ターン (40 x 2)。3 ターン目を足すと 130 で超える
        window = select_window(messages, max_messages=10**6, max_chars=100)

        assert len(window) == 5
        assert _texts(window)[0] == "s" * 10
        assert _texts(window)[1] == "U" * 20

    def test_単体_保持する_system_プロンプトも文字数予算に数える(self) -> None:
        """無いと: 巨大な system プロンプトを入れた瞬間、宣言した上限を静かに超える。"""
        long_prompt = "s" * 90
        messages = [
            _msg("system", long_prompt),
            _msg("user", "u" * 20),
            _msg("assistant", "a" * 20),
            _msg("user", "x" * 5),
            _msg("assistant", "y" * 5),
        ]

        window = select_window(messages, max_messages=10**6, max_chars=100)

        # 予算 100 のうち 90 は system が食う。残り 10 に入るのは最新ターン (10) だけ
        assert _texts(window) == [long_prompt, "x" * 5, "y" * 5]

    def test_単体_件数と文字数は両方が同時に効く(self) -> None:
        """無いと: 片方の判定を落としても、もう片方が緩い限りテストが緑のまま通る。"""
        messages = _history(10)

        by_count = select_window(messages, max_messages=5, max_chars=10**6)
        by_chars = select_window(messages, max_messages=10**6, max_chars=40)
        both = select_window(messages, max_messages=5, max_chars=40)

        # 厳しい方に合わせて切れる (どちらか一方だけを見ていたら both が緩くなる)
        assert len(both) == min(len(by_count), len(by_chars))


class TestTurnBoundary:
    def test_単体_ツール結果を_user_発言から切り離さない(self) -> None:
        messages = _history(
            4, tool_results=2
        )  # 1 ターン = user + system x2 + assistant

        window = select_window(messages, max_messages=9, max_chars=10**6)

        # system 1 + 2 ターン (4 通 x 2) = 9 通。ターンは丸ごと入るか丸ごと落ちる
        assert _texts(window) == [
            SYSTEM_PROMPT,
            "user-2",
            "Tool result (t2-0): ok",
            "Tool result (t2-1): ok",
            "assistant-2",
            "user-3",
            "Tool result (t3-0): ok",
            "Tool result (t3-1): ok",
            "assistant-3",
        ]

    def test_単体_ターンの途中で切れる件数でも半端なターンを残さない(self) -> None:
        """無いと: 「新しい方から N 通」の実装に戻っても、上の件数テストだけなら緑で通る。"""
        messages = _history(4, tool_results=2)

        # 8 通 = system 1 + 1 ターン (4) までしか入らない (2 ターン目は 4 通で溢れる)
        window = select_window(messages, max_messages=8, max_chars=10**6)

        assert _texts(window) == [
            SYSTEM_PROMPT,
            "user-3",
            "Tool result (t3-0): ok",
            "Tool result (t3-1): ok",
            "assistant-3",
        ]

    def test_単体_ツール結果の_system_は保持対象ではなくターンと一緒に落ちる(
        self,
    ) -> None:
        """無いと: 「system は全部残す」実装が通り、ツール結果が無限に溜まる
        (履歴の増加分の大半がツール結果になるので、窓がほぼ効かなくなる)。"""
        messages = _history(6, tool_results=3)

        window = select_window(messages, max_messages=6, max_chars=10**6)

        old_tool_results = [
            t for t in _texts(window) if t.startswith("Tool result (t0")
        ]
        assert old_tool_results == []
        assert _texts(window).count(SYSTEM_PROMPT) == 1


class TestAlwaysKept:
    def test_単体_先頭の_system_プロンプトは予算に関係なく残る(self) -> None:
        messages = _history(5)

        window = select_window(messages, max_messages=1, max_chars=1)

        assert window[0].text == SYSTEM_PROMPT

    def test_単体_最新ターンは予算を超えても落とさない(self) -> None:
        """無いと: 長い 1 通を投げた瞬間、いま答えるべき user 発言ごと消えた文脈で
        LLM が呼ばれる (エラーにならず、噛み合わない応答が返る)。"""
        messages = [
            _msg("system", SYSTEM_PROMPT),
            _msg("user", "古い質問"),
            _msg("assistant", "古い答え"),
            _msg("user", "x" * 5000),
        ]

        window = select_window(messages, max_messages=2, max_chars=10)

        assert _texts(window) == [SYSTEM_PROMPT, "x" * 5000]

    def test_単体_空の履歴は空のまま返す(self) -> None:
        assert select_window([], max_messages=10, max_chars=100) == []


class TestPureness:
    def test_単体_入力の列を書き換えない(self) -> None:
        """無いと: 純粋関数のつもりが呼び出し側の履歴を破壊し、
        「LLM に渡す窓」と「保存する履歴」が同じ操作になってしまう。"""
        messages = _history(5)
        before = _texts(messages)

        select_window(messages, max_messages=3, max_chars=10)

        assert _texts(messages) == before


class TestWindowProperty:
    @settings(max_examples=200, deadline=None)
    @given(
        turns=st.lists(
            st.tuples(
                st.integers(min_value=1, max_value=30),  # user 発言の長さ
                st.integers(min_value=0, max_value=3),  # ツール結果の数
            ),
            min_size=0,
            max_size=20,
        ),
        max_messages=st.integers(min_value=1, max_value=30),
        max_chars=st.integers(min_value=1, max_value=400),
    )
    def test_単体_性質_窓は常に上限内かつ末尾一致で系列の部分列(
        self, turns, max_messages, max_chars
    ) -> None:
        """性質 3 つを同時に見る (§3 の「例ではなく性質で書く」):

        1. 返り値は上限内 — **最新ターンだけは例外** (落とすと会話が壊れるため)
        2. 返り値は元の列の**末尾側**を保った部分列 (順序を入れ替えない / 作らない)
        3. 一番新しいメッセージは必ず残る
        """
        messages = [_msg("system", SYSTEM_PROMPT)]
        for index, (length, tool_results) in enumerate(turns):
            messages.append(_msg("user", f"{index}" + "u" * length))
            for i in range(tool_results):
                messages.append(_msg("system", f"Tool result (t{index}-{i}): ok"))
            messages.append(_msg("assistant", f"{index}-a"))

        window = select_window(messages, max_messages=max_messages, max_chars=max_chars)

        # 2: 「先頭の system プロンプト 1 通 + 末尾から連続した並び」であること
        assert window[0] == messages[0]
        tail_length = len(window) - 1
        if tail_length:
            assert window[1:] == messages[len(messages) - tail_length :]

        # 3: 一番新しいメッセージは必ず残る
        assert window[-1] == messages[-1]

        # 1: 最新ターン (最後の user 以降) を除いた分は必ず上限内
        last_user = max(
            (i for i, m in enumerate(window) if m.role == "user"), default=None
        )
        if last_user is not None and last_user > 1:
            # 最新ターンより前が残っている = 予算に収まって残ったということ
            assert len(window) <= max_messages
            assert sum(len(m.text) for m in window) <= max_chars


class TestPrune:
    def test_単体_prune_は_select_window_と同じ判定で履歴を刈る(self) -> None:
        """無いと: 保存側だけ別判定になり、「LLM に渡ったものと保存されたもの」が
        食い違う (次ターンの文脈が保存時にさらに削れていても気づけない)。"""
        messages = _history(8)
        history = ChatHistory(messages)

        history.prune(max_messages=5, max_chars=10**6)

        assert history.messages == select_window(
            messages, max_messages=5, max_chars=10**6
        )
        assert len(history.messages) == 5
