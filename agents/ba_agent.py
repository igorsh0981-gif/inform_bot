"""
BA Agent — Business Analyst
Анализирует задачу, задаёт уточняющие вопросы (макс 3 итерации, таймаут 5 мин),
формирует бизнес-требования.
"""

import asyncio
import logging
import re
from datetime import datetime

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from models.task import Task
from services.claude_client import call_claude, build_content_with_attachment
from services.notifier import notify_ba_questions, notify_ba_timeout, notify_ba_done

logger = logging.getLogger(__name__)

MAX_QUESTIONS = 3          # Снижено с 5 до 3
ANSWER_TIMEOUT = 300       # 5 минут

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

## Правила формирования вопросов:
- Если есть КРИТИЧЕСКИЕ неясности — верни секцию "## ВОПРОСЫ:" в конце (МАКСИМУМ 3 вопроса)
- Каждый вопрос ОБЯЗАТЕЛЬНО заканчивается знаком "?"
- Если вопрос предполагает выбор из известных вариантов — добавь "ВАРИАНТЫ: A) ... B) ... C) ..."
- Если всё понятно или неясности некритичны — НЕ добавляй секцию вопросов
- Пиши на русском языке
- Учитывай специфику банковского приложения НБУ (Национальный банк Узбекистана)
"""


def _extract_questions(text: str) -> list[dict] | None:
    """
    Извлекает секцию вопросов из ответа BA.
    Возвращает список словарей: {text, options}
    """
    match = re.search(r"##\s*ВОПРОСЫ:(.*?)(?=##|$)", text, re.DOTALL | re.IGNORECASE)
    if not match:
        return None

    raw = match.group(1).strip()
    if not raw or len(raw) < 10:
        return None

    questions = []
    # Разбиваем на отдельные вопросы по нумерации
    items = re.split(r"\n\s*\d+[\.\)]\s*", "\n" + raw)
    for item in items:
        item = item.strip()
        if not item:
            continue

        # Извлекаем варианты ответа если есть
        options = []
        options_match = re.search(r"ВАРИАНТЫ:(.*?)(?=\n\n|$)", item, re.DOTALL)
        if options_match:
            opts_raw = options_match.group(1)
            for opt in re.findall(r"[A-Za-zА-Яа-яЁё]\)\s*(.+)", opts_raw):
                options.append(opt.strip())
            # Убираем блок вариантов из текста вопроса
            item = item[:options_match.start()].strip()

        # Убеждаемся что вопрос заканчивается на "?"
        q_text = item.strip()
        if q_text and not q_text.endswith("?"):
            q_text += "?"

        if q_text:
            questions.append({"text": q_text, "options": options})

    return questions if questions else None


def _clean_artifact(text: str) -> str:
    """Убирает секцию вопросов из финального артефакта"""
    return re.sub(
        r"##\s*ВОПРОСЫ:.*?(?=##|$)", "", text, flags=re.DOTALL | re.IGNORECASE
    ).strip()


async def _send_question_with_options(
    bot: Bot,
    chat_id: int,
    question: dict,
    attempt: int,
    total: int,
    options_cache: dict | None = None,
    task_id: str | None = None,
) -> None:
    """Отправляет вопрос с вариантами ответа (кнопки) если они есть."""
    q_text = question["text"]
    options = question.get("options", [])
    # Сохраняем варианты в кэш чтобы main.py мог восстановить текст по индексу
    if options_cache is not None and task_id is not None:
        options_cache[task_id] = options

    header = f"❓ Вопрос BA ({attempt}/{total}):\n\n{q_text}"

    if options:
        # callback_data ограничен 64 байтами Telegram — передаём только индекс
        # Текст варианта хранится в глобальном словаре ba_options_cache
        keyboard = []
        for i, opt in enumerate(options):
            label = f"{chr(65+i)}) {opt[:40]}"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"ba_opt:{i}")])
        keyboard.append([InlineKeyboardButton("✍️ Введу свой ответ", callback_data="ba_opt:__")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await bot.send_message(
            chat_id=chat_id,
            text=header + "\n\n⬇️ Выберите вариант или введите ответ текстом:",
            reply_markup=reply_markup,
        )
    else:
        await bot.send_message(
            chat_id=chat_id,
            text=header + "\n\n✍️ Напишите ответ текстом или используйте:\n/skip — пропустить\n/stop — остановить анализ",
        )


async def run_ba(
    task: Task,
    bot: Bot,
    notify_chat_id: int,
    answer_queue: asyncio.Queue,
    options_cache: dict | None = None,
) -> Task:
    """
    Запускает BA агента.
    answer_queue — очередь куда main.py кладёт ответы пользователя.
    Возвращает task с заполненными ba_text, ba_summary.
    """
    logger.info(f"BA старт | задача: {task.feature_name}")

    context = task.build_context()
    accumulated_answers = []
    question_count = 0

    for attempt in range(1, MAX_QUESTIONS + 2):
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
                response += "\n\n---\n⚠️ Часть вопросов осталась без ответа — анализ продолжен с допущениями [ASSUMED]"

            task.ba_text = _clean_artifact(response)
            task.ba_summary = _extract_summary(response)
            task.ba_questions_count = question_count
            task.ba_answers = accumulated_answers

            await notify_ba_done(bot, notify_chat_id, task.ba_summary)
            logger.info(f"BA завершён за {question_count} итераций вопросов")
            return task

        # Есть вопросы — отправляем по одному
        question_count += 1
        q = questions[0]  # Берём первый вопрос из списка
        await _send_question_with_options(bot, notify_chat_id, q, attempt, MAX_QUESTIONS, options_cache, task.task_id)

        try:
            answer = await asyncio.wait_for(
                answer_queue.get(),
                timeout=ANSWER_TIMEOUT,
            )

            if "[STOP" in answer:
                logger.info("BA получил STOP — прерываем анализ")
                raise InterruptedError("Пользователь остановил анализ")

            if "[SKIP" in answer:
                logger.info(f"BA получил SKIP на итерации {attempt}")
                accumulated_answers.append(f"Вопрос {attempt}: [ПРОПУЩЕНО PM]")
                task.ba_answers = accumulated_answers
                continue

            accumulated_answers.append(f"Вопрос {attempt}: {answer}")
            task.ba_answers = accumulated_answers
            logger.info(f"BA получил ответ на итерации {attempt}")

        except asyncio.TimeoutError:
            await notify_ba_timeout(bot, notify_chat_id)
            accumulated_answers.append(f"Вопрос {attempt}: [НЕ ПОЛУЧЕН — таймаут, продолжено с допущениями]")
            # После таймаута на первом же вопросе — выходим без новых вопросов
            break

    # Финальный прогон с накопленными ответами (после таймаута или исчерпания лимита)
    user_text = f"Запрос на разработку:\n{context}"
    if accumulated_answers:
        user_text += "\n\n## Уточнения от PM:\n" + "\n".join(accumulated_answers)
    user_text += "\n\n[ИНСТРУКЦИЯ: Не задавай больше вопросов. Сформируй финальный артефакт с допущениями [ASSUMED] для всего неясного.]"

    content = build_content_with_attachment(user_text, task)
    try:
        response = await call_claude(BA_SYSTEM, content, max_tokens=8192)
    except Exception as e:
        logger.error(f"BA финальный Claude ошибка: {e}")
        response = task.ba_text or "Ошибка формирования BA артефакта"

    task.ba_text = _clean_artifact(response)
    task.ba_summary = _extract_summary(response)
    task.ba_questions_count = question_count
    task.ba_answers = accumulated_answers
    await notify_ba_done(bot, notify_chat_id, task.ba_summary)
    return task


def _extract_summary(text: str) -> str:
    lines = [l.strip() for l in text.split("\n") if l.strip() and not l.startswith("#")]
    summary = " ".join(lines[:3])
    return summary[:200] + "..." if len(summary) > 200 else summary
