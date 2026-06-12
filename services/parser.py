import os
import json
import logging
import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-haiku-4-5-20251001"

EXTRACT_PROMPT = """Ты — ассистент PM банковского мобильного приложения.
Из сообщения пользователя извлеки структурированную информацию о задаче на разработку.

Верни ТОЛЬКО JSON без markdown-обёртки:
{
  "feature_name": "короткое название фичи (3-6 слов, на русском)",
  "summary": "суть задачи одним абзацем (2-4 предложения)",
  "actor": "кто использует функционал (пользователь / оператор / система)",
  "module": "модуль приложения (переводы / кредиты / карты / депозиты / общее)",
  "priority": "high / medium / low / unknown"
}

Если информации недостаточно для определения поля — ставь "unknown".
"""


async def extract_feature(raw_message: str, figma_content: str = "") -> dict:
    """
    Вызывает Claude Haiku для извлечения сути задачи.
    Возвращает dict с полями feature_name, summary, actor, module, priority.
    """
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY не задан")
        return _fallback(raw_message)

    # Формируем контекст: сообщение + Figma если есть
    user_content = f"Сообщение пользователя:\n{raw_message}"
    if figma_content and "[Figma:" not in figma_content:
        user_content += f"\n\nFigma описание:\n{figma_content}"

    payload = {
        "model": MODEL,
        "max_tokens": 512,
        "system": EXTRACT_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        text = data["content"][0]["text"].strip()

        # Убираем возможные markdown-обёртки
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

        result = json.loads(text)
        logger.info(f"Фича извлечена: {result.get('feature_name')}")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e} | text: {text}")
        return _fallback(raw_message)
    except Exception as e:
        logger.error(f"Claude API ошибка: {e}")
        return _fallback(raw_message)


def _fallback(raw_message: str) -> dict:
    """Запасной вариант если Claude недоступен"""
    return {
        "feature_name": raw_message[:50].strip(),
        "summary": raw_message[:300].strip(),
        "actor": "unknown",
        "module": "unknown",
        "priority": "unknown",
    }
