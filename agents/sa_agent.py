"""
SA Agent — System Analyst (профессиональная версия)
Задаёт вопросы BA только о реально неизвестном (не TBD из артефакта).
Включает ревью собственного артефакта.
"""

import asyncio
import logging
import re
from models.task import Task
from services.claude_client import call_claude, build_content_with_attachment
from services.notifier import notify_sa_done

logger = logging.getLogger(__name__)

MAX_SA_TO_BA_ROUNDS = 2

SA_SYSTEM = """Ты — Senior System Analyst с опытом проектирования банковских систем (Java Spring Boot, Kotlin/Swift).

На вход получаешь BA артефакт. Создай ПРОФЕССИОНАЛЬНЫЙ SA артефакт для мобильного приложения Milliy (НБУ).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## СТРУКТУРА SA АРТЕФАКТА
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 1. Архитектурное решение
- Затрагиваемые сервисы (iABS, MUNIS, transfer-service, notification-service и др.)
- Новые компоненты: название, ответственность, тип (microservice/module/lib)
- Паттерн: REST / gRPC / Event-driven (Kafka), обоснование выбора
- Диаграмма зависимостей (текстовая: A → B → C)

### 2. API контракты (OpenAPI-style)
Для каждого endpoint:
```
[METHOD] /api/v1/[path]
Auth: Bearer JWT
Request: { поля с типами и валидацией }
Response 200: { структура ответа }
Response 4xx/5xx: { код, message, errorCode }
```
Минимум 3 endpoint'а для любой фичи.

### 3. Модель данных
```sql
-- Новые таблицы
CREATE TABLE [name] (
  id UUID PRIMARY KEY,
  ...
  created_at TIMESTAMPTZ DEFAULT NOW()
);
-- Индексы
CREATE INDEX ...
```
Изменения в существующих таблицах — ALTER TABLE с обоснованием.

### 4. Sequence диаграмма
```
Mobile App
  → API Gateway (auth, rate-limit)
    → [Service] (бизнес-логика)
      → PostgreSQL (запись)
      → Redis (кэш, TTL=X)
      → [External] iABS/MUNIS (если нужно)
    ← [Service] response
  ← API Gateway
← Mobile App (UI update)
```

### 5. Нефункциональные требования
| Параметр | Требование | Обоснование |
|---|---|---|
| Response time P95 | < 500ms | банковский стандарт |
| Throughput | X RPS | расчёт от DAU |
| Availability | 99.9% | SLA НБУ |
| Data retention | X дней | регуляторное требование |

### 6. Безопасность
- Аутентификация: JWT + refresh token / mTLS для межсервисного
- Авторизация: RBAC роли (client / operator / admin)
- Маскирование данных в логах: номера карт, суммы
- Audit log: какие события пишем, формат

### 7. Оценка трудоёмкости
| Компонент | Backend (дни) | Mobile iOS (дни) | Mobile Android (дни) | QA (дни) |
|---|---|---|---|---|
| [компонент 1] | X | X | X | X |
| **Итого** | **X** | **X** | **X** | **X** |

### 8. Риски реализации
| Риск | Вероятность | Влияние | Митигация |
|---|---|---|---|
| [риск] | H/M/L | H/M/L | [действие] |

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## ПРАВИЛА
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Стек: Java 17 Spring Boot 3 (backend), Kotlin (Android), Swift (iOS), PostgreSQL 15, Redis 7, Kafka
- Поля [TBD]/[UNK] из BA — принимай как [ASSUMED] с разумным банковским допущением
- Вопрос к BA только если архитектурное решение принципиально зависит от ответа И это не TBD
- Если нужен вопрос, добавь в конце:
  ## ВОПРОС_К_BA:
  [один конкретный вопрос]?
- Пиши на русском, термины API/SQL — на английском
"""

BA_ANSWER_SYSTEM = """Ты — Senior BA банковского мобильного приложения.
Отвечай на вопрос SA коротко и конкретно (максимум 3 предложения).
Опирайся на банковскую практику НБУ. Если точных данных нет — дай лучшее [ASSUMED] допущение.
Не говори что вопрос помечен TBD — просто дай ответ.
"""

SA_REVIEWER_SYSTEM = """Ты — архитектор банковских систем. Проверь SA артефакт.
Верни ТОЛЬКО JSON:
{
  "score": 0-100,
  "approved": true/false,
  "issues": ["issue1"],
  "improvements": ["улучшение 1"]
}
Критерии: API контракты полные (0-20), модель данных есть (0-20),
sequence диаграмма (0-20), NFR с числами (0-20), оценка трудоёмкости (0-20).
approved=true если score >= 75.
"""


