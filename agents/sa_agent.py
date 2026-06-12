"""
SA Agent — System Analyst
Формирует системный анализ на основе BA артефакта.
Если есть пробелы — задаёт вопросы BA агенту (через Claude, без участия PM).
PM получает только уведомление о ходе.
"""

import asyncio
import logging
import re
from models.task import Task
from services.claude_client import call_claude, build_content_with_attachment
from services.notifier import notify_sa_done

logger = logging.getLogger(__name__)

MAX_SA_TO_BA_ROUNDS = 2   # SA может спросить BA максимум 2 раза

# ── SA: основной промпт ────────────────────────────────────────────────────────
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

## Правила:
- Стек: Java Spring Boot (backend), Kotlin/Swift (mobile), PostgreSQL, Redis
- Если в BA артефакте есть КРИТИЧЕСКИЙ пробел блокирующий архитектуру — добавь в конце секцию:
  ## ВОПРОС_К_BA:
  [один конкретный вопрос, заканчивается на ?]
- МАКСИМУМ ОДИН вопрос за раз. Если пробелов нет — секцию не добавляй.
- Пиши на русском языке
"""

# ── BA: промпт для ответа на вопросы SA ───────────────────────────────────────
BA_ANSWER_SYSTEM = """Ты — опытный бизнес-аналитик банковского мобильного приложения.

Тебе задаёт вопрос системный аналитик (SA). 
Ответь коротко и конкретно на основе имеющегося контекста задачи.
Если информации недостаточно — дай наиболее разумное допущение и пометь [ASSUMED].
Пиши на русском языке.
"""


def _extract_sa_question(text: str) -> str | None:
    """Извлекает вопрос SA к BA из артефакта."""
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
    """Убирает секцию вопроса из финального артефакта."""
    return re.sub(
        r"##\s*ВОПРОС_К_BA:.*?(?=##|$)", "", text, flags=re.DOTALL | re.IGNORECASE
    ).strip()


async def _ask_ba(question: str, task: Task, ba_context: str) -> str:
    """
    SA задаёт вопрос BA агенту.
    BA отвечает через отдельный вызов Claude с контекстом исходного запроса.
    """
    user_text = (
        f"## Исходный запрос на фичу:\n{task.raw_message}\n\n"
        f"## BA артефакт:\n{ba_context}\n\n"
        f"## Вопрос от системного аналитика (SA):\n{question}"
    )
    logger.info(f"[SA→BA] Вопрос: {question[:80]}")
    try:
        answer = await call_claude(BA_ANSWER_SYSTEM, user_text, max_tokens=1024, timeout=60)
        logger.info(f"[SA→BA] Ответ BA: {answer[:80]}")
        return answer.strip()
    except Exception as e:
        logger.error(f"[SA→BA] Ошибка ответа BA: {e}")
        return "[ASSUMED] Ответ BA недоступен — продолжаю с допущениями"


async def run_sa(task: Task, bot, notify_chat_id: int) -> Task:
    """
    Запускает SA агента.
    SA самостоятельно уточняет у BA если нужно (без участия PM).
    """
    logger.info(f"SA старт | задача: {task.feature_name}")

    ba_context = task.ba_text or task.raw_message
    clarifications = []  # накопленные ответы BA на вопросы SA

    for round_num in range(1, MAX_SA_TO_BA_ROUNDS + 2):
        # Собираем контекст для SA
        user_text = (
            f"## BA Артефакт:\n{ba_context}\n\n"
            f"## Исходный запрос:\n{task.raw_message}\n\n"
            f"## Figma описание:\n{task.figma_content or 'Не предоставлено'}"
        )
        if clarifications:
            user_text += "\n\n## Уточнения от BA (по запросу SA):\n" + "\n\n".join(clarifications)
        if round_num > MAX_SA_TO_BA_ROUNDS:
            user_text += "\n\n[ИНСТРУКЦИЯ: Вопросов больше не задавай. Сформируй финальный артефакт с допущениями [ASSUMED] для неясного.]"

        content = build_content_with_attachment(user_text, task)

        try:
            response = await call_claude(SA_SYSTEM, content, max_tokens=8192)
        except Exception as e:
            logger.error(f"SA Claude ошибка (round {round_num}): {e}")
            raise

        question = _extract_sa_question(response)

        # Нет вопроса или исчерпали лимит — финализируем
        if not question or round_num > MAX_SA_TO_BA_ROUNDS:
            task.sa_text = _clean_sa_artifact(response)
            task.sa_summary = _extract_summary(response)
            if clarifications:
                task.sa_text += f"\n\n---\n*SA запросил уточнения у BA: {len(clarifications)} раз(а)*"
            await notify_sa_done(bot, notify_chat_id, task.sa_summary)
            logger.info(f"SA завершён (раундов с BA: {len(clarifications)})")
            return task

        # Есть вопрос — SA спрашивает BA напрямую (без PM)
        await bot.send_message(
            chat_id=notify_chat_id,
            text=(
                f"🔄 SA запрашивает уточнение у BA ({round_num}/{MAX_SA_TO_BA_ROUNDS}):\n\n"
                f"_{question}_"
            ),
            parse_mode="Markdown",
        )

        ba_answer = await _ask_ba(question, task, ba_context)
        clarifications.append(f"SA спросил: {question}\nBA ответил: {ba_answer}")

        await bot.send_message(
            chat_id=notify_chat_id,
            text=f"✅ BA ответил SA:\n\n{ba_answer[:500]}{'...' if len(ba_answer) > 500 else ''}",
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
