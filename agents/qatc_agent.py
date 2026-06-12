"""
QATC Agent — QA Test Cases
Подготавливает тест-кейсы на основе BA + SA артефактов.
НЕ проводит тестирование — только формирует тест-кейсы.
"""

import logging
from models.task import Task
from services.claude_client import call_claude
from services.notifier import notify_qatc_done

logger = logging.getLogger(__name__)

QATC_SYSTEM = """Ты — опытный QA инженер банковского мобильного приложения (iOS/Android).

На вход получаешь BA и SA артефакты. Твоя задача: подготовить полный набор тест-кейсов.

## Структура QATC артефакта:

### 1. Стратегия тестирования
- Scope тестирования (что тестируем, что нет)
- Типы тестирования: функциональное, регрессионное, граничные значения, негативные

### 2. Тест-кейсы — Позитивные сценарии
Формат каждого тест-кейса:
**TC-001: [Название]**
- Предусловие: [что должно быть настроено]
- Шаги: [нумерованный список действий]
- Ожидаемый результат: [что должно произойти]
- Приоритет: Critical / High / Medium / Low

### 3. Тест-кейсы — Негативные сценарии
- Невалидные данные
- Граничные значения
- Отсутствие прав доступа
- Сетевые ошибки / таймауты

### 4. Тест-кейсы — Граничные значения
- Минимальные/максимальные значения
- Пустые поля
- Спецсимволы

### 5. Регрессионные тест-кейсы
- Что нужно проверить в смежном функционале

### 6. Чек-лист для релиза
- Список обязательных проверок перед выпуском

## Важно:
- Минимум 15 тест-кейсов
- Учитывай платформы: iOS и Android
- Учитывай требования безопасности: авторизация, маскирование данных
- НЕ проводи тестирование — только описывай тест-кейсы
- Пиши на русском языке
"""


async def run_qatc(task: Task, bot, notify_chat_id: int) -> Task:
    """
    Запускает QATC агента.
    Возвращает task с заполненными qatc_text, qatc_summary.
    """
    logger.info(f"QATC старт | задача: {task.feature_name}")

    user_text = (
        f"## BA Артефакт:\n{task.ba_text}\n\n"
        f"## SA Артефакт:\n{task.sa_text}\n\n"
        f"## Исходный запрос:\n{task.raw_message}"
    )

    try:
        response = await call_claude(QATC_SYSTEM, user_text, max_tokens=8192)
        task.qatc_text = response
        task.qatc_summary = _extract_summary(response)
        await notify_qatc_done(bot, notify_chat_id, task.qatc_summary)
        logger.info("QATC завершён")
        return task
    except Exception as e:
        logger.error(f"QATC ошибка: {e}")
        raise


def _extract_summary(text: str) -> str:
    lines = [l.strip() for l in text.split("\n") if l.strip() and not l.startswith("#")]
    summary = " ".join(lines[:3])
    return summary[:200] + "..." if len(summary) > 200 else summary
