"""
QATC Agent — QA Test Cases (профессиональная версия)
Минимум 20 тест-кейсов, структурированный формат, ревью качества.
"""

import logging
import re
from models.task import Task
from services.claude_client import call_claude
from services.notifier import notify_qatc_done

logger = logging.getLogger(__name__)

QATC_SYSTEM = """Ты — Senior QA Engineer с опытом тестирования банковских мобильных приложений (iOS/Android).

На вход получаешь BA и SA артефакты. Создай ПРОФЕССИОНАЛЬНЫЙ набор тест-кейсов для Milliy (НБУ).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## СТРУКТУРА QATC АРТЕФАКТА
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 1. Стратегия тестирования
- **In scope**: [что тестируем]
- **Out of scope**: [что НЕ тестируем и почему]
- **Типы тестирования**: Functional / Integration / Security / Performance / Regression
- **Тестовые окружения**: Dev / Staging / Prod-like
- **Устройства**: iOS 15+ (iPhone 12+), Android 10+ (Samsung, Xiaomi, Huawei)

### 2. Тест-кейсы — Позитивные сценарии (Happy Path)
Минимум 8 тест-кейсов. Формат:

**TC-001: [Название]**
- **Приоритет**: Critical / High / Medium / Low
- **Платформа**: iOS / Android / Both
- **Предусловие**: [состояние системы и данных]
- **Шаги**:
  1. [действие]
  2. [действие]
- **Ожидаемый результат**: [что видит пользователь + что происходит в системе]
- **Тестовые данные**: [конкретные значения]

### 3. Тест-кейсы — Негативные сценарии
Минимум 6 тест-кейсов:
- Невалидные входные данные
- Превышение лимитов (регуляторные лимиты НБУ)
- Отсутствие прав доступа / заблокированный аккаунт
- Сетевые ошибки (timeout, 500, нет соединения)
- Конкурентные запросы / двойное нажатие

### 4. Тест-кейсы — Граничные значения
Минимум 4 тест-кейса:
- Минимальное и максимальное допустимое значение
- Значение на 1 меньше/больше лимита
- Пустые поля / null / спецсимволы

### 5. Тест-кейсы — Безопасность
Минимум 4 тест-кейса:
- Доступ без авторизации
- Истёкший JWT токен
- Маскирование чувствительных данных в UI и логах
- IDOR — попытка получить данные другого клиента

### 6. Регрессионные тест-кейсы
Список экранов/функций которые нужно проверить на регрессию после релиза.

### 7. Чек-лист перед релизом
- [ ] Smoke тест на Staging
- [ ] Проверка на iOS и Android
- [ ] Нагрузочный тест (X RPS)
- [ ] Security scan (OWASP Mobile Top 10)
- [ ] Проверка работы при слабом сигнале (3G)
- [ ] [специфичные для фичи проверки]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## ПРАВИЛА
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- МИНИМУМ 22 тест-кейса суммарно
- Тестовые данные — конкретные (суммы, номера, даты), не "введите данные"
- Учитывай платформенную специфику iOS vs Android
- Учитывай регуляторные лимиты НБУ (Постановление 2693 и др.)
- Если есть критический пробел в SA/BA добавь:
  ## ВОПРОС_К_SA:
  [вопрос]?
  ИЛИ ## ВОПРОС_К_BA:
  [вопрос]?
  (только один, только если критично для тест-кейсов)
- Пиши на русском языке
"""

SA_ANSWER_SYSTEM = """Ты — SA банковского приложения. Ответь на вопрос QA за 2-3 предложения.
Конкретно, с числами/типами данных если нужно. Используй [ASSUMED] если точных данных нет.
"""

BA_ANSWER_SYSTEM = """Ты — BA банковского приложения. Ответь на вопрос QA за 2-3 предложения.
Конкретно, опираясь на бизнес-требования. Используй [ASSUMED] если точных данных нет.
"""


def _extract_qa_question(text: str) -> tuple[str | None, str | None]:
    for target in ("SA", "BA"):
        match = re.search(
            rf"##\s*ВОПРОС_К_{target}:(.*?)(?=##|\Z)", text, re.DOTALL | re.IGNORECASE
        )
        if match:
            q = match.group(1).strip().rstrip("?") + "?"
            if len(q) > 6:
                return target, q
    return None, None


def _clean_qatc_artifact(text: str) -> str:
    text = re.sub(r"##\s*ВОПРОС_К_SA:.*?(?=##|\Z)", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"##\s*ВОПРОС_К_BA:.*?(?=##|\Z)", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


async def _ask_agent(question: str, target: str, task: Task) -> str:
    system = SA_ANSWER_SYSTEM if target == "SA" else BA_ANSWER_SYSTEM
    context = (
        f"## {'SA' if target == 'SA' else 'BA'} артефакт:\n"
        f"{task.sa_text if target == 'SA' else task.ba_text}\n\n"
        f"## Вопрос от QA:\n{question}"
    )
    logger.info(f"[QA→{target}] {question[:80]}")
    try:
        answer = await call_claude(system, context, max_tokens=256, timeout=40)
        return answer.strip()
    except Exception as e:
        logger.error(f"[QA→{target}] ошибка: {e}")
        return "[ASSUMED] Используй стандартную практику"


async def run_qatc(task: Task, bot, notify_chat_id: int) -> Task:
    logger.info(f"QATC старт | {task.feature_name}")

    base_text = (
        f"## BA Артефакт:\n{task.ba_text}\n\n"
        f"## SA Артефакт:\n{task.sa_text}\n\n"
        f"## Исходный запрос:\n{task.raw_message}"
    )

    clarifications = []
    prev_question = None
    response = ""

    for round_num in range(1, 3):  # максимум 1 раунд уточнений
        user_text = base_text
        if clarifications:
            user_text += "\n\n## Уточнения от SA/BA:\n" + "\n\n".join(clarifications)
        if round_num > 1:
            user_text += "\n\n[ИНСТРУКЦИЯ: Финальный прогон. Вопросов не задавай.]"

        try:
            response = await call_claude(QATC_SYSTEM, user_text, max_tokens=8192)
        except Exception as e:
            logger.error(f"QATC ошибка round {round_num}: {e}")
            raise

        target, question = _extract_qa_question(response)

        if not question or not target or round_num > 1 or question == prev_question:
            break

        prev_question = question
        await bot.send_message(notify_chat_id, f"🔄 QA → {target}:\n{question}")
        answer = await _ask_agent(question, target, task)
        clarifications.append(f"QA → {target}: {question}\n{target}: {answer}")
        await bot.send_message(notify_chat_id, f"✅ {target} → QA:\n{answer[:300]}")

    artifact = _clean_qatc_artifact(response)

    task.qatc_text = artifact
    task.qatc_summary = _extract_summary(artifact)
    await bot.send_message(notify_chat_id, "✅ QATC завершён")
    await notify_qatc_done(bot, notify_chat_id, task.qatc_summary)
    return task


def _extract_summary(text: str) -> str:
    lines = [l.strip() for l in text.split("\n") if l.strip() and not l.startswith("#")]
    summary = " ".join(lines[:3])
    return summary[:200] + "..." if len(summary) > 200 else summary
