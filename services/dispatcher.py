import os
import json
import logging
from telegram import Bot
from models.task import Task

logger = logging.getLogger(__name__)

INFORM_BOT_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    "8452455471:AAHsi4JaO_UOPXqIZWm2wUiCl4a1JZybP70"
)
RELEASE_AGENT_CHAT_ID = os.getenv("RELEASE_AGENT_CHAT_ID", "5281759957")


async def dispatch_to_release_agent(task: Task) -> bool:
    try:
        bot = Bot(token=INFORM_BOT_TOKEN)
        payload = task.to_agent_payload()
        payload_json = json.dumps(payload, ensure_ascii=False, indent=2)

        command_text = f"/start_analysis {task.task_id}"
        await bot.send_message(
            chat_id=int(RELEASE_AGENT_CHAT_ID),
            text=command_text,
        )
        logger.info(f"[DISPATCH] Команда отправлена: {command_text}")

        filename = f"task_{task.task_id}.json"
        await bot.send_document(
            chat_id=int(RELEASE_AGENT_CHAT_ID),
            document=payload_json.encode("utf-8"),
            filename=filename,
            caption=f"Задача: {task.feature_name}",
        )
        logger.info(f"[DISPATCH] JSON отправлен: {filename}")

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
            await bot.send_document(
                chat_id=int(RELEASE_AGENT_CHAT_ID),
                document=file_bytes,
                filename=f"attachment_{task.task_id}.{ext}",
                caption=f"Вложение к задаче #{task.task_id}",
            )

        logger.info(f"[DISPATCH] Задача {task.task_id} передана")
        return True

    except Exception as e:
        logger.error(f"[DISPATCH] Ошибка: {e}")
        return False
