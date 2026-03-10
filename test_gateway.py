"""
Quick test: verify Deluxe gateway proxy works for both
chat completions (Claude) and embeddings (Titan).

Usage:
    python test_gateway.py
"""

import os
import sys
from openai import OpenAI

GATEWAY_URL = os.getenv("DLXAI_GATEWAY_URL", "https://dlxai-dev.deluxe.com/proxy")
GATEWAY_KEY = os.getenv("DLXAI_GATEWAY_KEY", "sk-2cdb551cf35f418ea88b36")
CHAT_MODEL = os.getenv("GATEWAY_MODEL", "Claude-4.5-Sonnet")
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "Titan-v2")

client = OpenAI(base_url=GATEWAY_URL, api_key=GATEWAY_KEY)

print(f"Gateway: {GATEWAY_URL}")
print(f"Chat model: {CHAT_MODEL}")
print(f"Embed model: {EMBED_MODEL}")
print("=" * 50)

# --- Test 1: Chat Completion ---
print("\n[TEST 1] Chat Completion...")
try:
    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[{"role": "user", "content": "Say hello in one sentence."}],
        temperature=0.7,
        max_tokens=50,
    )
    if resp and resp.choices:
        answer = resp.choices[0].message.content
        print(f"  [OK] SUCCESS: {answer}")
    else:
        print(f"  [WARN] Response was empty or had no choices.")
        print(f"  Raw response: {resp}")
except Exception as e:
    print(f"  [FAIL] ERROR: {e}")

# --- Test 2: Embeddings ---
print("\n[TEST 2] Embeddings...")
try:
    resp = client.embeddings.create(
        model=EMBED_MODEL,
        input="Hello world",
    )
    if resp and resp.data:
        vec = resp.data[0].embedding
        print(f"  [OK] SUCCESS: got {len(vec)}-dim vector (first 5: {vec[:5]})")
    else:
        print(f"  [WARN] Response was empty or had no data.")
        print(f"  Raw response: {resp}")
except Exception as e:
    print(f"  [FAIL] ERROR: {e}")

print("\n" + "=" * 50)
print("Done.")
