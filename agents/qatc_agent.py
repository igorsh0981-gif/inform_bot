"""
QATC Agent — QA Test Cases
Подготавливает тест-кейсы на основе BA + SA артефактов.
Может задавать вопросы SA и BA напрямую (без участия PM).
"""

import asyncio
import logging
import re
from models.task import Task
from services.claude_client import call_claude
from services.notifier import notify_qatc_done

logger = logging.getLogger(__name__)

MAX_QA_ROUNDS = 2   # QA может задать вопросы максимум 2 раза (суммарно по SA и BA)

# ── QATC: основной промпт ──────────────────────────────────────────────────────
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
- Пустые поля / Спецсимволы

### 5. Регрессионные тест-кейсы
- Что нужно проверить в смежном функционале

### 6. Чек-лист для релиза
- Список обязательных проверок перед выпуском

## Правила:
- Минимум 15 тест-кейсов
- Учитывай платформы: iOS и Android
- Учитывай требования безопасности: авторизация, маскирование данных
- НЕ проводи тестирование — только описывай тест-кейсы
- Если есть КРИТИЧЕСКИЙ пробел в SA или BA блокирующий тест-кейсы — добавь в конце:
  ## ВОПРОС_К_SA:
  [один конкретный вопрос к SA, заканчивается на ?]
  ИЛИ
  ## ВОПРОС_К_BA:
  [один конкретный вопрос к BA, заканчивается на ?]
  — но не оба сразу, только самый критичный
- МАКСИМУМ ОДИН вопрос за раз. Если пробелов нет — секции не добавляй.
- Пиши на русском языке
"""

# ── SA: промпт для ответа на вопросы QA ───────────────────────────────────────
SA_ANSWER_SYSTEM = """Ты — опытный системный аналитик банковского мобильного приложения.

Тебе задаёт вопрос QA инженер.
Ответь коротко и конкретно на основе SA артефакта и контекста задачи.
Если информации недостаточно — дай наиболее разумное допущение и пометь [ASSUMED].
Пиши на русском языке.
"""

# ── BA: промпт для ответа на вопросы QA ───────────────────────────────────────
BA_ANSWER_SYSTEM = """Ты — опытный бизнес-аналитик банковского мобильного приложения.

