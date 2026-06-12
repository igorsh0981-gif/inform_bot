"""
Общий модуль ревью артефактов для BA, SA, QATC.
"""
import re
import json
import logging
from services.claude_client import call_claude

logger = logging.getLogger(__name__)

# Промпт специально написан чтобы возвращать КОРОТКИЙ JSON
REVIEWER_SYSTEM = """Ты — рецензент артефактов. Оцени артефакт и верни JSON.

ПРАВИЛО: верни ТОЛЬКО одну строку JSON, без переносов внутри строк, без markdown.
Пример: {"score":85,"approved":true,"issues":[],"improvements":[]}

Поля:
- score: число 0-100
- approved: true если score>=70
- issues: массив строк (макс 3 штуки, каждая строка до 60 символов)
- improvements: массив строк (макс 3 штуки, каждая строка до 60 символов)

Критерии (по 25 баллов каждый):
1. Полнота — все разделы заполнены
2. Конкретность — числа, типы, примеры (не "будет уточнено")
3. Специфика НБУ — терминология, интеграции, регуляторика
4. Проверяемость — каждый пункт можно проверить
"""


async def review_artifact(artifact: str, agent_name: str) -> dict:
    """
    Ревью артефакта. При любой ошибке возвращает approved=True — не блокируем цепочку.
    """
    if not artifact or len(artifact) < 100:
        return {"score": 0, "approved": False, "issues": ["Артефакт пустой"], "improvements": []}

    # Передаём первые 4000 символов — достаточно для оценки
    sample = artifact[:4000]
    prompt = f"Агент: {agent_name}\n\nАртефакт (первые 4000 символов):\n{sample}"

    try:
        raw = await call_claude(REVIEWER_SYSTEM, prompt, max_tokens=256, timeout=60)
        logger.info(f"{agent_name} ревью raw: {raw[:200]}")

        # Ищем JSON — берём первый { ... } блок
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()

        # Пробуем прямой парсинг
        try:
            result = json.loads(clean)
        except json.JSONDecodeError:
            # Ищем JSON объект через regex
            m = re.search(r'\{.*?\}', clean, re.DOTALL)
            if m:
                result = json.loads(m.group())
            else:
                raise ValueError(f"JSON не найден в: {clean[:100]}")

        score = max(0, min(100, int(result.get("score", 50))))
        approved = bool(result.get("approved", score >= 70))
        issues = [str(i)[:80] for i in result.get("issues", [])][:3]
        improvements = [str(i)[:80] for i in result.get("improvements", [])][:3]

        logger.info(f"{agent_name} ревью: score={score}, approved={approved}")
        return {"score": score, "approved": approved, "issues": issues, "improvements": improvements}

    except Exception as e:
        logger.warning(f"{agent_name} ревью не удалось: {e} | raw={raw[:150] if 'raw' in dir() else 'N/A'}")
        # Не блокируем цепочку
        return {"score": 50, "approved": True, "issues": [], "improvements": []}
