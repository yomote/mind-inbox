"""[L1] extractor の入出力契約・グルーピング分岐・例外パスを pin する。

ここで test しないこと:
- LLM の抽出品質 (困りごとの切り出しの妥当性、テーマ判定の精度) — prompt engineering の領域
- prompt 文面の正しさ — 仕様変更時に false positive を量産する
- session repository 自体の挙動 — それは test_repositories.py
- HTTP / FastAPI 経由の通し挙動 — それは L2 (test_l2_endpoints.py)
"""

import json

import pytest
from semantic_kernel.contents import ChatHistory

from app.extractor import extract
from app.schemas import ExistingProblemRef

_NEW_PAYLOAD = {
    "mentions": [
        {
            "statement": "転職すべきか迷っている",
            "excerpt": "転職しようか迷ってて",
            "affect": {"label": "不安", "valence": "negative", "intensity": 0.6},
            "theme": "仕事・キャリア",
            "tags": ["転職"],
            "grouping": {
                "existingProblemId": None,
                "newProblemTitle": "転職の迷い",
                "confidence": 0.8,
            },
        }
    ]
}


async def _seed(session_repo, session_id="s1"):
    history = ChatHistory()
    history.add_user_message("転職しようか迷ってて、あと最近眠れない")
    await session_repo.save(session_id, history)


class TestExtractNew:
    async def test_l1_maps_new_problem_to_extraction_result(
        self, session_repo, make_client
    ):
        # 新規 Problem を起こす分岐の各フィールド mapping を pin する。
        # 無いと: schema フィールドのリネーム/型変更や problem_id の紐付け欠落が静かに通り、
        #         BFF 側で deserialize / グルーピング表示が壊れる
        await _seed(session_repo)
        client_mock = make_client(json.dumps(_NEW_PAYLOAD))

        result = await extract("s1", [], session_repo, client_mock)

        assert len(result.items) == 1
        item = result.items[0]
        assert item.grouping.kind == "new"
        assert item.grouping.is_recurrence is False
        assert item.grouping.mention_count == 1
        assert item.grouping.reignited is False
        assert item.grouping.problem_title == "転職の迷い"
        assert item.grouping.problem_theme == "仕事・キャリア"
        # mention が grouping の problem_id と紐づいていること
        assert item.mention.problem_id == item.grouping.problem_id
        assert item.mention.proposed_theme == "仕事・キャリア"
        assert item.mention.proposed_tags == ["転職"]
        assert item.mention.affect.valence == "negative"
        assert item.mention.dump_id == "s1"
        # 新規は既存との類似度が無いため grouping_confidence は null (domain.ts 契約)
        assert item.grouping.grouping_confidence is None
        assert item.mention.grouping_confidence is None
        assert result.new_problem_count == 1
        assert result.updated_problem_count == 0

    async def test_l1_new_problem_title_falls_back_to_statement(
        self, session_repo, make_client
    ):
        # newProblemTitle が空なら statement をタイトルに使う契約を pin する。
        # 無いと: 新規 Problem のタイトルが空文字のまま一覧に並ぶ退行が静かに通る
        await _seed(session_repo)
        payload = json.loads(json.dumps(_NEW_PAYLOAD))
        payload["mentions"][0]["grouping"]["newProblemTitle"] = ""
        client_mock = make_client(json.dumps(payload))

        result = await extract("s1", [], session_repo, client_mock)

        assert result.items[0].grouping.problem_title == "転職すべきか迷っている"


