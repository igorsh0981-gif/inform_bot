"""
BA Agent — Business Analyst (профессиональная версия)

Логика:
  1. Прогон 1: анализ → если нет вопросов → ревью → финал
  2. Если есть вопросы → отправить блоком (до 3) → ждать ответ PM
  3. Прогон 2: анализ с ответами → если ещё вопросы (новые!) → ещё 1 раунд
  4. Прогон 3: финальный артефакт → обязательный ревью качества
  
Максимум: 2 раунда вопросов + 1 финал + 1 ревью = 4 вызова Claude
"""

import asyncio
import logging
import re

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from models.task import Task
from services.claude_client import call_claude, build_content_with_attachment
from services.notifier import notify_ba_timeout, notify_ba_done

logger = logging.getLogger(__name__)

MAX_ROUNDS = 2        # максимум раундов вопросов
ANSWER_TIMEOUT = 300  # 5 минут

# ── Основной промпт BA ────────────────────────────────────────────────────────
BA_SYSTEM = """Ты — Senior Business Analyst с 10+ годами опыта в банковском секторе СНГ.
Специализация: мобильный банкинг, платёжные системы, регуляторные требования ЦБ РУз / НБУ.

Твоя задача: создать ПРОФЕССИОНАЛЬНЫЙ BA артефакт для фичи мобильного приложения Milliy (НБУ).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## СТРУКТУРА АРТЕФАКТА (обязательно все разделы)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 1. Executive Summary
- Бизнес-проблема которую решаем (1-2 предложения)
- Предлагаемое решение (1-2 предложения)
- Ожидаемый бизнес-эффект: конкретные метрики (снижение обращений в КЦ на X%, рост DAU на Y%)

### 2. Контекст и стейкхолдеры
- Инициатор и заказчик фичи
- Целевая аудитория: сегмент клиентов НБУ (физлица / ИП / ЮЛ), объём (если известен)
- Смежные команды которых затрагивает изменение

### 3. Функциональные требования
Формат каждого требования:
**FR-001** | Приоритет: Must/Should/Could | Источник: [стейкхолдер]
> Описание: [что система должна делать]
> Критерии приёмки:
> - [ ] [конкретный проверяемый критерий 1]
> - [ ] [конкретный проверяемый критерий 2]

Минимум 8 требований для любой фичи.

### 4. User Stories
Формат:
**US-001**: Как [роль], я хочу [конкретное действие], чтобы [измеримая цель]
**Acceptance Criteria** (Gherkin):
```
Given [начальное состояние]
When [действие пользователя]
Then [ожидаемый результат]
And [дополнительный результат если есть]
```
Минимум 5 User Stories.

### 5. Бизнес-правила и ограничения
- **BR-001**: [правило] — Источник: [регулятор/внутренняя политика]
Примеры для НБУ: лимиты переводов по 2693, идентификация клиентов, AML требования,
соответствие Постановлениям ЦБ РУз, интеграция с HUMO/UzCard/Paynet

### 6. Нефункциональные требования
- Производительность: время отклика UI < X мс, загрузка данных < Y сек
- Доступность: X% uptime
- Безопасность: шифрование, маскирование данных, сессионные токены
- Локализация: UZ / RU / EN

### 7. Сценарии использования (Happy path + Edge cases)
Опиши 3-5 сценариев включая негативные (нет интернета, истёк токен, недостаточно средств и т.д.)

### 8. Открытые вопросы и допущения
| # | Вопрос/Допущение | Тип | Влияние | Ответственный |
|---|---|---|---|---|
| 1 | [текст] | [UNK/TBD/ASSUMED] | [High/Med/Low] | [роль] |

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## ПРАВИЛА РАБОТЫ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Используй банковскую терминологию НБУ: iABS, MUNIS, HUMO, UzCard, Paynet, My ID
- Все допущения [ASSUMED] обосновывай — почему именно такое допущение
- Если получены ответы PM — явно используй их в соответствующих разделах
- Пиши на русском языке, термины API/технические — на английском

## КОГДА ЗАДАВАТЬ ВОПРОСЫ
Только если пробел БЛОКИРУЕТ написание требований (High Impact в таблице открытых вопросов).
Добавь секцию в конце:

## ВОПРОСЫ:
1. [вопрос 1 — конкретный, отвечает на него PM за 1 фразу]?
   ВАРИАНТЫ: A) [вариант] B) [вариант] C) [другое — уточнить]
2. [вопрос 2]?
3. [вопрос 3]?

НЕ спрашивай о том что можно разумно предположить на основе банковской практики НБУ.
НЕ повторяй вопросы на которые уже получены ответы.
Если всё ясно — секцию ВОПРОСЫ не добавляй.
"""

