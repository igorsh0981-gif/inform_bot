import os
import logging
from telegram import Bot
from models.task import Task

logger = logging.getLogger(__name__)

BOT_CHAT_ID = int(os.getenv("BOT_CHAT_ID", "5281759957"))


# ── Уведомления InformNBU (приём задачи) ──────────────────────────────────────

def format_new_task_message(task: Task) -> str:
    attachment_info = ""
    if task.has_attachment:
        att_icons = {"pdf": "📄", "image": "🖼", "docx": "📝", "file": "📎"}
        att_icon = att_icons.get(task.attachment_type, "📎")
        attachment_info = f"\n{att_icon} Вложение: {task.attachment_type}"

    figma_info = ""
    if task.figma_url:
        figma_info = f"\n🎨 Figma: {task.figma_url}"

    return (
        f"📋 Новая задача #{task.task_id}\n"
        f"🔵 FEATURE | 📱 {task.feature_name}\n"
        f"👤 Автор: {task.author_username} {task.author_name}\n"
        f"💬 {task.summary}"
        f"{attachment_info}"
        f"{figma_info}\n"
        f"\n⏳ Запускаю анализ...\n"
        f"/status {task.task_id}"
    )


async def notify_self(bot: Bot, task: Task) -> None:
    """Уведомление о новой задаче — в личку боту"""
    try:
        await bot.send_message(chat_id=BOT_CHAT_ID, text=format_new_task_message(task))
        logger.info("Уведомление о задаче отправлено")
    except Exception as e:
        logger.error(f"Ошибка notify_self: {e}")


async def notify_accepted(bot: Bot, task: Task) -> None:
    try:
        await bot.send_message(
            chat_id=BOT_CHAT_ID,
            text=f"✅ Задача #{task.task_id} принята\n⛓️ Запускаю цепочку агентов..."
        )
    except Exception as e:
        logger.error(f"Ошибка notify_accepted: {e}")


# ── Уведомления ReleaseAgent (прогресс анализа) ───────────────────────────────

async def _send(bot: Bot, text: str) -> None:
    try:
        await bot.send_message(chat_id=BOT_CHAT_ID, text=text)
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")


async def notify_started(bot: Bot, chat_id: int, task: Task) -> None:
    await _send(bot,
        f"⏳ Анализ запущен\n"
        f"📋 {task.feature_name}\n"
        f"🆔 #{task.task_id}\n\n"
        f"▶️ BA агент стартует..."
    )


async def notify_ba_questions(bot: Bot, chat_id: int, questions: str, attempt: int) -> None:
    await _send(bot,
        f"❓ BA запрашивает уточнения (попытка {attempt}/5)\n\n"
        f"{questions}\n\n"
        f"⏰ Жду ответ 5 минут...\n"
        f"➡️ Ответьте текстом или используйте команды:\n"
        f"/skip — пропустить вопрос и продолжить\n"
        f"/stop — остановить анализ"
    )


async def notify_ba_timeout(bot: Bot, chat_id: int) -> None:
    await _send(bot, "⚠️ Ответ BA не получен (таймаут)\nПродолжаю с [ASSUMED]")


async def notify_ba_done(bot: Bot, chat_id: int, summary: str) -> None:
    await _send(bot, f"✅ BA завершён\n📄 {summary}\n\n➡️ Передаю SA...")


async def notify_sa_done(bot: Bot, chat_id: int, summary: str) -> None:
    await _send(bot, f"✅ SA завершён\n📄 {summary}\n\n➡️ Передаю QATC...")


async def notify_qatc_done(bot: Bot, chat_id: int, summary: str) -> None:
    await _send(bot, f"✅ QATC завершён\n📄 {summary}\n\n➡️ Передаю PM...")


async def notify_pm_done(bot: Bot, chat_id: int, summary: str) -> None:
    await _send(bot, f"✅ PM завершён\n📄 {summary}\n\n📦 Формирую артефакты...")


async def notify_packing(bot: Bot, chat_id: int) -> None:
    await _send(bot, "📦 Загружаю файлы в Google Drive и Notion...")


async def notify_done(bot: Bot, chat_id: int, task: Task) -> None:
    await _send(bot,
        f"✅ Все документы готовы\n"
        f"📁 {task.feature_name}\n"
        f"🔗 {task.gdrive_feature_folder_url}"
    )


async def notify_error(bot: Bot, chat_id: int, task_id: str, phase: str, error: str) -> None:
    await _send(bot,
        f"❌ Ошибка в фазе {phase}\n"
        f"Задача #{task_id}\n"
        f"Причина: {error}"
    )