class TestExtractExisting:
    async def test_l1_groups_into_existing_and_flags_recurrence(
        self, session_repo, make_client
    ):
        # 既存 Problem への再出現分岐: is_recurrence / mention_count 加算 / updated 集計を pin する。
        # 無いと: 再出現が常に新規として起き、「ためる」体験 (重複束ね) が壊れる
        await _seed(session_repo)
        existing = [
            ExistingProblemRef(
                id="p1",
                title="睡眠不足",
                theme="心と体",
                mention_count=2,
                status="open",
            )
        ]
        payload = {
            "mentions": [
                {
                    "statement": "また眠れない",
                    "excerpt": "最近眠れなくて",
                    "affect": {
                        "label": "疲労",
                        "valence": "negative",
                        "intensity": 0.5,
                    },
                    "theme": "心と体",
                    "tags": ["睡眠"],
                    "grouping": {
                        "existingProblemId": "p1",
                        "newProblemTitle": "",
                        "confidence": 0.9,
                    },
                }
            ]
        }
        client_mock = make_client(json.dumps(payload))

        result = await extract("s1", existing, session_repo, client_mock)

        item = result.items[0]
        assert item.grouping.kind == "existing"
        assert item.grouping.problem_id == "p1"
        assert item.grouping.problem_title == "睡眠不足"
        assert item.grouping.is_recurrence is True
        assert item.grouping.mention_count == 3  # 既存 2 + 今回 1
        assert item.grouping.reignited is False  # status == open
        # 既存への寄せは類似度スコアを保持する (new と対照的に非 null)
        assert item.grouping.grouping_confidence == 0.9
        assert item.mention.grouping_confidence == 0.9
        assert result.new_problem_count == 0
        assert result.updated_problem_count == 1

    async def test_l1_accumulates_mention_count_within_same_dump(
        self, session_repo, make_client
    ):
        # 同一 Dump 内で複数 Mention が同じ既存 Problem に寄る時、mention_count を累積する契約を pin する。
        # 無いと: 2件目以降も ref.mention_count + 1 のまま (過小カウント) になり「今月N回目」表示が狂う
        await _seed(session_repo)
        existing = [
            ExistingProblemRef(
                id="p1",
                title="睡眠不足",
                theme="心と体",
                mention_count=2,
                status="open",
            )
        ]
        payload = {
            "mentions": [
                {
                    "statement": "また眠れない",
                    "excerpt": "眠れなくて",
                    "affect": {
                        "label": "疲労",
                        "valence": "negative",
                        "intensity": 0.5,
                    },
                    "theme": "心と体",
                    "tags": [],
                    "grouping": {"existingProblemId": "p1", "confidence": 0.9},
                },
                {
                    "statement": "夜中に何度も目が覚める",
                    "excerpt": "何度も目が覚める",
                    "affect": {
                        "label": "疲労",
                        "valence": "negative",
                        "intensity": 0.4,
                    },
                    "theme": "心と体",
                    "tags": [],
                    "grouping": {"existingProblemId": "p1", "confidence": 0.8},
                },
            ]
        }
        client_mock = make_client(json.dumps(payload))

        result = await extract("s1", existing, session_repo, client_mock)

        counts = [item.grouping.mention_count for item in result.items]
        assert counts == [3, 4]  # 既存 2 → 3 → 4 と累積
        assert result.updated_problem_count == 1  # 同じ Problem なので 1
        assert result.new_problem_count == 0

    async def test_l1_reignited_when_existing_problem_not_open(
        self, session_repo, make_client
    ):
        # 棚卸し済み / 解決済みの Problem に再出現したら reignited=True を pin する。
        # 無いと: 再燃 (解決したはずの悩みがぶり返した) の気づきが UI に出せない退行が静かに通る
        await _seed(session_repo)
        existing = [
            ExistingProblemRef(
                id="p1",
                title="睡眠不足",
                theme="心と体",
                mention_count=1,
                status="shelved",
            )
        ]
        payload = {
            "mentions": [
                {
                    "statement": "また眠れない",
                    "excerpt": "眠れない",
                    "affect": {
                        "label": "疲労",
                        "valence": "negative",
                        "intensity": 0.5,
                    },
                    "theme": "心と体",
                    "tags": [],
                    "grouping": {"existingProblemId": "p1", "confidence": 0.7},
                }
            ]
        }
        client_mock = make_client(json.dumps(payload))

        result = await extract("s1", existing, session_repo, client_mock)

        assert result.items[0].grouping.reignited is True

    async def test_l1_unknown_existing_id_is_treated_as_new(
        self, session_repo, make_client
    ):
        # LLM が候補に無い id を返したら dangling 参照を作らず新規に丸める契約を pin する。
        # 無いと: 存在しない problemId を指す Mention が生まれ、詳細表示が壊れる
        await _seed(session_repo)
        existing = [
            ExistingProblemRef(id="p1", title="睡眠不足", theme="心と体", status="open")
        ]
        payload = json.loads(json.dumps(_NEW_PAYLOAD))
        payload["mentions"][0]["grouping"]["existingProblemId"] = "p-does-not-exist"
        client_mock = make_client(json.dumps(payload))

        result = await extract("s1", existing, session_repo, client_mock)

        assert result.items[0].grouping.kind == "new"
        assert result.new_problem_count == 1
        assert result.updated_problem_count == 0


class TestExtractRobustness:
    async def test_l1_json_inside_markdown_fence(self, session_repo, make_client):
        # organizer と同じく ```json フェンス剥がしが効くことを pin する。
        await _seed(session_repo)
        fenced = f"```json\n{json.dumps(_NEW_PAYLOAD)}\n```"
        client_mock = make_client(fenced)

        result = await extract("s1", [], session_repo, client_mock)

        assert len(result.items) == 1
        assert result.items[0].grouping.kind == "new"

    async def test_l1_malformed_json_returns_empty_result(
        self, session_repo, make_client
    ):
        # LLM が JSON でない文字列を返した時、例外を投げず空 ExtractionResult を返す契約を pin する。
        # 無いと: parse failure 時に 500 で落ち、吐き出し直後の抽出画面が汎用エラーになる
        await _seed(session_repo)
        client_mock = make_client("not valid json at all")

        result = await extract("s1", [], session_repo, client_mock)

        assert result.items == []
        assert result.new_problem_count == 0
        assert result.updated_problem_count == 0

    async def test_l1_unknown_theme_falls_back_to_michubunrui(
        self, session_repo, make_client
    ):
        # 固定分類外のテーマは「未分類」に丸める契約を pin する。
        # 無いと: 想定外テーマがそのまま流れ、Theme enum (domain.ts) と乖離して BFF で弾かれる
        await _seed(session_repo)
        payload = json.loads(json.dumps(_NEW_PAYLOAD))
        payload["mentions"][0]["theme"] = "宇宙開発"
        client_mock = make_client(json.dumps(payload))

        result = await extract("s1", [], session_repo, client_mock)

        assert result.items[0].mention.proposed_theme == "未分類"
        assert result.items[0].grouping.problem_theme == "未分類"

    async def test_l1_missing_session_raises_value_error(
        self, session_repo, make_client
    ):
        # 存在しない session は ValueError (endpoint 側で 404 に mapping)。
        client_mock = make_client(json.dumps(_NEW_PAYLOAD))

        with pytest.raises(ValueError, match="Session not found"):
            await extract("nonexistent", [], session_repo, client_mock)

    async def test_l1_empty_mentions_returns_empty_result(
        self, session_repo, make_client
    ):
        # 困りごとが無い吐き出し (mentions 空配列) を正常に空結果として返す。
        # 無いと: 0 件抽出が異常系に落ちる退行が静かに通る
        await _seed(session_repo)
        client_mock = make_client(json.dumps({"mentions": []}))

        result = await extract("s1", [], session_repo, client_mock)

        assert result.items == []
        assert result.new_problem_count == 0
