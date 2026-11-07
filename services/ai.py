"""Gemma 4 via Google AI Studio (OpenAI-compatible endpoint).
One shared free key at launch — every caller must check quota BEFORE calling."""

import asyncio

import httpx

from core.config import get_settings

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


async def generate_reply(system_prompt: str, user_text: str) -> str:
    s = get_settings()
    payload = {
        "model": s.GEMMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.5,
        "max_tokens": 512,
    }
    headers = {"Authorization": f"Bearer {s.GEMINI_API_KEY}", "Content-Type": "application/json"}

    last_err: Exception | None = None
    for attempt in range(2):  # one retry on transient failure
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(s.AI_ENDPOINT, json=payload, headers=headers)
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = RuntimeError(f"AI HTTP {resp.status_code}: {resp.text[:200]}")
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            return (data["choices"][0]["message"]["content"] or "").strip()
        except Exception as e:  # noqa: BLE001 — surfaced to owner as generic failure
            last_err = e
            await asyncio.sleep(1.0)
    raise RuntimeError(f"AI call failed: {last_err}")
