import os
from typing import Dict, List, Optional

from openai import OpenAI


DEFAULT_CHAT_MODEL = "Claude-4.5-Sonnet"
DEFAULT_BASE_URL = "https://dlxai-dev.deluxe.com/proxy"
DEFAULT_API_KEY = "sk-2cdb551cf35f418ea88b36"


def _get_client() -> OpenAI:
    base_url = os.getenv("DLXAI_GATEWAY_URL", DEFAULT_BASE_URL)
    api_key = os.getenv("DLXAI_GATEWAY_KEY", DEFAULT_API_KEY)
    return OpenAI(base_url=base_url, api_key=api_key)


def chat_completion(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.9,
    max_tokens: Optional[int] = None,
) -> str:
    client = _get_client()
    params = {
        "model": model or os.getenv("DLXAI_CHAT_MODEL", DEFAULT_CHAT_MODEL),
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        params["max_tokens"] = max_tokens

    response = client.chat.completions.create(**params)
    if not response or not response.choices:
        raise ValueError(f"Gateway returned empty response for model={params.get('model')}")
    return (response.choices[0].message.content or "").strip()
