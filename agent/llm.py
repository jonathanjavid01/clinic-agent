"""Optional LLM layer for the clinic agent.

Product behaviour:
- If GEMINI_API_KEY is set in the environment, open questions are answered by
  Gemini, grounded ONLY in the business facts supplied by core.py.
- If no key is set (or the call fails), returns None and core.py falls back to
  its built-in retrieval answers. The agent therefore always works offline.

The key belongs to whoever deploys the agent (the business or the reseller).
It is read from the environment only - never stored in this codebase.
"""
import json
import os
import urllib.request
import urllib.error

MODEL = os.environ.get("LLM_MODEL", "gemini-3.5-flash")
HOST = "generativelanguage.googleapis.com"


def available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def answer(system_prompt: str, user_text: str) -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    url = f"https://{HOST}/v1beta/models/{MODEL}:generateContent"
    body = json.dumps({
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_text}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400},
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("x-goog-api-key", key)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
        return text or None
    except Exception:
        return None
