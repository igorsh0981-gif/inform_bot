"""
TZ_BIZ Agent — генерация Бизнесового Технического Задания
Заполняет шаблон ТЗ_Бизнесовое на основе BA артефакта.
"""

import os
import logging
from pathlib import Path
from models.task import Task
from services.claude_client import call_claude

logger = logging.getLogger(__name__)

# Загружаем шаблон из файла
TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "tz_biz.md"


def _load_template() -> str:
    try:
        return TEMPLATE_PATH.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Ошибка загрузки шаблона ТЗ Бизнесовое: {e}")
        return ""


TZ_BIZ_SYSTEM = """Ты — опытный бизнес-аналитик банковского мобильного приложения НБУ (Национальный банк Узбекистана).

Твоя задача: заполнить шаблон Бизнесового Технического Задания (ТЗ) на основе BA артефакта.

## Правила заполнения:
- Заменяй все placeholder'ы в квадратных скобках [Название фичи], [описание] и т.д. реальными данными из BA артефакта
- Сохраняй структуру и форматирование шаблона полностью
- Для полей которые невозможно определить из BA — ставь [ТРЕБУЕТ УТОЧНЕНИЯ]
- Даты ставь в формате ДД.ММ.ГГГГ, если конкретные даты неизвестны — [ДД.ММ.ГГГГ]
- Номер документа: ТЗ-001-2026
- Название приложения: Milliy (мобильное приложение НБУ)
- Заказчик: Национальный банк Узбекистана (НБУ)
- Исполнитель: Команда разработки НБУ Мобайл
- Пиши на русском языке
- Верни ТОЛЬКО заполненный документ в формате Markdown, без пояснений
"""


async def run_tz_biz(task: Task) -> str:
    """
    Генерирует заполненное Бизнесовое ТЗ.
    Возвращает строку с Markdown документом.
    """
    logger.info(f"TZ_BIZ старт | задача: {task.feature_name}")

    template = _load_template()
    if not template:
        return "# Ошибка: шаблон ТЗ_Бизнесовое не найден"

    user_text = (
        f"## Шаблон для заполнения:\n\n{template}\n\n"
        f"---\n\n"
        f"## BA Артефакт (источник данных):\n\n{task.ba_text}\n\n"
        f"## Исходный запрос:\n{task.raw_message}\n\n"
        f"## Название фичи: {task.feature_name}\n"
        f"## Краткое описание: {task.summary}"
    )

    try:
        result = await call_claude(TZ_BIZ_SYSTEM, user_text, max_tokens=8192, timeout=180)
        logger.info("TZ_BIZ завершён")
        return result
    except Exception as e:
        logger.error(f"TZ_BIZ ошибка: {e}")
        return f"# Ошибка генерации Бизнесового ТЗ\n\n{e}"
