"""[L1] extractor の入出力契約・グルーピング分岐・例外パスを pin する。

ここで test しないこと:
- LLM の抽出品質 (困りごとの切り出しの妥当性、テーマ判定の精度) — prompt engineering の領域
- prompt 文面の正しさ — 仕様変更時に false positive を量産する
- session repository 自体の挙動 — それは test_repositories.py
- HTTP / FastAPI 経由の通し挙動 — それは L2 (test_l2_endpoints.py)

fixture 置き換え (M1-5 / #82): SK 依存除去に伴い、履歴 fixture を SK ChatHistory
から app.history.ChatHistory (MAF Message ベース / 追記 API は同形) へ差し替えた。
検証意図は不変。
"""

import json
import logging

import pytest
from app.history import ChatHistory

from app.extractor import ExtractionParseError, ExtractionUnavailable, extract
from app.schemas import ConversationMessage, ExistingProblemRef

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

    async def test_l1_malformed_json_raises_parse_error(
        self, session_repo, make_client
    ):
        # LLM の応答を解釈できなかったら**失敗として上げる** (#183)。
        # 無いと: 空の ExtractionResult が返り、画面には「追跡する困りごとは
        #         見つかりませんでした」と出る。壊れているのに正常終了に見えるため、
        #         ユーザーもログも「0 件だった」と区別できない (最も気づけない壊れ方)。
        await _seed(session_repo)
        client_mock = make_client("not valid json at all")

        with pytest.raises(ExtractionParseError):
            await extract("s1", [], session_repo, client_mock)

    async def test_単体_パース失敗のログに抽出本文を出さない(
        self, session_repo, make_client, caplog
    ):
        # 無いと: 抽出された困りごとの要約 (このプロダクトで最も機微なテキスト) が
        # ERROR ログ経由で Log Analytics に流れ続ける (Issue #313 / rubric S3)。
        # デバッグ可能性は ref + 指紋で残すので、ここは「本文が出ない」だけを見る。
        await _seed(session_repo)
        secret = "妻との関係で限界が来ていて、毎晩眠れない"
        client_mock = make_client(f"not json: {secret}")

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(ExtractionParseError) as excinfo:
                await extract("s1", [], session_repo, client_mock)

        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert secret not in logged
        assert secret not in str(excinfo.value)
        # 追える形は残っている: 同じ ref がログとクライアント向けメッセージの両方にある
        ref = str(excinfo.value).split("ref: ")[1].rstrip(")")
        assert ref in logged
        assert "hmac=" in logged  # 指紋はプロセス鍵つき HMAC (PR #324 P2)

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

    async def test_l1_missing_session_and_no_messages_raises_unavailable(
        self, session_repo, make_client
    ):
        # 履歴も会話も無ければ材料が無い (endpoint 側で 404 に mapping)。
        client_mock = make_client(json.dumps(_NEW_PAYLOAD))

        with pytest.raises(ExtractionUnavailable):
            await extract("nonexistent", [], session_repo, client_mock)

    async def test_l1_uses_given_messages_without_session_history(
        self, session_repo, make_client
    ):
        # 会話が渡されていれば履歴が無くても抽出できる (#183)。
        # 無いと: このサービスのプロセスメモリ頼みに戻り、scale-to-zero・スケールアウト・
        #         リビジョン差し替えのいずれでも「対話はできたのに抽出だけ 404」になる。
        client_mock = make_client(json.dumps(_NEW_PAYLOAD))
        messages = [
            ConversationMessage(role="assistant", text="どうしましたか"),
            ConversationMessage(role="user", text="転職しようか迷ってて"),
        ]

        result = await extract("nonexistent", [], session_repo, client_mock, messages)

        assert len(result.items) == 1
        assert result.items[0].grouping.kind == "new"

    async def test_l1_given_messages_reach_the_prompt(self, session_repo, make_client):
        # 渡した会話が実際にプロンプトへ載ることを確認する。
        # 無いと: 会話を受け取るのに使わない実装 (履歴を見に行ったまま) が緑で通る。
        client_mock = make_client(json.dumps(_NEW_PAYLOAD))
        messages = [ConversationMessage(role="user", text="眠れない日が続いている")]

        await extract("s-any", [], session_repo, client_mock, messages)

        # complete() は Message(role="user", contents=[prompt]) の 1 通で呼ぶ
        sent_messages = client_mock.get_response.call_args.args[0]
        prompt = str(sent_messages[0].contents[0])
        assert "眠れない日が続いている" in prompt

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


# ---- 思考整理マップ (#433 段階 1) --------------------------------------------

