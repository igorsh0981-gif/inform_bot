"""
@InformNBU_bot — единый бот-диспетчер и оркестратор агентов
Слушает группы, извлекает суть фичи, запускает BA→SA→QATC→PM напрямую.
"""

import os
import logging
import asyncio
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from models.task import Task
from services.parser import extract_feature
from services.figma_reader import extract_figma_key, extract_figma_url, read_figma
from services.file_handler import detect_attachment, process_attachment
from services.sheets import append_task
from services.notifier import (
    notify_self, notify_accepted,
    notify_started, notify_ba_questions, notify_ba_timeout,
    notify_ba_done, notify_sa_done, notify_qatc_done,
    notify_pm_done, notify_packing, notify_done, notify_error
)
from services.gdrive import create_feature_folder, upload_all_artifacts
from services.notion import create_feature_page, update_feature_page
from agents.ba_agent import run_ba
from agents.sa_agent import run_sa
from agents.qatc_agent import run_qatc
from agents.pm_agent import run_pm

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    "8452455471:AAHsi4JaO_UOPXqIZWm2wUiCl4a1JZybP70"
)
BOT_CHAT_ID = int(os.getenv("BOT_CHAT_ID", "5281759957"))

FEATURE_TRIGGERS = [
    "фича", "feature",
    "реализовать", "реализация",
    "разработать", "разработка",
    "добавить функционал", "добавить функцию",
    "нужна функция", "нужен функционал",
    "доработка", "доработать",
    "внедрить", "внедрение",
    "нужно реализовать", "требуется реализовать",
    "запрос на разработку",
    "новый функционал",
]

ANTI_TRIGGERS = ["?", "как ", "почему", "когда", "зачем", "что такое", "объясни"]

# Очереди ответов BA { task_id: Queue }
ba_answer_queues: dict[str, asyncio.Queue] = {}


def is_feature_trigger(text: str) -> bool:
    text_lower = text.lower()
    if any(anti in text_lower for anti in ANTI_TRIGGERS):
        has_explicit = any(
            t in text_lower for t in ["фича", "feature", "реализовать", "разработать"]
        )
        if not has_explicit:
            return False
    return any(trigger in text_lower for trigger in FEATURE_TRIGGERS)


async def handle_feature_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return

    raw_text = message.text or message.caption or ""
    if not is_feature_trigger(raw_text):
        return

    logger.info(f"Триггер | chat: {message.chat.title} | user: {message.from_user.username}")

    # Сборка задачи
    task = Task(
        task_id=str(message.message_id),
        timestamp=datetime.now(timezone.utc).isoformat(),
        raw_message=raw_text,
        author_username=f"@{message.from_user.username}" if message.from_user.username else "",
        author_name=f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip(),
        chat_id=message.chat.id,
        chat_name=message.chat.title or "private",
        chat_type=message.chat.type,
    )

    # Figma
    figma_key = extract_figma_key(raw_text)
    if figma_key:
        task.figma_url = extract_figma_url(raw_text)
        task.figma_content = await read_figma(figma_key)

    # Вложение
    file_id, att_type, mime_type = detect_attachment(message)
    if file_id:
        task.has_attachment = True
        task.attachment_type = att_type
        task.attachment_mime = mime_type
        b64, _ = await process_attachment(context.bot, file_id, mime_type)
        task.attachment_base64 = b64

    # Claude: извлечь суть
    parsed = await extract_feature(raw_text, task.figma_content)
    task.feature_name = parsed.get("feature_name", raw_text[:50])
    task.summary = parsed.get("summary", raw_text[:300])

    # Уведомление себе
    await notify_self(context.bot, task)

    # Запись в Sheets
    task.status = "in_progress"
    await append_task(task)

    # Запуск цепочки агентов в фоне
    answer_queue = asyncio.Queue()
    ba_answer_queues[task.task_id] = answer_queue
    asyncio.create_task(run_chain(task, context.bot, answer_queue))


async def handle_ba_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ответы пользователя на вопросы BA — только в личке с ботом"""
    message = update.message
    if not message or message.chat.id != BOT_CHAT_ID:
        return

    text = message.text or ""
    for task_id, queue in ba_answer_queues.items():
        await queue.put(text)
        logger.info(f"BA ответ получен для задачи {task_id}")
        break


async def handle_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /skip — продолжить анализ без ответа на вопросы BA.
    Кладёт специальный маркер в очередь.
    """
    message = update.message
    if not message or message.chat.id != BOT_CHAT_ID:
        return

    if not ba_answer_queues:
        await message.reply_text("Нет активных задач ожидающих ответа.")
        return

    for task_id, queue in ba_answer_queues.items():
        await queue.put("[SKIP — пользователь пропустил вопрос]")
        await message.reply_text(
            f"⏩ Пропускаю вопросы BA для задачи #{task_id}\n"
            f"Продолжаю анализ с допущениями [ASSUMED]..."
        )
        logger.info(f"BA вопрос пропущен для задачи {task_id}")
        break


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /status — показывает активные задачи"""
    message = update.message
    if not message or message.chat.id != BOT_CHAT_ID:
        return

    if not ba_answer_queues:
        await message.reply_text("Нет активных задач в обработке.")
        return

    tasks_list = "\n".join([f"  • #{tid}" for tid in ba_answer_queues.keys()])
    await message.reply_text(
        f"⚙️ Активные задачи:\n{tasks_list}\n\n"
        f"Для пропуска вопросов BA: /skip"
    )


async def run_chain(task: Task, bot, answer_queue: asyncio.Queue) -> None:
    """BA → SA → QATC → PM → GDrive → Notion"""
    chat_id = BOT_CHAT_ID
    logger.info(f"[CHAIN] Старт: {task.feature_name}")

    try:
        await notify_started(bot, chat_id, task)

        # GDrive папка
        folder_id, folder_url = await create_feature_folder(task.feature_name)
        task.gdrive_feature_folder_id = folder_id
        task.gdrive_feature_folder_url = folder_url

        # Notion страница
        notion_url = await create_feature_page(task)
        task.notion_page_url = notion_url

        # BA
        task = await run_ba(task, bot, chat_id, answer_queue)

        # SA
        task = await run_sa(task, bot, chat_id)

        # QATC
        task = await run_qatc(task, bot, chat_id)

        # PM
        task = await run_pm(task, bot, chat_id)

        # Упаковка
        await notify_packing(bot, chat_id)
        await upload_all_artifacts(task)

        if notion_url:
            await update_feature_page(notion_url, task)

        await notify_done(bot, chat_id, task)
        logger.info(f"[CHAIN] Завершено: {task.feature_name}")

    except Exception as e:
        logger.error(f"[CHAIN] Ошибка: {e}", exc_info=True)
        await notify_error(bot, chat_id, task.task_id, "SYSTEM", str(e))

    finally:
        ba_answer_queues.pop(task.task_id, None)


def main() -> None:
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN не задан")

    logger.info("Запуск @InformNBU_bot...")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Команды в личке бота
    app.add_handler(CommandHandler("skip", handle_skip))
    app.add_handler(CommandHandler("status", handle_status))

    # Триггеры фич из групп
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
        handle_feature_message,
    ))

    # Файлы без подписи
    app.add_handler(MessageHandler(
        (filters.Document.ALL | filters.PHOTO) & ~filters.CAPTION,
        handle_feature_message,
    ))

    # Ответы на вопросы BA (только из личного чата)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Chat(BOT_CHAT_ID),
        handle_ba_answer,
    ))

    logger.info("Бот запущен. Ожидаю сообщения...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
