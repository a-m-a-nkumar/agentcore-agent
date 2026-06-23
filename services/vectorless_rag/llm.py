"""
LLM interface (spec §7) — two methods wired to the existing DLX gateway helper
(`llm_gateway.chat_completion`). No new SDK. Cheap model for routing/sufficiency,
stronger model for synthesis. JSON repair + retry-once on the routing path.
"""

from __future__ import annotations

import json
import os
import re
import threading

# Cheap model routes (reasons over summaries); stronger model writes the answer.
# Names verified against the DLX gateway; override via env.
ROUTE_MODEL = os.getenv("VELOX_ROUTE_MODEL", "Claude-4.5-Haiku")
ANSWER_MODEL = os.getenv("VELOX_ANSWER_MODEL", "Claude-4.5-Sonnet")


def _extract_json(text: str) -> dict:
    """Strip ```json fences, parse, else grab the first balanced {...}."""
    if not text:
        raise ValueError("empty response")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise ValueError(f"no JSON object in: {text[:200]!r}")


class GatewayLLM:
    """json_call (routing/sufficiency) + text_call (synthesis), counting calls."""

    def __init__(self, user_id: str | None = None) -> None:
        self.user_id = user_id
        self.json_calls = 0
        self.text_calls = 0
        self._lock = threading.Lock()  # descent runs sub-question routes in parallel

    def reset(self) -> None:
        with self._lock:
            self.json_calls = 0
            self.text_calls = 0

    @property
    def total_calls(self) -> int:
        return self.json_calls + self.text_calls

    def json_call(self, system: str, user: str) -> dict:
        from llm_gateway import chat_completion

        with self._lock:
            self.json_calls += 1
        sys2 = system + "\n\nReturn ONLY the JSON object. No prose, no code fences."
        last: Exception | None = None
        for _ in range(2):  # retry once on parse failure
            try:
                raw = chat_completion(
                    [{"role": "user", "content": user}],
                    model=ROUTE_MODEL,
                    temperature=0.0,
                    max_tokens=900,
                    system_prompt=sys2,
                    user_id=self.user_id,
                    token_source="velox-guide-route",
                )
                return _extract_json(raw)
            except Exception as e:  # noqa: BLE001 — repair + retry
                last = e
        raise last  # type: ignore[misc]

    def text_call(self, system: str, user: str) -> str:
        from llm_gateway import chat_completion

        with self._lock:
            self.text_calls += 1
        return chat_completion(
            [{"role": "user", "content": user}],
            model=ANSWER_MODEL,
            temperature=0.2,
            max_tokens=1200,
            system_prompt=system,
            user_id=self.user_id,
            token_source="velox-guide-synth",
        ).strip()