# ── Промпт ревьюера ───────────────────────────────────────────────────────────
BA_REVIEWER_SYSTEM = """Ты — Lead BA и QA артефактов в банковском проекте.

Проверь BA артефакт по чеклисту и верни ТОЛЬКО JSON:
{
  "score": 0-100,
  "issues": ["issue1", "issue2"],
  "missing_sections": ["section1"],
  "improvements": ["конкретное улучшение 1", "конкретное улучшение 2"],
  "approved": true/false
}

Критерии (каждый 0-10 баллов):
1. Executive Summary — есть конкретные метрики эффекта?
2. FR — минимум 8 требований с критериями приёмки?
3. US — минимум 5 историй с Gherkin AC?
4. Бизнес-правила — упомянуты регуляторные требования НБУ?
5. Edge cases — описаны негативные сценарии?
6. Нефункциональные требования — есть конкретные числа?
7. Открытые вопросы — есть таблица?
8. Полнота — нет разделов "будет уточнено" без обоснования?
9. Конкретность — нет воды, каждое требование проверяемо?
10. Банковская специфика — терминология НБУ, интеграции упомянуты?

approved=true если score >= 75 И нет критических issues.
"""


def _extract_questions(text: str) -> list[dict]:
    """Извлекает все вопросы из секции ## ВОПРОСЫ:"""
    match = re.search(r"##\s*ВОПРОСЫ:(.*?)(?=##|$)", text, re.DOTALL | re.IGNORECASE)
    if not match:
        return []
    raw = match.group(1).strip()
    if not raw or len(raw) < 10:
        return []

    questions = []
    items = re.split(r"\n\s*\d+[\.\)]\s+", "\n" + raw)
    for item in items:
        item = item.strip()
        if not item:
            continue
        options = []
        opts_match = re.search(r"ВАРИАНТЫ:(.*?)(?=\n\n|\Z)", item, re.DOTALL)
        if opts_match:
            for opt in re.findall(r"[A-Za-zА-Яа-яЁё]\)\s*(.+)", opts_match.group(1)):
                options.append(opt.strip())
            item = item[:opts_match.start()].strip()
        q_text = item.strip().rstrip("?") + "?"
        if len(q_text) > 6:
            questions.append({"text": q_text, "options": options})
    return questions[:3]


def _clean_artifact(text: str) -> str:
    return re.sub(
        r"##\s*ВОПРОСЫ:.*?(?=##|\Z)", "", text, flags=re.DOTALL | re.IGNORECASE
    ).strip()


def _format_qa_pairs(rounds: list[dict]) -> str:
    """Форматирует историю вопросов и ответов для контекста."""
    parts = []
    for i, r in enumerate(rounds, 1):
        parts.append(f"=== Раунд {i} ===")
        for j, q in enumerate(r["questions"], 1):
            parts.append(f"Вопрос {j}: {q['text']}")
        parts.append(f"Ответ PM: {r['answer']}")
    return "\n".join(parts)


