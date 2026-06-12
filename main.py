"""
@InformNBU_bot — единый бот-диспетчер и оркестратор агентов
Слушает группы, извлекает суть фичи, запускает BA→SA→QATC→PM напрямую.
"""

import os
import logging
import asyncio
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
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
from agents.tz_biz_agent import run_tz_biz
from agents.tz_sys_agent import run_tz_sys

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

# Очереди ответов { task_id: Queue }
answer_queues: dict[str, asyncio.Queue] = {}


def is_feature_trigger(text: str) -> bool:
    text_lower = text.lower()
    if any(anti in text_lower for anti in ANTI_TRIGGERS):
        has_explicit = any(
            t in text_lower for t in ["фича", "feature", "реализовать", "разработать"]
        )
        if not has_explicit:
            return False
    return any(trigger in text_lower for trigger in FEATURE_TRIGGERS)


async def handle_pm_text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Ответы PM текстом на вопросы BA/SA/QATC — только из личного чата BOT_CHAT_ID.
    Этот хендлер регистрируется ПЕРВЫМ для личного чата.
    """
    message = update.message
    if not message:
        return

    logger.info(f"[PM_REPLY] chat_id={message.chat.id} BOT_CHAT_ID={BOT_CHAT_ID}")

    if message.chat.id != BOT_CHAT_ID:
        return

    if not answer_queues:
        # Нет активных задач — может быть новая фича из личного чата
        await handle_feature_message(update, context)
        return

    text = message.text or ""
    # Кладём ответ в первую активную очередь
    for task_id, queue in answer_queues.items():
        await queue.put(text)
        logger.info(f"[PM_REPLY] Ответ получен для задачи {task_id}: {text[:50]}")
        break


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатие на inline-кнопку вариантов ответа"""
    query = update.callback_query
    if not query:
        return

    await query.answer()
    data = query.data or ""

    if not data.startswith("ba_opt:"):
        return

    value = data[len("ba_opt:"):]

    if value == "__custom__":
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(
            chat_id=query.message.chat.id,
            text="✍️ Напишите ответ текстом:",
        )
        return

    # Кладём выбранный вариант в очередь
    if answer_queues:
        for task_id, queue in answer_queues.items():
            await queue.put(value)
            logger.info(f"[CALLBACK] Вариант выбран для задачи {task_id}: {value[:50]}")
            break
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=f"✅ Принято: {value}",
        )


async def handle_feature_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ловит триггеры фич из ГРУПП (или личного чата если нет активных задач)"""
    message = update.message
    if not message:
        return

    raw_text = message.text or message.caption or ""

    # Из личного чата — только если нет активных задач
    if message.chat.id == BOT_CHAT_ID and answer_queues:
        return

    if not is_feature_trigger(raw_text):
        return

    logger.info(f"Триггер | chat: {message.chat.title or 'private'} | user: {message.from_user.username}")

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

    figma_key = extract_figma_key(raw_text)
    if figma_key:
        task.figma_url = extract_figma_url(raw_text)
        task.figma_content = await read_figma(figma_key)

    file_id, att_type, mime_type = detect_attachment(message)
    if file_id:
        task.has_attachment = True
        task.attachment_type = att_type
        task.attachment_mime = mime_type
        b64, _ = await process_attachment(context.bot, file_id, mime_type)
        task.attachment_base64 = b64

    parsed = await extract_feature(raw_text, task.figma_content)
    task.feature_name = parsed.get("feature_name", raw_text[:50])
    task.summary = parsed.get("summary", raw_text[:300])

    await notify_self(context.bot, task)

    task.status = "in_progress"
    await append_task(task)

    answer_queue = asyncio.Queue()
    answer_queues[task.task_id] = answer_queue
    asyncio.create_task(run_chain(task, context.bot, answer_queue))


async def handle_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/skip — пропустить текущий вопрос агента"""
    message = update.message
    if not message or message.chat.id != BOT_CHAT_ID:
        return

    if not answer_queues:
        await message.reply_text("⚠️ Нет активных задач ожидающих ответа.")
        return

    for task_id, queue in answer_queues.items():
        await queue.put("[SKIP — пользователь пропустил вопрос]")
        await message.reply_text(
            f"⏩ Вопрос пропущен для задачи #{task_id}\n"
            f"Продолжаю анализ с допущениями [ASSUMED]..."
        )
        logger.info(f"Вопрос пропущен для задачи {task_id}")
        break


