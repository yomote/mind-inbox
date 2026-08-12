"""[L1] agents.py の既定オプション分岐 + 外向き呼び出しのタイムアウト。

無いと: 推論モデル (gpt-5 系) に temperature / max_tokens を送って 400 になる
退行、または通常モデルから既定パラメータが消える退行が静かに通る (#55)。

タイムアウト (Issue #313) が無いと: 応答しない上流に張り付いた 1 本のリクエストが
ACA のワーカー (maxReplicas=3 / cpu 0.5) と Azure OpenAI の TPM 枠を占有し続ける。
上流 (Functions) が 230s で切っても下流は走り続けるので、**誰も待っていない
トークンを燃やし、正規ユーザーが締め出される** — しかも例外は出ないので静か。
"""

import asyncio

import pytest

from app.agents import _build_client, _options_for_model, chat_stream, complete
from app.config import get_settings


class TestOptionsForModel:
    # OpenAIChatOptions は TypedDict (実体は dict) なのでキーの有無で検証する

    def test_l1_standard_model_gets_default_params(self):
        opts = _options_for_model("gpt-4o")
        assert opts.get("temperature") == 0.7
        assert opts.get("max_tokens") == 1024

    def test_l1_reasoning_model_omits_unsupported_params(self):
        for model in ("gpt-5-mini", "GPT-5", "o1-preview", "o3-mini"):
            opts = _options_for_model(model)
            assert "temperature" not in opts, model
            assert "max_tokens" not in opts, model

    def test_l1_none_model_falls_back_to_defaults(self):
        opts = _options_for_model(None)
        assert opts.get("temperature") == 0.7
        assert opts.get("max_tokens") == 1024


@pytest.fixture
def short_timeouts(monkeypatch):
    """タイムアウトを実測可能な短さに置き換える (値そのものは config.py が正典)。"""
    monkeypatch.setenv("LLM_TOTAL_TIMEOUT_SECONDS", "0.05")
    monkeypatch.setenv("LLM_STREAM_IDLE_TIMEOUT_SECONDS", "0.05")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class HangingChatClient:
    """応答しない上流 (接続は生きているが返ってこない) を模す。"""

    def get_response(self, messages, *, stream=False, options=None, **kwargs):
        if stream:
            return self._stream()
        return self._hang()

    async def _hang(self):
        await asyncio.sleep(30)

    async def _stream(self):
        await asyncio.sleep(30)
        yield  # pragma: no cover — ここへは到達しない


class TestTimeouts:
    async def test_単体_応答しないLLM呼び出しは制限時間で打ち切られる(
        self, short_timeouts
    ):
        with pytest.raises(TimeoutError):
            await complete(HangingChatClient(), "テスト")

    async def test_単体_無音のストリームは制限時間で打ち切られる(self, short_timeouts):
        with pytest.raises(TimeoutError):
            async for _ in chat_stream(HangingChatClient(), []):
                pass

    def test_単体_外向きHTTPクライアントに1試行あたりの上限が入る(self, monkeypatch):
        # 無いと: MAF 側の API 変更で timeout の差し込みが外れても誰も気づかず、
        # OpenAI SDK 既定の 600s に戻る (詰まった 1 本が 10 分ワーカーを占有する)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        get_settings.cache_clear()
        try:
            client = _build_client()
            assert client.client.timeout == get_settings().llm_request_timeout_seconds
        finally:
            get_settings.cache_clear()