_MAP_PAYLOAD = {
    "mentions": [],
    "thinkingMap": {
        "nodes": [
            {
                "id": "n1",
                "kind": "topic",
                "label": "転職の不安",
                "status": "confirmed",
                "parentId": None,
            },
            {
                "id": "n2",
                "kind": "hypothesis",
                "label": "失敗が怖い",
                "status": "tentative",
                "parentId": "n1",
            },
            {
                "id": "n3",
                "kind": "unknown",
                "label": "上司との関係?",
                "status": "unexplored",
                "parentId": "n1",
            },
        ]
    },
}


class TestThinkingMap:
    async def test_l1_maps_thinking_map_nodes(self, session_repo, make_client):
        # kind × status の 2 軸と親子が ExtractionResult まで届くことを pin する (#433 裁定 4)。
        # 無いと: 右ペインの「AI の整理」が常に空になる / 2 軸のどちらかが落ちても
        #         画面は「整理はできている」ように見えるので気づけない。
        await _seed(session_repo)
        client_mock = make_client(json.dumps(_MAP_PAYLOAD))

        result = await extract("s1", [], session_repo, client_mock)

        assert result.thinking_map is not None
        nodes = result.thinking_map.nodes
        assert [n.id for n in nodes] == ["n1", "n2", "n3"]
        assert [n.kind for n in nodes] == ["topic", "hypothesis", "unknown"]
        assert [n.status for n in nodes] == ["confirmed", "tentative", "unexplored"]
        assert [n.parent_id for n in nodes] == [None, "n1", "n1"]

    async def test_l1_thinking_map_rides_on_the_same_llm_call(
        self, session_repo, make_client
    ):
        # 追加の LLM 呼び出し 0 (#433 裁定 2) を pin する。
        # 無いと: マップ用にもう 1 回 LLM を呼ぶ実装 (入力トークンの二重払い) が
        #         テスト緑のまま入り込む — 出力は同じなので結果からは区別できない。
        await _seed(session_repo)
        client_mock = make_client(json.dumps(_MAP_PAYLOAD))

        await extract("s1", [], session_repo, client_mock)

        assert client_mock.get_response.call_count == 1

    async def test_l1_problem_id_is_always_null(self, session_repo, make_client):
        # #321 まで Problem とは紐づけない (裁定 4 — 接続穴だけ空ける)。
        # 無いと: LLM が推測した Problem id がそのまま画面に出て、
        #         「AI が既存の悩みと紐づけた」と誤読される (実際は当てずっぽう)。
        await _seed(session_repo)
        payload = json.loads(json.dumps(_MAP_PAYLOAD))
        payload["thinkingMap"]["nodes"][0]["problemId"] = "prob-existing"
        client_mock = make_client(json.dumps(payload))

        result = await extract("s1", [], session_repo, client_mock)

        assert [n.problem_id for n in result.thinking_map.nodes] == [None, None, None]

    async def test_l1_unknown_kind_and_status_fall_back(
        self, session_repo, make_client
    ):
        # 未知の値は kind=topic / status=tentative に丸める。
        # 無いと: LLM の書き損じ (status: "done" など) が unexplored / confirmed に化けて、
        #         画面に出る実数 (未探索の枝の数・確定の数) が静かに動く。
        await _seed(session_repo)
        payload = json.loads(json.dumps(_MAP_PAYLOAD))
        payload["thinkingMap"]["nodes"][0]["kind"] = "conclusion"
        payload["thinkingMap"]["nodes"][0]["status"] = "done"
        client_mock = make_client(json.dumps(payload))

        result = await extract("s1", [], session_repo, client_mock)

        assert result.thinking_map.nodes[0].kind == "topic"
        assert result.thinking_map.nodes[0].status == "tentative"

    async def test_l1_dangling_and_cyclic_parents_become_roots(
        self, session_repo, make_client
    ):
        # 存在しない親・循環した親は根に落とす (節は捨てない)。
        # 無いと: 循環 (a→b→a) でツリー描画が無限再帰し、存在しない親を指す節は
        #         どこにもぶら下がれず画面から消える (数と表示が食い違う)。
        await _seed(session_repo)
        payload = {
            "mentions": [],
            "thinkingMap": {
                "nodes": [
                    {
                        "id": "a",
                        "kind": "topic",
                        "label": "A",
                        "status": "confirmed",
                        "parentId": "b",
                    },
                    {
                        "id": "b",
                        "kind": "topic",
                        "label": "B",
                        "status": "confirmed",
                        "parentId": "a",
                    },
                    {
                        "id": "c",
                        "kind": "topic",
                        "label": "C",
                        "status": "confirmed",
                        "parentId": "zzz",
                    },
                    {
                        "id": "d",
                        "kind": "topic",
                        "label": "D",
                        "status": "confirmed",
                        "parentId": "d",
                    },
                ]
            },
        }
        client_mock = make_client(json.dumps(payload))

        result = await extract("s1", [], session_repo, client_mock)

        parents = {n.id: n.parent_id for n in result.thinking_map.nodes}
        assert parents == {"a": None, "b": None, "c": None, "d": None}

    async def test_l1_empty_label_nodes_are_dropped(self, session_repo, make_client):
        # label が無い節は画面に何も伝えないので捨てる。
        # 無いと: 空行が実数 (話題 N) に数えられ、「数えただけ」という誠実さが崩れる。
        await _seed(session_repo)
        payload = json.loads(json.dumps(_MAP_PAYLOAD))
        payload["thinkingMap"]["nodes"][1]["label"] = "   "
        client_mock = make_client(json.dumps(payload))

        result = await extract("s1", [], session_repo, client_mock)

        assert [n.id for n in result.thinking_map.nodes] == ["n1", "n3"]

    async def test_l1_non_string_labels_are_dropped(self, session_repo, make_client):
        # label が文字列でない節も捨てる (Codex P2 / PR #444)。
        # 無いと: `str(None)` = "None" / `str({})` = "{}" のような**非空文字列**に化けて
        #         空ラベル判定をすり抜け、検証できない値が「話題」として実数に加算され
        #         画面にも 1 行出る (数えただけ、という誠実さが崩れる)。
        await _seed(session_repo)
        payload = json.loads(json.dumps(_MAP_PAYLOAD))
        payload["thinkingMap"]["nodes"][0]["label"] = None
        payload["thinkingMap"]["nodes"][1]["label"] = {"text": "オブジェクト"}
        client_mock = make_client(json.dumps(payload))

        result = await extract("s1", [], session_repo, client_mock)

        assert [n.id for n in result.thinking_map.nodes] == ["n3"]

    async def test_l1_non_string_id_falls_back_to_generated_id(
        self, session_repo, make_client
    ):
        # id が文字列でなければ採番し直す (親の参照先が壊れた文字列になるのを防ぐ)。
        # 無いと: `str({...})` のような id が親子の突き合わせに使われ、
        #         親子関係が黙って壊れる (節は出るのでツリーが平らになるだけ)。
        await _seed(session_repo)
        payload = json.loads(json.dumps(_MAP_PAYLOAD))
        payload["thinkingMap"]["nodes"][0]["id"] = None
        client_mock = make_client(json.dumps(payload))

        result = await extract("s1", [], session_repo, client_mock)

        assert result.thinking_map.nodes[0].id == "node-0"

    async def test_l1_missing_thinking_map_is_none(self, session_repo, make_client):
        # マップを返さない LLM 応答でも抽出は成立する (旧プロンプト / 応答の欠落)。
        # 無いと: マップが無いだけで抽出全体が落ち、右ペインの下書きまで消える。
        await _seed(session_repo)
        client_mock = make_client(json.dumps(_NEW_PAYLOAD))

        result = await extract("s1", [], session_repo, client_mock)

        assert result.thinking_map is None
        assert len(result.items) == 1

    async def test_l1_thinking_map_is_capped(self, session_repo, make_client):
        # 節の数に上限を置く (画面とトークンの締め)。
        # 無いと: LLM が 100 節返したときに右ペインが際限なく伸びる。
        await _seed(session_repo)
        payload = {
            "mentions": [],
            "thinkingMap": {
                "nodes": [
                    {
                        "id": f"n{i}",
                        "kind": "topic",
                        "label": f"話題{i}",
                        "status": "confirmed",
                        "parentId": None,
                    }
                    for i in range(40)
                ]
            },
        }
        client_mock = make_client(json.dumps(payload))

        result = await extract("s1", [], session_repo, client_mock)

        assert len(result.thinking_map.nodes) == 24

    async def test_l1_thinking_map_prompt_asks_for_the_two_axes(
        self, session_repo, make_client
    ):
        # 2 軸をプロンプトで求めていることだけ確認する (文面ではなく契約語彙の存在)。
        # 無いと: 契約 (schemas.py) にだけ kind/status があり、LLM には一言も求めていない
        #         実装が緑で通る — マップは永久に空のまま。
        await _seed(session_repo)
        client_mock = make_client(json.dumps(_MAP_PAYLOAD))

        await extract("s1", [], session_repo, client_mock)

        prompt = str(client_mock.get_response.call_args.args[0][0].contents[0])
        assert "thinkingMap" in prompt
        for token in (
            "topic",
            "hypothesis",
            "unknown",
            "confirmed",
            "tentative",
            "unexplored",
        ):
            assert token in prompt
