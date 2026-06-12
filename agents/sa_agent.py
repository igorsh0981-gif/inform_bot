"""
SA Agent — System Analyst
Формирует системный анализ на основе BA артефакта.
"""

import logging
from models.task import Task
from services.claude_client import call_claude, build_content_with_attachment
from services.notifier import notify_sa_done

logger = logging.getLogger(__name__)

SA_SYSTEM = """Ты — опытный системный аналитик банковского мобильного приложения (iOS/Android).

На вход получаешь BA артефакт. Твоя задача: создать полный SA артефакт.

## Структура SA артефакта:

### 1. Архитектурное решение
- Затрагиваемые микросервисы/модули
- Новые компоненты которые нужно создать
- Паттерн взаимодействия (REST/gRPC/Event)

### 2. API контракты
- Endpoint'ы (метод, путь, request/response)
- Коды ответов и обработка ошибок
- Авторизация и аутентификация

### 3. Модель данных
- Новые таблицы/коллекции
- Изменения в существующих схемах
- Индексы и связи

### 4. Sequence диаграмма
- Текстовое описание потока взаимодействия компонентов
- Шаги: клиент → API Gateway → сервис → БД → ответ

### 5. Нефункциональные требования
- Производительность (RPS, latency)
- Безопасность (шифрование, маскирование данных)
- Масштабируемость

### 6. Оценка трудоёмкости
- Backend разработка: X дней
- Frontend разработка: X дней
- Тестирование: X дней
- Итого: X дней

## Важно:
- Стек: Java Spring Boot (backend), Kotlin/Swift (mobile)
- БД: PostgreSQL, Redis (кэш)
- Учитывай требования безопасности банковских приложений НБУ
- Пиши на русском языке
"""


async def run_sa(task: Task, bot, notify_chat_id: int) -> Task:
    """
    Запускает SA агента.
    Возвращает task с заполненными sa_text, sa_summary.
    """
    logger.info(f"SA старт | задача: {task.feature_name}")

    user_text = (
        f"## BA Артефакт:\n{task.ba_text}\n\n"
        f"## Исходный запрос:\n{task.raw_message}\n\n"
        f"## Figma описание:\n{task.figma_content or 'Не предоставлено'}"
    )

    content = build_content_with_attachment(user_text, task)

    try:
        response = await call_claude(SA_SYSTEM, content, max_tokens=8192)
        task.sa_text = response
        task.sa_summary = _extract_summary(response)
        await notify_sa_done(bot, notify_chat_id, task.sa_summary)
        logger.info("SA завершён")
        return task
    except Exception as e:
        logger.error(f"SA ошибка: {e}")
        raise


def _extract_summary(text: str) -> str:
    lines = [l.strip() for l in text.split("\n") if l.strip() and not l.startswith("#")]
    summary = " ".join(lines[:3])
    return summary[:200] + "..." if len(summary) > 200 else summary
