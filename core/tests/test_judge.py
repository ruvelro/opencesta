import json
from types import SimpleNamespace

import pytest

from opencesta.judge import (
    MODEL,
    Verdict,
    VerdictCache,
    build_prompt,
    cache_key,
    judge_pairs,
    judging_is_available,
)


class FakeClient:
    """Stands in for anthropic.Anthropic, recording what it was asked."""

    def __init__(self, verdicts_per_call, stop_reason="end_turn"):
        self.verdicts_per_call = list(verdicts_per_call)
        self.stop_reason = stop_reason
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        payload = {"verdicts": self.verdicts_per_call.pop(0)}
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            stop_details="declinado",
            content=[SimpleNamespace(type="text", text=json.dumps(payload))],
        )


def test_cache_key_is_stable_and_order_sensitive():
    assert cache_key("a", "b") == cache_key("a", "b")
    assert cache_key("a", "b") != cache_key("b", "a")


def test_cache_roundtrips_through_disk(tmp_path):
    path = tmp_path / "verdicts.jsonl"
    cache = VerdictCache(path)
    assert cache.get("Aceite", "Aceite") is None

    cache.put("Aceite", "Aceite", Verdict("equivalent", "el mismo aceite", MODEL))
    assert VerdictCache(path).get("Aceite", "Aceite").verdict == "equivalent"
    assert len(VerdictCache(path)) == 1


def test_build_prompt_numbers_every_pair():
    prompt = build_prompt([("A uno", "B uno"), ("A dos", "B dos")])
    assert "[0]" in prompt and "[1]" in prompt
    assert "A (Mercadona): A uno" in prompt
    assert "B (Dia): B dos" in prompt


def test_judge_calls_once_then_serves_from_cache(tmp_path):
    cache = VerdictCache(tmp_path / "v.jsonl")
    client = FakeClient([[{"id": 0, "verdict": "different", "reason": "orujo no es oliva"}]])
    pairs = [("Aceite de oliva 0,4º", "Aceite de orujo de oliva")]

    first = judge_pairs(pairs, cache, client=client)
    assert first[pairs[0]].verdict == "different"
    assert len(client.calls) == 1

    # Second run must not call the API at all.
    second = judge_pairs(pairs, VerdictCache(tmp_path / "v.jsonl"), client=client)
    assert second[pairs[0]].reason == "orujo no es oliva"
    assert len(client.calls) == 1


def test_only_uncached_pairs_are_sent(tmp_path):
    cache = VerdictCache(tmp_path / "v.jsonl")
    cache.put("A", "B", Verdict("equivalent", "ya juzgado", MODEL))
    client = FakeClient([[{"id": 0, "verdict": "substitute", "reason": "parecido"}]])

    results = judge_pairs([("A", "B"), ("C", "D")], cache, client=client)

    assert len(client.calls) == 1
    assert "C" in client.calls[0]["messages"][0]["content"]
    assert "A (Mercadona): A\n" not in client.calls[0]["messages"][0]["content"]
    assert results[("A", "B")].verdict == "equivalent"
    assert results[("C", "D")].verdict == "substitute"


def test_batching_splits_large_inputs(tmp_path):
    pairs = [(f"A{i}", f"B{i}") for i in range(5)]
    client = FakeClient([
        [{"id": i, "verdict": "equivalent", "reason": "x"} for i in range(2)],
        [{"id": i, "verdict": "equivalent", "reason": "x"} for i in range(2)],
        [{"id": 0, "verdict": "equivalent", "reason": "x"}],
    ])

    results = judge_pairs(pairs, VerdictCache(tmp_path / "v.jsonl"), batch_size=2, client=client)
    assert len(client.calls) == 3
    assert len(results) == 5


def test_uses_structured_output_and_the_configured_model(tmp_path):
    client = FakeClient([[{"id": 0, "verdict": "equivalent", "reason": "x"}]])
    judge_pairs([("A", "B")], VerdictCache(tmp_path / "v.jsonl"), client=client)

    call = client.calls[0]
    assert call["model"] == MODEL
    assert call["output_config"]["format"]["type"] == "json_schema"
    enum = call["output_config"]["format"]["schema"]["properties"]["verdicts"]["items"][
        "properties"
    ]["verdict"]["enum"]
    assert enum == ["equivalent", "substitute", "different"]


def test_out_of_range_id_is_ignored(tmp_path):
    """A hallucinated index must not be pinned onto some other pair."""
    client = FakeClient([[
        {"id": 0, "verdict": "equivalent", "reason": "ok"},
        {"id": 99, "verdict": "different", "reason": "inventado"},
    ]])
    results = judge_pairs([("A", "B")], VerdictCache(tmp_path / "v.jsonl"), client=client)
    assert len(results) == 1
    assert results[("A", "B")].verdict == "equivalent"


def test_refusal_is_raised_not_silently_treated_as_different(tmp_path):
    client = FakeClient([[]], stop_reason="refusal")
    with pytest.raises(RuntimeError, match="refused"):
        judge_pairs([("A", "B")], VerdictCache(tmp_path / "v.jsonl"), client=client)


def test_cache_ignores_comments(tmp_path):
    path = tmp_path / "v.jsonl"
    path.write_text(
        '# nota\n\n{"key": "abc", "a": "A", "b": "B", "verdict": "equivalent", "reason": "r"}\n',
        encoding="utf-8",
    )
    assert len(VerdictCache(path)) == 1


def test_judging_is_available_reads_env(monkeypatch, tmp_path):
    monkeypatch.setattr("opencesta.judge.Path.home", lambda: tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert judging_is_available() is False

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert judging_is_available() is True


def test_judging_is_available_finds_a_cli_profile(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("opencesta.judge.Path.home", lambda: tmp_path)
    (tmp_path / ".config" / "anthropic").mkdir(parents=True)
    assert judging_is_available() is True