Тебе задаёт вопрос QA инженер.
Ответь коротко и конкретно на основе BA артефакта и контекста задачи.
Если информации недостаточно — дай наиболее разумное допущение и пометь [ASSUMED].
Пиши на русском языке.
"""


def _extract_qa_question(text: str) -> tuple[str | None, str | None]:
    """
    Извлекает вопрос QA к SA или BA.
    Возвращает (target, question) где target = 'SA' | 'BA' | None
    """
    for target in ("SA", "BA"):
        match = re.search(
            rf"##\s*ВОПРОС_К_{target}:(.*?)(?=##|$)", text, re.DOTALL | re.IGNORECASE
        )
        if match:
            q = match.group(1).strip()
            if q and len(q) >= 5:
                if not q.endswith("?"):
                    q += "?"
                return target, q
    return None, None


def _clean_qatc_artifact(text: str) -> str:
    """Убирает секции вопросов из финального артефакта."""
    text = re.sub(r"##\s*ВОПРОС_К_SA:.*?(?=##|$)", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"##\s*ВОПРОС_К_BA:.*?(?=##|$)", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


async def _ask_agent(
    question: str,
    target: str,   # 'SA' или 'BA'
    task: Task,
) -> str:
    """
    QA задаёт вопрос SA или BA агенту через отдельный вызов Claude.
    """
    if target == "SA":
        system = SA_ANSWER_SYSTEM
        context = (
            f"## SA Артефакт:\n{task.sa_text or 'Не сформирован'}\n\n"
            f"## BA Артефакт:\n{task.ba_text or task.raw_message}\n\n"
            f"## Вопрос от QA:\n{question}"
        )
    else:  # BA
        system = BA_ANSWER_SYSTEM
        context = (
            f"## BA Артефакт:\n{task.ba_text or task.raw_message}\n\n"
            f"## Исходный запрос:\n{task.raw_message}\n\n"
            f"## Вопрос от QA:\n{question}"
        )

    logger.info(f"[QA→{target}] Вопрос: {question[:80]}")
    try:
        answer = await call_claude(system, context, max_tokens=1024, timeout=60)
        logger.info(f"[QA→{target}] Ответ: {answer[:80]}")
        return answer.strip()
    except Exception as e:
        logger.error(f"[QA→{target}] Ошибка: {e}")
        return f"[ASSUMED] Ответ {target} недоступен — продолжаю с допущениями"


async def run_qatc(task: Task, bot, notify_chat_id: int) -> Task:
    """
    Запускает QATC агента.
    QA самостоятельно уточняет у SA или BA если нужно (без участия PM).
    """
    logger.info(f"QATC старт | задача: {task.feature_name}")

    clarifications = []  # накопленные ответы SA/BA на вопросы QA

    for round_num in range(1, MAX_QA_ROUNDS + 2):
        user_text = (
            f"## BA Артефакт:\n{task.ba_text or task.raw_message}\n\n"
            f"## SA Артефакт:\n{task.sa_text or 'Не сформирован'}\n\n"
            f"## Исходный запрос:\n{task.raw_message}"
        )
        if clarifications:
            user_text += "\n\n## Уточнения от SA/BA (по запросу QA):\n" + "\n\n".join(clarifications)
        if round_num > MAX_QA_ROUNDS:
            user_text += "\n\n[ИНСТРУКЦИЯ: Вопросов больше не задавай. Сформируй финальный артефакт с допущениями [ASSUMED].]"

        try:
            response = await call_claude(QATC_SYSTEM, user_text, max_tokens=8192)
        except Exception as e:
            logger.error(f"QATC Claude ошибка (round {round_num}): {e}")
            raise

        target, question = _extract_qa_question(response)

        # Нет вопроса или исчерпали лимит — финализируем
        if not question or not target or round_num > MAX_QA_ROUNDS:
            task.qatc_text = _clean_qatc_artifact(response)
            task.qatc_summary = _extract_summary(response)
            if clarifications:
                task.qatc_text += f"\n\n---\n*QA запрашивал уточнения: {len(clarifications)} раз(а)*"
            await notify_qatc_done(bot, notify_chat_id, task.qatc_summary)
            logger.info(f"QATC завершён (раундов уточнений: {len(clarifications)})")
            return task

        # Есть вопрос — QA спрашивает SA или BA напрямую
        target_emoji = "🔧" if target == "SA" else "📋"
        await bot.send_message(
            chat_id=notify_chat_id,
            text=(
                f"🔄 QA запрашивает уточнение у {target} ({round_num}/{MAX_QA_ROUNDS}):\n\n"
                f"{target_emoji} _{question}_"
            ),
            parse_mode="Markdown",
        )

        agent_answer = await _ask_agent(question, target, task)
        clarifications.append(f"QA → {target}: {question}\n{target} ответил: {agent_answer}")

        await bot.send_message(
            chat_id=notify_chat_id,
            text=(
                f"✅ {target} ответил QA:\n\n"
                f"{agent_answer[:500]}{'...' if len(agent_answer) > 500 else ''}"
            ),
        )

    # Запасной финал
    task.qatc_text = task.qatc_text or _clean_qatc_artifact(response)
    task.qatc_summary = task.qatc_summary or _extract_summary(response)
    await notify_qatc_done(bot, notify_chat_id, task.qatc_summary)
    return task


def _extract_summary(text: str) -> str:
    lines = [l.strip() for l in text.split("\n") if l.strip() and not l.startswith("#")]
    summary = " ".join(lines[:3])
    return summary[:200] + "..." if len(summary) > 200 else summary
