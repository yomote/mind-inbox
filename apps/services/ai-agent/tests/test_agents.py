"""[L1] agents.py の既定オプション分岐。

無いと: 推論モデル (gpt-5 系) に temperature / max_tokens を送って 400 になる
退行、または通常モデルから既定パラメータが消える退行が静かに通る (#55)。
"""

from app.agents import _options_for_model


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