def _extract_sa_question(text: str) -> str | None:
    match = re.search(r"##\s*ВОПРОС_К_BA:(.*?)(?=##|\Z)", text, re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    q = match.group(1).strip().rstrip("?") + "?"
    return q if len(q) > 6 else None


def _clean_sa_artifact(text: str) -> str:
    return re.sub(r"##\s*ВОПРОС_К_BA:.*?(?=##|\Z)", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


async def _ask_ba(question: str, task: Task) -> str:
    user_text = (
        f"## Контекст фичи:\n{task.raw_message}\n\n"
        f"## BA артефакт:\n{task.ba_text}\n\n"
        f"## Вопрос от SA:\n{question}"
    )
    logger.info(f"[SA→BA] {question[:80]}")
    try:
        answer = await call_claude(BA_ANSWER_SYSTEM, user_text, max_tokens=256, timeout=40)
        logger.info(f"[SA→BA] ответ: {answer[:80]}")
        return answer.strip()
    except Exception as e:
        logger.error(f"[SA→BA] ошибка: {e}")
        return "[ASSUMED] Используй стандартную банковскую практику"


async def run_sa(task: Task, bot, notify_chat_id: int) -> Task:
    logger.info(f"SA старт | {task.feature_name}")

    base_text = (
        f"## BA Артефакт:\n{task.ba_text}\n\n"
        f"## Исходный запрос:\n{task.raw_message}\n\n"
        f"## Figma:\n{task.figma_content or 'Не предоставлено'}"
    )

    clarifications = []
    prev_question = None
    response = ""

    for round_num in range(1, MAX_SA_TO_BA_ROUNDS + 2):
        user_text = base_text
        if clarifications:
            user_text += "\n\n## Уточнения от BA:\n" + "\n\n".join(clarifications)
        if round_num > MAX_SA_TO_BA_ROUNDS:
            user_text += "\n\n[ИНСТРУКЦИЯ: Финальный прогон. Вопросов не задавай. [ASSUMED] для всего неясного.]"

        content = build_content_with_attachment(user_text, task)
        try:
            response = await call_claude(SA_SYSTEM, content, max_tokens=8192)
        except Exception as e:
            logger.error(f"SA ошибка round {round_num}: {e}")
            raise

        question = _extract_sa_question(response)

        # Нет вопроса, финальный прогон, или повтор → выходим
        if not question or round_num > MAX_SA_TO_BA_ROUNDS or question == prev_question:
            break

        prev_question = question
        await bot.send_message(
            notify_chat_id,
            f"🔄 SA → BA ({round_num}/{MAX_SA_TO_BA_ROUNDS}):\n{question}",
        )
        ba_answer = await _ask_ba(question, task)
        clarifications.append(f"SA: {question}\nBA: {ba_answer}")
        await bot.send_message(
            notify_chat_id,
            f"✅ BA → SA:\n{ba_answer[:300]}{'...' if len(ba_answer)>300 else ''}",
        )

    artifact = _clean_sa_artifact(response)

    # Ревью
    await bot.send_message(notify_chat_id, "🔍 Проверяю качество SA артефакта...")
    try:
        import json
        raw_review = await call_claude(SA_REVIEWER_SYSTEM, artifact, max_tokens=512, timeout=40)
        clean = re.sub(r"```(?:json)?|```", "", raw_review).strip()
        review = json.loads(clean)
        score = review.get("score", 0)
        if not review.get("approved") and review.get("improvements"):
            imps = "\n".join(f"- {i}" for i in review["improvements"])
            improved_text = (
                f"{base_text}\n\n## Черновик SA:\n{artifact}\n\n"
                f"## Улучшения (score={score}/100):\n{imps}\n\n"
                f"[ИНСТРУКЦИЯ: Улучши артефакт. Вопросов не задавай.]"
            )
            content = build_content_with_attachment(improved_text, task)
            improved = await call_claude(SA_SYSTEM, content, max_tokens=8192)
            artifact = _clean_sa_artifact(improved)
            logger.info(f"SA улучшен после ревью (score={score})")
    except Exception as e:
        score = 0
        logger.warning(f"SA ревью не удалось: {e}")

    task.sa_text = artifact
    task.sa_summary = _extract_summary(artifact)
    await bot.send_message(notify_chat_id, f"✅ SA завершён (уточнений: {len(clarifications)}, качество: {score}/100)")
    await notify_sa_done(bot, notify_chat_id, task.sa_summary)
    return task


def _extract_summary(text: str) -> str:
    lines = [l.strip() for l in text.split("\n") if l.strip() and not l.startswith("#")]
    summary = " ".join(lines[:3])
    return summary[:200] + "..." if len(summary) > 200 else summary
