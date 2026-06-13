"""
SA Agent — System Analyst
Один прогон без межагентных уточнений.
"""

import asyncio
import logging
import re
from models.task import Task
from services.claude_client import call_claude, build_content_with_attachment
from services.notifier import notify_sa_done
from services.utils import extract_summary

logger = logging.getLogger(__name__)

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
Минимум 3 endpoint'а.

### 3. Модель данных
```sql
CREATE TABLE [name] (
  id UUID PRIMARY KEY,
  ...
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ...
```

### 4. Sequence диаграмма
```
Mobile App → API Gateway → [Service] → PostgreSQL/Redis → [External]
```

### 5. Нефункциональные требования
| Параметр | Требование | Обоснование |
|---|---|---|
| Response time P95 | < 500ms | банковский стандарт |
| Throughput | X RPS | расчёт от DAU |
| Availability | 99.9% | SLA НБУ |

### 6. Безопасность
- Аутентификация, авторизация, маскирование данных в логах, audit log

### 7. Оценка трудоёмкости
| Компонент | Backend | Mobile iOS | Mobile Android | QA |
|---|---|---|---|---|
| [компонент] | X дней | X дней | X дней | X дней |
| **Итого** | | | | |

### 8. Риски реализации
| Риск | Вероятность | Влияние | Митигация |

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## ПРАВИЛА
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Стек: Java 17 Spring Boot 3, Kotlin (Android), Swift (iOS), PostgreSQL 15, Redis 7, Kafka
- Поля [TBD]/[UNK] из BA — принимай как [ASSUMED] с разумным допущением
- НЕ задавай вопросов — формируй финальный артефакт с [ASSUMED] для неясного
- Пиши на русском, термины API/SQL — на английском
"""


async def run_sa(task: Task, bot, notify_chat_id: int) -> Task:
    logger.info(f"SA старт | {task.feature_name}")

    user_text = (
        f"## BA Артефакт:\n{task.ba_text}\n\n"
        f"## Исходный запрос:\n{task.raw_message}\n\n"
        f"## Figma:\n{task.figma_content or 'Не предоставлено'}"
    )

    content = build_content_with_attachment(user_text, task)

    try:
        response = await call_claude(SA_SYSTEM, content, max_tokens=16000, timeout=300)
    except Exception as e:
        logger.error(f"SA ошибка: {e}")
        raise

    task.sa_text = response.strip()
    task.sa_summary = extract_summary(response)

    await notify_sa_done(bot, notify_chat_id, task.sa_summary)
    logger.info("SA завершён")
    return task