async def _send_questions_block(
    bot: Bot,
    chat_id: int,
    questions: list[dict],
    round_num: int,
    max_rounds: int,
    options_cache: dict,
    task_id: str,
) -> None:
    """Отправляет все вопросы одним блоком."""
    lines = [
        f"❓ BA: раунд {round_num}/{max_rounds} — {len(questions)} вопрос(а)",
        "Ответьте одним сообщением на все:\n",
    ]

    first_options = []
    for i, q in enumerate(questions, 1):
        lines.append(f"{i}. {q['text']}")
        opts = q.get("options", [])
        if opts:
            for j, opt in enumerate(opts):
                lines.append(f"   {chr(65+j)}) {opt}")
            if not first_options:
                first_options = opts
        lines.append("")

    lines.append("/skip — пропустить  /stop — остановить")
    text = "\n".join(lines)

    # Кнопки только если 1 вопрос с вариантами
    if len(questions) == 1 and first_options:
        options_cache[task_id] = first_options
        keyboard = [
            [InlineKeyboardButton(f"{chr(65+i)}) {opt[:40]}", callback_data=f"ba_opt:{i}")]
            for i, opt in enumerate(first_options)
        ]
        keyboard.append([InlineKeyboardButton("✍️ Свой ответ", callback_data="ba_opt:__")])
        await bot.send_message(
            chat_id=chat_id, text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await bot.send_message(chat_id=chat_id, text=text)


async def _review_artifact(artifact: str, task: Task) -> dict:
    """Запускает ревьюера артефакта. Возвращает результат проверки."""
    try:
        import json
        review_text = (
            f"## BA Артефакт для проверки:\n\n{artifact}\n\n"
            f"## Контекст задачи:\n{task.feature_name} — {task.summary}"
        )
        raw = await call_claude(BA_REVIEWER_SYSTEM, review_text, max_tokens=1024, timeout=45)
        # Убираем возможные markdown-обёртки
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()
        return json.loads(clean)
    except Exception as e:
        logger.warning(f"BA ревью не удалось: {e}")
        return {"score": 0, "approved": False, "issues": [str(e)], "improvements": []}


async def _wait_answer(
    answer_queue: asyncio.Queue,
    bot: Bot,
    chat_id: int,
) -> str | None:
    """Ждёт ответ PM. Возвращает текст или None при STOP."""
    try:
        answer = await asyncio.wait_for(answer_queue.get(), timeout=ANSWER_TIMEOUT)
        if "[STOP" in answer:
            raise InterruptedError("Пользователь остановил анализ")
        if "[SKIP" in answer:
            logger.info("BA получил SKIP")
            return "[ПРОПУЩЕНО — продолжаю с допущениями]"
        return answer
    except asyncio.TimeoutError:
        await notify_ba_timeout(bot, chat_id)
        return "[ТАЙМАУТ — продолжаю с допущениями]"


async def run_ba(
    task: Task,
    bot: Bot,
    notify_chat_id: int,
    answer_queue: asyncio.Queue,
    options_cache: dict | None = None,
) -> Task:
    """
    Профессиональный BA агент с итеративным уточнением и ревью качества.
    """
    if options_cache is None:
        options_cache = {}

    logger.info(f"BA старт | задача: {task.feature_name}")

    context = task.build_context()
    qa_rounds: list[dict] = []   # история всех раундов вопрос/ответ
    prev_questions: set[str] = set()  # защита от повтора вопросов

    response = ""

    for round_num in range(MAX_ROUNDS + 1):  # 0 = первый прогон без ответов
        # ── Формируем контекст для Claude ────────────────────────────────────
        user_text = f"## Запрос на разработку:\n{context}"

        if qa_rounds:
            user_text += f"\n\n## История уточнений PM:\n{_format_qa_pairs(qa_rounds)}"

        if round_num >= MAX_ROUNDS:
            user_text += "\n\n[ИНСТРУКЦИЯ: Это финальный прогон. Вопросов больше НЕ задавай. Сформируй полный артефакт используя все полученные ответы и [ASSUMED] для оставшихся пробелов.]"

        content = build_content_with_attachment(user_text, task)

        try:
            response = await call_claude(BA_SYSTEM, content, max_tokens=8192)
        except Exception as e:
            logger.error(f"BA Claude ошибка (round {round_num}): {e}")
            raise

        questions = _extract_questions(response)

        # Фильтруем вопросы — не повторяем уже заданные
        new_questions = [
            q for q in questions
            if q["text"] not in prev_questions
        ]

        # Нет новых вопросов или финальный прогон → выходим
        if not new_questions or round_num >= MAX_ROUNDS:
            break

        # ── Есть новые вопросы → отправляем блоком ───────────────────────────
        for q in new_questions:
            prev_questions.add(q["text"])

        await _send_questions_block(
            bot, notify_chat_id, new_questions,
            round_num + 1, MAX_ROUNDS,
            options_cache, task.task_id,
        )

        answer = await _wait_answer(answer_queue, bot, notify_chat_id)

        qa_rounds.append({
            "questions": new_questions,
            "answer": answer or "[нет ответа]",
        })

        logger.info(f"BA раунд {round_num + 1} завершён, ответ: {str(answer)[:60]}")

    # ── Финальный артефакт сформирован ───────────────────────────────────────
    artifact = _clean_artifact(response)

    # ── Ревью качества ────────────────────────────────────────────────────────
    await bot.send_message(notify_chat_id, "🔍 Проверяю качество BA артефакта...")
    review = await _review_artifact(artifact, task)

    score = review.get("score", 0)
    improvements = review.get("improvements", [])
    logger.info(f"BA ревью: score={score}, approved={review.get('approved')}")

    # Если качество низкое — делаем улучшенный финальный прогон
    if not review.get("approved") and improvements:
        improvements_text = "\n".join(f"- {imp}" for imp in improvements)
        user_text = (
            f"## Запрос на разработку:\n{context}\n\n"
            + (f"## История уточнений PM:\n{_format_qa_pairs(qa_rounds)}\n\n" if qa_rounds else "")
            + f"## Черновик артефакта:\n{artifact}\n\n"
            f"## Требуемые улучшения от ревьюера (score={score}/100):\n{improvements_text}\n\n"
            f"[ИНСТРУКЦИЯ: Улучши артефакт устранив все замечания. "
            f"Вопросов не задавай. Верни полный улучшенный артефакт.]"
        )
        content = build_content_with_attachment(user_text, task)
        try:
            improved = await call_claude(BA_SYSTEM, content, max_tokens=8192)
            artifact = _clean_artifact(improved)
            logger.info("BA артефакт улучшен после ревью")
        except Exception as e:
            logger.warning(f"BA улучшение не удалось: {e}")

    task.ba_text = artifact
    task.ba_summary = _extract_summary(artifact)
    task.ba_questions_count = len(qa_rounds)
    task.ba_answers = [r["answer"] for r in qa_rounds]

    status_msg = f"✅ BA завершён (раундов: {len(qa_rounds)}, качество: {score}/100)"
    await bot.send_message(notify_chat_id, status_msg)
    await notify_ba_done(bot, notify_chat_id, task.ba_summary)

    logger.info(f"BA финал | раундов={len(qa_rounds)} score={score}")
    return task


def _extract_summary(text: str) -> str:
    lines = [l.strip() for l in text.split("\n") if l.strip() and not l.startswith("#")]
    summary = " ".join(lines[:3])
    return summary[:200] + "..." if len(summary) > 200 else summary
