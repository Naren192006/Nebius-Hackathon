from __future__ import annotations

from types import SimpleNamespace

from app import config, llm


def test_strip_think_blocks_removes_complete_and_unclosed_blocks():
    assert llm.strip_think_blocks("<think>secret</think>Hello") == "Hello"
    assert llm.strip_think_blocks("<THINKING>hmm</THINKING>\nAnswer") == "Answer"
    assert llm.strip_think_blocks("Answer first<think>trailing reasoning") == "Answer first"
    assert llm.strip_think_blocks("no tags <here>") == "no tags <here>"


def test_cost_uses_per_million_prices():
    price_in, price_out = config.price_for_tier("nano")
    assert llm.calculate_cost("nano", 1_000_000, 0) == price_in
    assert llm.calculate_cost("nano", 0, 1_000_000) == price_out
    expected = (1_000 * price_in + 2_000 * price_out) / 1_000_000
    assert llm.calculate_cost("nano", 1_000, 2_000) == expected


def test_call_llm_returns_text_usage_latency_cost(monkeypatch):
    calls = {}

    class FakeCompletions:
        def create(self, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="<think>hidden</think>visible answer"),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=2000),
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(llm, "_client", fake_client)
    monkeypatch.setenv("TRUSTROUTER_MOCK_LLM", "0")
    monkeypatch.setenv("PRICE_NANO_IN", "0.06")
    monkeypatch.setenv("PRICE_NANO_OUT", "0.24")

    result = llm.call_llm("nano", [{"role": "user", "content": "hello"}], max_tokens=100)

    assert result.text == "visible answer"
    assert result.tokens_in == 1000
    assert result.tokens_out == 2000
    assert result.latency_ms >= 0
    assert result.cost_usd == (1000 * 0.06 + 2000 * 0.24) / 1_000_000
    assert result.model == config.model_for_tier("nano")
    assert calls["max_tokens"] == 100
    assert calls["temperature"] == 0.2


def test_mock_mode_needs_no_key(monkeypatch):
    monkeypatch.setenv("TRUSTROUTER_MOCK_LLM", "1")
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    result = llm.call_llm("super", [{"role": "user", "content": "ping"}], max_tokens=100)
    assert result.mock is True
    assert result.cost_usd == 0.0
    assert result.tokens_in > 0 and result.tokens_out > 0
    assert "mock:super" in result.text
