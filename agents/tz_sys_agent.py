"""
TZ_SYS Agent — генерация Системного Технического Задания (SRS)
Заполняет шаблон ТЗ_Системное на основе SA + BA артефактов.
"""

import os
import logging
from pathlib import Path
from models.task import Task
from services.claude_client import call_claude

logger = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "tz_sys.md"


def _load_template() -> str:
    try:
        return TEMPLATE_PATH.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Ошибка загрузки шаблона ТЗ Системное: {e}")
        return ""


TZ_SYS_SYSTEM = """Ты — опытный системный аналитик банковского мобильного приложения НБУ (Национальный банк Узбекистана).

Твоя задача: заполнить шаблон Системного Технического Задания (SRS) на основе SA и BA артефактов.

## Правила заполнения:
- Заменяй все placeholder'ы [Название], [описание] и т.д. реальными данными из SA и BA артефактов
- Сохраняй структуру и форматирование шаблона полностью
- API endpoints бери из SA артефакта
- Модель данных (ERD) бери из SA артефакта
- User Stories и UC бери из BA артефакта
- Для полей которые невозможно определить — ставь [ТРЕБУЕТ УТОЧНЕНИЯ]
- Номер документа: SRS-001-2026
- Родительский документ: ТЗ-001-2026
- Название приложения: Milliy (мобильное приложение НБУ)
- Заказчик: Национальный банк Узбекистана (НБУ)
- Стек: Java Spring Boot (backend), Kotlin (Android), Swift (iOS), PostgreSQL, Redis
- Пиши на русском языке
- Верни ТОЛЬКО заполненный документ в формате Markdown, без пояснений
"""


async def run_tz_sys(task: Task) -> str:
    """
    Генерирует заполненное Системное ТЗ (SRS).
    Возвращает строку с Markdown документом.
    """
    logger.info(f"TZ_SYS старт | задача: {task.feature_name}")

    template = _load_template()
    if not template:
        return "# Ошибка: шаблон ТЗ_Системное не найден"

    user_text = (
        f"## Шаблон для заполнения:\n\n{template}\n\n"
        f"---\n\n"
        f"## BA Артефакт:\n\n{task.ba_text}\n\n"
        f"## SA Артефакт:\n\n{task.sa_text}\n\n"
        f"## Название фичи: {task.feature_name}\n"
        f"## Краткое описание: {task.summary}"
    )

    try:
        result = await call_claude(TZ_SYS_SYSTEM, user_text, max_tokens=8192, timeout=180)
        logger.info("TZ_SYS завершён")
        return result
    except Exception as e:
        logger.error(f"TZ_SYS ошибка: {e}")
        return f"# Ошибка генерации Системного ТЗ\n\n{e}"
