"""AI Studio client. Never expose response bodies, request URLs or keys in errors."""

import asyncio

import httpx

from core.config import get_settings

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_TRANSIENT = {429, 500, 502, 503, 504}


class AIError(RuntimeError):
    """Safe diagnostic code only; never wraps a provider exception's text."""


async def generate_reply(system_prompt: str, user_text: str, *, history: list[dict[str, str]] | None = None) -> str:
    s = get_settings()
    payload = {
        "model": s.GEMMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            *[{"role": item["role"], "content": item["content"]}
              for item in (history or []) if item.get("role") in {"user", "assistant"}],
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.5,
        "max_tokens": 512,
    }
    headers = {"Authorization": f"Bearer {s.GEMINI_API_KEY}", "Content-Type": "application/json"}
    code = "transport"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(2):
            try:
                resp = await client.post(s.AI_ENDPOINT, json=payload, headers=headers)
            except httpx.TransportError:
                code = "transport"
            else:
                if resp.status_code in _TRANSIENT:
                    code = f"http_{resp.status_code}"
                elif not resp.is_success:
                    raise AIError(f"http_{resp.status_code}") from None
                else:
                    try:
                        reply = resp.json()["choices"][0]["message"]["content"]
                        if not isinstance(reply, str) or not reply.strip():
                            raise ValueError("empty")
                        # Telegram uses UTF-16 units for its message length limit.
                        if len(reply.strip().encode("utf-16-le")) // 2 > 4096:
                            raise ValueError("too long")
                        return reply.strip()
                    except (ValueError, KeyError, IndexError, TypeError):
                        raise AIError("invalid_response") from None
            if attempt == 0:
                await asyncio.sleep(1.5)
    raise AIError(code) from None
