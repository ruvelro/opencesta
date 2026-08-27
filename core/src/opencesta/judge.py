"""Level (d) of the cascade: Claude judges the pairs the scorer cannot settle.

Own-brand products carry no EAN and no shared brand, so the deterministic scorer
(level b) can only compare wording. That is enough at the extremes and useless in
the middle, where near-identical wording hides real differences — "Aceite de oliva
0,4º" against "Aceite de orujo de oliva" shares almost every word and is a
different product. Recommending one as the other would be a real error, so those
pairs go to a judge.

Two properties keep this affordable and honest:

* Only the ambiguous band is sent. High-scoring pairs are accepted without a call
  and low-scoring ones are dropped without a call.
* Every verdict is cached on disk forever, keyed by the exact product names. The
  same pair is never paid for twice, and the cache is reviewable in git.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

MODEL = "claude-opus-5"

VERDICTS = ("equivalent", "substitute", "different")

SYSTEM = """Eres un experto en productos de supermercado español. Decides si dos \
productos de cadenas distintas son intercambiables para quien hace la compra.

Veredictos:
- "equivalent": el mismo producto. Quien compre uno u otro obtiene lo mismo.
- "substitute": cumplen la misma función pero no son idénticos (una denominación de \
origen frente a la genérica, con o sin gluten, un sabor distinto de la misma gama).
- "different": productos distintos. Sustituir uno por otro decepcionaría a quien compra.

Presta especial atención a diferencias que las palabras compartidas esconden: aceite de \
oliva no es aceite de orujo, leche entera no es semidesnatada, y "sin lactosa" o "sin \
gluten" cambian para quién sirve el producto.

Ante la duda entre equivalent y substitute, elige substitute. Ante la duda entre \
substitute y different, elige different: un falso equivalente hace daño al comparador, \
un falso negativo solo deja dinero sobre la mesa."""

SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "reason": {"type": "string", "description": "Una frase, en español."},
                },
                "required": ["id", "verdict", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


def cache_key(name_a: str, name_b: str) -> str:
    """Key a verdict by the exact wording judged, not by SKU.

    SKUs get reused and relisted; the words are what the judge actually saw, so a
    cached verdict stays valid exactly as long as it was based on the same text.
    """
    digest = hashlib.sha256(f"{name_a}\x00{name_b}".encode()).hexdigest()
    return digest[:16]


@dataclass(frozen=True, slots=True)
class Verdict:
    verdict: Literal["equivalent", "substitute", "different"]
    reason: str
    model: str

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "reason": self.reason, "model": self.model}


class VerdictCache:
    """Disk-backed store of judgements, one JSON object per line."""

    def __init__(self, path: Path):
        self.path = path
        self._entries: dict[str, Verdict] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                row = json.loads(line)
                self._entries[row["key"]] = Verdict(
                    verdict=row["verdict"], reason=row["reason"], model=row.get("model", "")
                )

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, name_a: str, name_b: str) -> Verdict | None:
        return self._entries.get(cache_key(name_a, name_b))

    def put(self, name_a: str, name_b: str, verdict: Verdict) -> None:
        key = cache_key(name_a, name_b)
        self._entries[key] = verdict
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {"key": key, "a": name_a, "b": name_b, **verdict.as_dict()},
                    ensure_ascii=False,
                )
                + "\n"
            )


def build_prompt(pairs: list[tuple[str, str]]) -> str:
    lines = ["Juzga cada par. Devuelve un veredicto por cada id.\n"]
    for index, (name_a, name_b) in enumerate(pairs):
        lines.append(f"[{index}]\n  A (Mercadona): {name_a}\n  B (Dia): {name_b}\n")
    return "\n".join(lines)


def judge_pairs(
    pairs: list[tuple[str, str]],
    cache: VerdictCache,
    batch_size: int = 20,
    client: Any = None,
) -> dict[tuple[str, str], Verdict]:
    """Judge pairs, consulting the cache first and only calling for the rest."""
    results: dict[tuple[str, str], Verdict] = {}
    pending: list[tuple[str, str]] = []
    for pair in pairs:
        cached = cache.get(*pair)
        if cached is not None:
            results[pair] = cached
        elif pair not in pending:
            pending.append(pair)
    if not pending:
        return results

    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": build_prompt(batch)}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"the judge refused: {response.stop_details}")
        payload = json.loads(
            "".join(block.text for block in response.content if block.type == "text")
        )
        for row in payload["verdicts"]:
            index = row["id"]
            if not 0 <= index < len(batch):
                continue  # An id we did not ask about; ignore rather than mislabel.
            pair = batch[index]
            verdict = Verdict(verdict=row["verdict"], reason=row["reason"], model=MODEL)
            cache.put(*pair, verdict)
            results[pair] = verdict
    return results


def judging_is_available() -> bool:
    """Whether a call could be made at all, so callers can degrade instead of crash."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    return Path.home().joinpath(".config", "anthropic").exists()
