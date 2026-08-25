from __future__ import annotations

import dftworld_bench.agents as agents


def test_make_provider_receives_explicit_trusted_values(monkeypatch) -> None:
    seen = {}

    class Fake:
        def __init__(self, model_id, base_url=None, apikey=None):
            seen.update(model_id=model_id, base_url=base_url, apikey=apikey)

    monkeypatch.setattr(agents, "DeepSeek", Fake)
    agents.make_provider(
        "deepseek/deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        api_key="sk-trusted",
    )
    assert seen == {
        "model_id": "deepseek-v4-pro",
        "base_url": "https://api.deepseek.com",
        "apikey": "sk-trusted",
    }
