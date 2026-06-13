import os
import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-6"
API_URL = "https://api.anthropic.com/v1/messages"

MAX_RETRIES = 2
RETRY_DELAY = 5  # сек между попытками


def _headers():
    # Динамически — на случай если ключ загружается позже
    return {
        "x-api-key": ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY", ""),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


async def call_claude(
    system: str,
    user_content,
    max_tokens: int = 8192,
    timeout: int = 240,   # поднято с 120 до 240 сек
) -> str:
    """
    Вызов Claude API с retry при таймауте.
    timeout=240 — достаточно для тяжёлых прогонов на 8192 токенов.
    """
    if not (ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY", "")):
        raise ValueError("ANTHROPIC_API_KEY не задан")

    if isinstance(user_content, str):
        content = [{"type": "text", "text": user_content}]
    else:
        content = user_content

    payload = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": content}],
    }

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(API_URL, headers=_headers(), json=payload)
                resp.raise_for_status()
                data = resp.json()
            return data["content"][0]["text"]

        except httpx.ReadTimeout as e:
            last_error = e
            logger.warning(f"Claude ReadTimeout (попытка {attempt}/{MAX_RETRIES}), retry через {RETRY_DELAY}s")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)

        except httpx.HTTPStatusError as e:
            # 529 Overloaded — тоже retry
            if e.response.status_code in (529, 503, 500) and attempt < MAX_RETRIES:
                logger.warning(f"Claude {e.response.status_code} (попытка {attempt}/{MAX_RETRIES}), retry")
                await asyncio.sleep(RETRY_DELAY * attempt)
            else:
                raise

        except Exception as e:
            raise

    raise last_error or RuntimeError("Claude API недоступен после всех попыток")


def build_content_with_attachment(text: str, task: "Task") -> list:
    content = [{"type": "text", "text": text}]

    if task.attachment_base64 and task.attachment_mime:
        if task.attachment_mime in ("image/png", "image/jpeg"):
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": task.attachment_mime,
                    "data": task.attachment_base64,
                },
            })
        else:
            content.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": task.attachment_mime,
                    "data": task.attachment_base64,
                },
            })

    return content