async def handle_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stop — остановить анализ"""
    message = update.message
    if not message or message.chat.id != BOT_CHAT_ID:
        return

    if not answer_queues:
        await message.reply_text("⚠️ Нет активных задач для остановки.")
        return

    for task_id, queue in answer_queues.items():
        await queue.put("[STOP — пользователь остановил анализ]")
        await message.reply_text(f"🛑 Анализ задачи #{task_id} остановлен.")
        logger.info(f"Анализ остановлен для задачи {task_id}")
        break


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — активные задачи"""
    message = update.message
    if not message or message.chat.id != BOT_CHAT_ID:
        return

    if not answer_queues:
        await message.reply_text("✅ Нет активных задач в обработке.")
        return

    tasks_list = "\n".join([f"  • #{tid}" for tid in answer_queues.keys()])
    await message.reply_text(
        f"⚙️ Активные задачи:\n{tasks_list}\n\n"
        f"/skip — пропустить вопрос\n"
        f"/stop — остановить анализ"
    )


async def run_chain(task: Task, bot, answer_queue: asyncio.Queue) -> None:
    """BA → SA → QATC → PM → TZ → GDrive → Notion"""
    chat_id = BOT_CHAT_ID
    logger.info(f"[CHAIN] Старт: {task.feature_name}")

    try:
        await notify_started(bot, chat_id, task)

        # GDrive папка
        folder_id, folder_url = await create_feature_folder(task.feature_name)
        task.gdrive_feature_folder_id = folder_id
        task.gdrive_feature_folder_url = folder_url
        if not folder_id:
            logger.error("[CHAIN] GDrive папка не создана!")
            await bot.send_message(chat_id, "⚠️ Не удалось создать папку в GDrive. Проверьте credentials.")

        # Notion страница
        notion_url = await create_feature_page(task)
        task.notion_page_url = notion_url
        if not notion_url:
            logger.warning("[CHAIN] Notion страница не создана")

        # BA (с очередью ответов PM)
        task = await run_ba(task, bot, chat_id, answer_queue)

        # SA (уточняет у BA самостоятельно — без PM)
        task = await run_sa(task, bot, chat_id)

        # QATC (уточняет у SA и BA самостоятельно — без PM)
        task = await run_qatc(task, bot, chat_id)

        # PM
        task = await run_pm(task, bot, chat_id)

        # ТЗ (параллельно)
        tz_results = await asyncio.gather(
            run_tz_biz(task),
            run_tz_sys(task),
            return_exceptions=True,
        )
        task.pm_template_1 = tz_results[0] if not isinstance(tz_results[0], Exception) else ""
        task.pm_template_2 = tz_results[1] if not isinstance(tz_results[1], Exception) else ""

        # Упаковка в GDrive
        await notify_packing(bot, chat_id)
        if folder_id:
            upload_ok = await upload_all_artifacts(task)
            if not upload_ok:
                await bot.send_message(chat_id, "⚠️ Часть файлов не загружена в GDrive — проверьте логи.")
        else:
            await bot.send_message(chat_id, "❌ GDrive папка недоступна — файлы не загружены.")

        # Notion обновление
        if notion_url:
            await update_feature_page(notion_url, task)
        else:
            logger.warning("[CHAIN] Notion URL отсутствует — пропускаем обновление")

        await notify_done(bot, chat_id, task)
        logger.info(f"[CHAIN] Завершено: {task.feature_name}")

    except InterruptedError as e:
        await bot.send_message(chat_id, f"🛑 Анализ остановлен: {e}")
        logger.info(f"[CHAIN] Остановлен: {e}")

    except Exception as e:
        logger.error(f"[CHAIN] Ошибка: {e}", exc_info=True)
        await notify_error(bot, chat_id, task.task_id, "SYSTEM", str(e))

    finally:
        answer_queues.pop(task.task_id, None)


def main() -> None:
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN не задан")

    logger.info("Запуск @InformNBU_bot...")

    import time
    import httpx

    try:
        httpx.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook",
            params={"drop_pending_updates": True},
            timeout=10,
        )
        logger.info("Webhook сброшен")
    except Exception as e:
        logger.warning(f"Не удалось сбросить webhook: {e}")

    time.sleep(3)

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .build()
    )

    # ПОРЯДОК ВАЖЕН: более специфичные хендлеры — первыми

    # 1. Inline-кнопки (варианты ответа)
    app.add_handler(CallbackQueryHandler(handle_callback_query, pattern="^ba_opt:"))

    # 2. Команды в личке
    app.add_handler(CommandHandler("skip", handle_skip))
    app.add_handler(CommandHandler("stop", handle_stop))
    app.add_handler(CommandHandler("status", handle_status))

    # 3. Текстовые ответы PM из личного чата (ПЕРВЫЙ приоритет для личного чата)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Chat(BOT_CHAT_ID),
        handle_pm_text_reply,
    ))

    # 4. Триггеры фич из групп (и из личного чата если нет активных задач)
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
        handle_feature_message,
    ))

    # 5. Файлы без подписи
    app.add_handler(MessageHandler(
        (filters.Document.ALL | filters.PHOTO) & ~filters.CAPTION,
        handle_feature_message,
    ))

    logger.info(f"Бот запущен. BOT_CHAT_ID={BOT_CHAT_ID}")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
