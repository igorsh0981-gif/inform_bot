"""
SA Agent — System Analyst
Формирует системный анализ на основе BA артефакта.
Задаёт вопросы BA только если пробел не помечен как TBD/UNK в BA артефакте.
"""

import asyncio
import logging
import re
from models.task import Task
from services.claude_client import call_claude, build_content_with_attachment
from services.notifier import notify_sa_done

logger = logging.getLogger(__name__)

MAX_SA_TO_BA_ROUNDS = 2

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

## ВАЖНЫЕ правила:
- Стек: Java Spring Boot (backend), Kotlin/Swift (mobile), PostgreSQL, Redis
- Поля помеченные [TBD] или [UNK] в BA артефакте — принимай как [ASSUMED] с разумным допущением, НЕ задавай по ним вопросы
- Вопрос к BA — ТОЛЬКО если отсутствует информация критичная для архитектуры И она НЕ помечена TBD/UNK в BA
- Если нужен вопрос — добавь в конце:
  ## ВОПРОС_К_BA:
  [один конкретный вопрос, заканчивается на ?]
- МАКСИМУМ ОДИН вопрос за итерацию. Если пробелов нет — секцию не добавляй.
- Пиши на русском языке
"""

BA_ANSWER_SYSTEM = """Ты — опытный бизнес-аналитик банковского мобильного приложения.

Тебе задаёт вопрос системный аналитик (SA).
Ответь коротко и конкретно на основе имеющегося контекста задачи.
Если информации нет — дай наиболее разумное допущение и пометь [ASSUMED].
НЕ отвечай что вопрос помечен TBD — просто дай лучшее допущение.
Пиши на русском языке. Максимум 3 абзаца.
"""


def _extract_sa_question(text: str) -> str | None:
    match = re.search(r"##\s*ВОПРОС_К_BA:(.*?)(?=##|$)", text, re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    q = match.group(1).strip()
    if not q or len(q) < 5:
        return None
    if not q.endswith("?"):
        q += "?"
    return q


def _clean_sa_artifact(text: str) -> str:
    return re.sub(
        r"##\s*ВОПРОС_К_BA:.*?(?=##|$)", "", text, flags=re.DOTALL | re.IGNORECASE
    ).strip()


async def _ask_ba(question: str, task: Task) -> str:
    user_text = (
        f"## Исходный запрос:\n{task.raw_message}\n\n"
        f"## BA артефакт:\n{task.ba_text}\n\n"
        f"## Вопрос от SA:\n{question}"
    )
    logger.info(f"[SA→BA] Вопрос: {question[:80]}")
    try:
        answer = await call_claude(BA_ANSWER_SYSTEM, user_text, max_tokens=512, timeout=45)
        logger.info(f"[SA→BA] Ответ: {answer[:80]}")
        return answer.strip()
    except Exception as e:
        logger.error(f"[SA→BA] Ошибка: {e}")
        return "[ASSUMED] Нет данных — использую разумное допущение"


async def run_sa(task: Task, bot, notify_chat_id: int) -> Task:
    logger.info(f"SA старт | задача: {task.feature_name}")

    base_text = (
        f"## BA Артефакт:\n{task.ba_text}\n\n"
        f"## Исходный запрос:\n{task.raw_message}\n\n"
        f"## Figma описание:\n{task.figma_content or 'Не предоставлено'}"
    )

    clarifications = []
    prev_question = None  # защита от повтора одного вопроса

    for round_num in range(1, MAX_SA_TO_BA_ROUNDS + 2):
        user_text = base_text
        if clarifications:
            user_text += "\n\n## Уточнения от BA:\n" + "\n\n".join(clarifications)
        if round_num > MAX_SA_TO_BA_ROUNDS:
            user_text += "\n\n[ИНСТРУКЦИЯ: Вопросов больше не задавай. Финальный артефакт с [ASSUMED] для всего неясного.]"

        content = build_content_with_attachment(user_text, task)
        try:
            response = await call_claude(SA_SYSTEM, content, max_tokens=8192)
        except Exception as e:
            logger.error(f"SA ошибка round {round_num}: {e}")
            raise

        question = _extract_sa_question(response)

        # Нет вопроса, лимит исчерпан, или повтор вопроса — финал
        if not question or round_num > MAX_SA_TO_BA_ROUNDS or question == prev_question:
            task.sa_text = _clean_sa_artifact(response)
            task.sa_summary = _extract_summary(response)
            if clarifications:
                task.sa_text += f"\n\n---\n*SA уточнял у BA: {len(clarifications)} раз(а)*"
            await notify_sa_done(bot, notify_chat_id, task.sa_summary)
            logger.info(f"SA завершён (раундов: {len(clarifications)})")
            return task

        prev_question = question

        await bot.send_message(
            chat_id=notify_chat_id,
            text=f"🔄 SA → BA ({round_num}/{MAX_SA_TO_BA_ROUNDS}):\n\n_{question}_",
            parse_mode="Markdown",
        )

        ba_answer = await _ask_ba(question, task)
        clarifications.append(f"SA: {question}\nBA: {ba_answer}")

        await bot.send_message(
            chat_id=notify_chat_id,
            text=f"✅ BA → SA:\n{ba_answer[:400]}{'...' if len(ba_answer) > 400 else ''}",
        )

    # Запасной финал
    task.sa_text = task.sa_text or _clean_sa_artifact(response)
    task.sa_summary = task.sa_summary or _extract_summary(response)
    await notify_sa_done(bot, notify_chat_id, task.sa_summary)
    return task


def _extract_summary(text: str) -> str:
    lines = [l.strip() for l in text.split("\n") if l.strip() and not l.startswith("#")]
    summary = " ".join(lines[:3])
    return summary[:200] + "..." if len(summary) > 200 else summary
