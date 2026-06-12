import os
import json
import logging
import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-6"
API_URL = "https://api.anthropic.com/v1/messages"
HEADERS = {
    "x-api-key": ANTHROPIC_API_KEY,
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}


async def call_claude(
    system: str,
    user_content,           # str или list (для multimodal с файлами)
    max_tokens: int = 8192,
    timeout: int = 120,
) -> str:
    """
    Базовый вызов Claude API.
    user_content может быть строкой или списком блоков (text + document/image).
    Возвращает текст ответа.
    """
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY не задан")

    # Нормализуем content
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

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(API_URL, headers=HEADERS, json=payload)
        resp.raise_for_status()
        data = resp.json()

    return data["content"][0]["text"]


def build_content_with_attachment(text: str, task) -> list:
    """
    Формирует multimodal content с вложением если есть.
    Поддерживает PDF, DOCX (как document) и изображения (как image).
    """
    content = [{"type": "text", "text": text}]

    if task.attachment_base64 and task.attachment_mime:
        if task.attachment_mime == "image/png" or task.attachment_mime == "image/jpeg":
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": task.attachment_mime,
                    "data": task.attachment_base64,
                },
            })
        else:
            # PDF, DOCX — как document
            content.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": task.attachment_mime,
                    "data": task.attachment_base64,
                },
            })

    return content
