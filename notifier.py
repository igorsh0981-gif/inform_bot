import os
import json
import logging
from telegram import Bot
from models.task import Task

logger = logging.getLogger(__name__)

RELEASE_AGENT_BOT_TOKEN = os.getenv(
    "RELEASE_AGENT_BOT_TOKEN",
    "8816881404:AAFapEGQyxBLuBMCDPZYtfXlFsfqCqtjq10"
)
RELEASE_AGENT_CHAT_ID = os.getenv("RELEASE_AGENT_CHAT_ID", "")


async def dispatch_to_release_agent(task: Task) -> bool:
    """
    Передаёт задачу в @ReleaseAgent_Bot через Telegram.
    Отправляет команду /start_analysis + JSON файл с артефактом.
    """
    if not RELEASE_AGENT_CHAT_ID:
        logger.error("RELEASE_AGENT_CHAT_ID не задан в переменных окружения")
        return False

    try:
        bot = Bot(token=RELEASE_AGENT_BOT_TOKEN)
        payload = task.to_agent_payload()
        payload_json = json.dumps(payload, ensure_ascii=False, indent=2)

        # Шаг 1: команда запуска
        command_text = f"/start_analysis {task.task_id}"
        await bot.send_message(
            chat_id=int(RELEASE_AGENT_CHAT_ID),
            text=command_text,
        )

        # Шаг 2: JSON артефакт как файл
        filename = f"task_{task.task_id}.json"
        await bot.send_document(
            chat_id=int(RELEASE_AGENT_CHAT_ID),
            document=payload_json.encode("utf-8"),
            filename=filename,
            caption=f"📦 Задача: {task.feature_name}",
        )

        # Шаг 3: если есть вложение — переслать и его
        if task.attachment_base64 and task.attachment_mime:
            import base64
            file_bytes = base64.b64decode(task.attachment_base64)
            ext_map = {
                "application/pdf": "pdf",
                "image/png": "png",
                "image/jpeg": "jpg",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
            }
            ext = ext_map.get(task.attachment_mime, "bin")
            att_filename = f"attachment_{task.task_id}.{ext}"

            await bot.send_document(
                chat_id=int(RELEASE_AGENT_CHAT_ID),
                document=file_bytes,
                filename=att_filename,
                caption=f"📎 Вложение к задаче #{task.task_id}",
            )

        logger.info(f"Задача {task.task_id} передана в ReleaseAgent")
        return True

    except Exception as e:
        logger.error(f"Ошибка передачи в ReleaseAgent: {e}")
        return False
