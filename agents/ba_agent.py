"""
BA Agent — Business Analyst
Анализирует задачу, задаёт уточняющие вопросы (макс 5 итераций, таймаут 5 мин),
формирует бизнес-требования.
"""

import asyncio
import logging
import re
from datetime import datetime

from telegram import Bot
from models.task import Task
from services.claude_client import call_claude, build_content_with_attachment
from services.notifier import notify_ba_questions, notify_ba_timeout, notify_ba_done

logger = logging.getLogger(__name__)

MAX_QUESTIONS = 5
ANSWER_TIMEOUT = 300  # 5 минут в секундах

BA_SYSTEM = """Ты — опытный бизнес-аналитик банковского мобильного приложения (iOS/Android).

Твоя задача: проанализировать запрос на разработку новой фичи и создать полный BA артефакт.

## Структура BA артефакта:

### 1. Контекст и цели
- Бизнес-цель фичи
- Целевая аудитория (сегмент клиентов)
- Бизнес-ценность (KPI которые улучшаем)

### 2. Функциональные требования
- Список требований в формате: FR-001, FR-002...
- Для каждого: описание, приоритет (Must/Should/Could), критерии приёмки

### 3. User Stories
- Формат: Как [роль], я хочу [действие], чтобы [цель]
- Acceptance Criteria для каждой истории

### 4. Бизнес-правила и ограничения
- Регуляторные требования (ЦБ РУз, НБУ)
- Бизнес-правила (лимиты, условия)

### 5. Открытые вопросы
- Помечай неясности как [UNK], предположения как [ASSUMED], требует уточнения как [TBD]

## Важно:
- Если есть КРИТИЧЕСКИЕ неясности (UNK/TBD) — верни секцию "## ВОПРОСЫ:" в конце
- Если всё понятно или неясности некритичны — не добавляй секцию вопросов
- Пиши на русском языке
- Учитывай специфику банковского приложения НБУ (Национальный банк Узбекистана)
"""


def _extract_questions(text: str) -> str | None:
    """Извлекает секцию вопросов из ответа BA"""
    match = re.search(r"##\s*ВОПРОСЫ:(.*?)(?=##|$)", text, re.DOTALL | re.IGNORECASE)
    if match:
        questions = match.group(1).strip()
        if questions and len(questions) > 10:
            return questions
    # Также проверяем наличие маркеров неясности
    has_unknown = bool(re.search(r"\[UNK\]|\[TBD\]", text))
    return None  # Не прерываем если только ASSUMED


def _clean_artifact(text: str) -> str:
    """Убирает секцию вопросов из финального артефакта"""
    return re.sub(
        r"##\s*ВОПРОСЫ:.*?(?=##|$)", "", text, flags=re.DOTALL | re.IGNORECASE
    ).strip()


async def run_ba(
    task: Task,
    bot: Bot,
    notify_chat_id: int,
    answer_queue: asyncio.Queue,
) -> Task:
    """
    Запускает BA агента.
    answer_queue — очередь куда main.py кладёт ответы пользователя.
    Возвращает task с заполненными ba_text, ba_summary.
    """
    logger.info(f"BA старт | задача: {task.feature_name}")

    context = task.build_context()
    accumulated_answers = []

    for attempt in range(1, MAX_QUESTIONS + 2):  # +2 чтобы сделать финальный прогон
        # Формируем промпт с накопленными ответами
        user_text = f"Запрос на разработку:\n{context}"
        if accumulated_answers:
            user_text += "\n\n## Уточнения от PM:\n" + "\n".join(accumulated_answers)

        # Вызов Claude
        content = build_content_with_attachment(user_text, task)
        try:
            response = await call_claude(BA_SYSTEM, content, max_tokens=8192)
        except Exception as e:
            logger.error(f"BA Claude ошибка: {e}")
            raise

        questions = _extract_questions(response)

        # Нет вопросов или исчерпали лимит — финализируем
        if not questions or attempt > MAX_QUESTIONS:
            if attempt > MAX_QUESTIONS and questions:
                # Добавляем пометку о незакрытых вопросах
                response += "\n\n---\n⚠️ Часть вопросов осталась без ответа — анализ продолжен с допущениями [ASSUMED]"

            task.ba_text = _clean_artifact(response)
            task.ba_summary = _extract_summary(response)
            task.ba_questions_count = attempt - 1
            task.ba_answers = accumulated_answers

            await notify_ba_done(bot, notify_chat_id, task.ba_summary)
            logger.info(f"BA завершён за {attempt - 1} итераций вопросов")
            return task

        # Есть вопросы — отправляем и ждём ответ
        if attempt <= MAX_QUESTIONS:
            await notify_ba_questions(bot, notify_chat_id, questions, attempt)

            try:
                answer = await asyncio.wait_for(
                    answer_queue.get(),
                    timeout=ANSWER_TIMEOUT,
                )
                accumulated_answers.append(f"Ответ {attempt}: {answer}")
                task.ba_answers = accumulated_answers
                logger.info(f"BA получил ответ на итерации {attempt}")

            except asyncio.TimeoutError:
                await notify_ba_timeout(bot, notify_chat_id)
                accumulated_answers.append(
                    f"Ответ {attempt}: [НЕ ПОЛУЧЕН — продолжено с допущениями]"
                )
                # После таймаута делаем финальный прогон без вопросов
                continue

    # Запасной финал если вышли из цикла иначе
    task.ba_text = task.ba_text or response
    task.ba_summary = task.ba_summary or _extract_summary(response)
    return task


def _extract_summary(text: str) -> str:
    """Извлекает краткое резюме из артефакта (первые 200 символов содержательного текста)"""
    lines = [l.strip() for l in text.split("\n") if l.strip() and not l.startswith("#")]
    summary = " ".join(lines[:3])
    return summary[:200] + "..." if len(summary) > 200 else summary
