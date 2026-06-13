"""
QATC Agent — QA Test Cases
Один прогон без межагентных уточнений.
"""

import logging
from models.task import Task
from services.claude_client import call_claude
from services.notifier import notify_qatc_done
from services.utils import extract_summary

logger = logging.getLogger(__name__)

QATC_SYSTEM = """Ты — Senior QA Engineer с опытом тестирования банковских мобильных приложений (iOS/Android).

На вход получаешь BA и SA артефакты. Создай ПРОФЕССИОНАЛЬНЫЙ набор тест-кейсов для Milliy (НБУ).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## СТРУКТУРА QATC АРТЕФАКТА
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 1. Стратегия тестирования
- **In scope** / **Out of scope**
- Типы: функциональное, интеграционное, безопасность, производительность, регрессия
- Окружения: Dev / Staging / Prod-like
- Устройства: iOS 15+ (iPhone 12+), Android 10+ (Samsung, Xiaomi, Huawei)

### 2. Тест-кейсы — Позитивные сценарии (минимум 8)
**TC-001: [Название]**
- **Приоритет**: Critical / High / Medium / Low
- **Платформа**: iOS / Android / Both
- **Предусловие**: [состояние]
- **Шаги**: 1. ... 2. ...
- **Ожидаемый результат**: [UI + система]
- **Тестовые данные**: [конкретные значения]

### 3. Тест-кейсы — Негативные сценарии (минимум 6)
- Невалидные данные, превышение лимитов НБУ, нет прав, сетевые ошибки, двойное нажатие

### 4. Тест-кейсы — Граничные значения (минимум 4)
- Min/max значения, значение ±1 от лимита, пустые поля, спецсимволы

### 5. Тест-кейсы — Безопасность (минимум 4)
- Доступ без авторизации, истёкший JWT, маскирование данных в UI/логах, IDOR

### 6. Регрессионные тест-кейсы
- Список экранов/функций для проверки после релиза

### 7. Чек-лист перед релизом
- [ ] Smoke тест на Staging
- [ ] iOS и Android проверка
- [ ] Нагрузочный тест (X RPS)
- [ ] Security scan (OWASP Mobile Top 10)
- [ ] Проверка при слабом сигнале (3G)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## ПРАВИЛА
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- МИНИМУМ 22 тест-кейса суммарно
- Тестовые данные — конкретные (суммы, номера, даты), не абстрактные
- Учитывай платформенную специфику iOS vs Android
- Учитывай регуляторные лимиты НБУ
- НЕ задавай вопросов — формируй финальный артефакт с [ASSUMED] для неясного
- Пиши на русском языке
"""


async def run_qatc(task: Task, bot, notify_chat_id: int) -> Task:
    logger.info(f"QATC старт | {task.feature_name}")

    user_text = (
        f"## BA Артефакт:\n{task.ba_text}\n\n"
        f"## SA Артефакт:\n{task.sa_text}\n\n"
        f"## Исходный запрос:\n{task.raw_message}"
    )

    try:
        response = await call_claude(QATC_SYSTEM, user_text, max_tokens=16000, timeout=300)
    except Exception as e:
        logger.error(f"QATC ошибка: {e}")
        raise

    task.qatc_text = response.strip()
    task.qatc_summary = extract_summary(response)

    await notify_qatc_done(bot, notify_chat_id, task.qatc_summary)
    logger.info("QATC завершён")
    return task
