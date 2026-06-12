"""
@InformNBU_bot — бот-диспетчер задач на разработку
Слушает группы, определяет триггеры фич, извлекает суть,
уведомляет группу и передаёт задачу в @ReleaseAgent_Bot.
"""

import os
import logging
import asyncio
from datetime import datetime

from telegram import Update, Bot
from telegram.ext import (
    Application,
    MessageHandler,
    filters,
    ContextTypes,
)

from models.task import Task
from services.parser import extract_feature
from services.figma_reader import extract_figma_key, extract_figma_url, read_figma
from services.file_handler import detect_attachment, process_attachment
from services.sheets import append_task
from services.notifier import notify_group, notify_accepted
from services.dispatcher import dispatch_to_release_agent

# ── Логирование ────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Конфигурация ───────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    "8452455471:AAHsi4JaO_UOPXqIZWm2wUiCl4a1JZybP70"
)

# Триггеры — любое из слов в сообщении запускает обработку
FEATURE_TRIGGERS = [
    "фича", "feature",
    "реализовать", "реализация",
    "разработать", "разработка",
    "добавить функционал", "добавить функцию",
    "нужна функция", "нужен функционал",
    "сделать", "доработка", "доработать",
    "внедрить", "внедрение",
    "нужно реализовать", "требуется реализовать",
    "запрос на разработку",
    "новый функционал",
]

# Антитриггеры — если есть, пропускаем (вопросы, не задачи)
ANTI_TRIGGERS = [
    "?", "как ", "почему", "когда", "зачем",
    "что такое", "объясни",
]


def is_feature_trigger(text: str) -> bool:
    """Проверяет наличие триггера фичи в тексте"""
    text_lower = text.lower()

    # Проверяем антитриггеры
    if any(anti in text_lower for anti in ANTI_TRIGGERS):
        # Дополнительная проверка — триггер должен явно присутствовать
        has_explicit = any(
            t in text_lower for t in ["фича", "feature", "реализовать", "разработать"]
        )
        if not has_explicit:
            return False

    return any(trigger in text_lower for trigger in FEATURE_TRIGGERS)


async def handle_feature_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Основной хендлер триггерных сообщений.
    Полный цикл: извлечь → сохранить → уведомить → передать агенту.
    """
    message = update.message
    if not message or not message.text and not message.caption:
        return

    # Текст из обычного сообщения или подписи к файлу
    raw_text = message.text or message.caption or ""

    if not is_feature_trigger(raw_text):
        return

    logger.info(
        f"Триггер сработал | chat: {message.chat.title} | "
        f"user: {message.from_user.username} | text: {raw_text[:80]}"
    )

    # ── Сборка задачи ──────────────────────────────────────────────────────────
    task = Task(
        task_id=str(message.message_id),
        timestamp=datetime.utcnow(),
        raw_message=raw_text,
        author_username=f"@{message.from_user.username}" if message.from_user.username else "",
        author_name=f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip(),
        author_id=message.from_user.id,
        chat_id=message.chat.id,
        chat_name=message.chat.title or message.chat.username or "private",
        chat_type=message.chat.type,
    )

    # ── Figma ──────────────────────────────────────────────────────────────────
    figma_key = extract_figma_key(raw_text)
    if figma_key:
        task.figma_url = extract_figma_url(raw_text)
        logger.info(f"Figma URL найден: {task.figma_url}")
        task.figma_content = await read_figma(figma_key)

    # ── Вложение ───────────────────────────────────────────────────────────────
    file_id, att_type, mime_type = detect_attachment(message)
    if file_id:
        task.has_attachment = True
        task.attachment_type = att_type
        task.attachment_file_id = file_id
        task.attachment_mime = mime_type

        logger.info(f"Вложение: {att_type} ({mime_type})")
        b64, mime = await process_attachment(context.bot, file_id, mime_type)
        task.attachment_base64 = b64

    # ── Claude Haiku: извлечь суть ─────────────────────────────────────────────
    parsed = await extract_feature(raw_text, task.figma_content)
    task.feature_name = parsed.get("feature_name", raw_text[:50])
    task.summary = parsed.get("summary", raw_text[:300])

    # ── Уведомление в группу ───────────────────────────────────────────────────
    await notify_group(context.bot, message.chat.id, task)

    # ── Запись в Google Sheets ─────────────────────────────────────────────────
    task.status = "in_progress"
    await append_task(task)

    # ── Передача в @ReleaseAgent_Bot ───────────────────────────────────────────
    dispatched = await dispatch_to_release_agent(task)

    if dispatched:
        await notify_accepted(context.bot, message.chat.id, task)
        logger.info(f"Задача {task.task_id} успешно передана агенту")
    else:
        await context.bot.send_message(
            chat_id=message.chat.id,
            text=f"⚠️ Задача #{task.task_id} принята, но передача агенту не удалась. Попробуем повторно.",
        )


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /status {task_id} — заглушка, будет расширена"""
    if not context.args:
        await update.message.reply_text("Использование: /status {task_id}")
        return

    task_id = context.args[0]
    await update.message.reply_text(
        f"🔍 Статус задачи #{task_id}\n"
        f"Функция в разработке. Проверьте Google Sheets или Notion."
    )


def main() -> None:
    """Точка входа"""
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN не задан")

    logger.info("Запуск @InformNBU_bot...")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Хендлер триггеров фич — слушает текст и подписи к файлам
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
            handle_feature_message,
        )
    )

    # Хендлер файлов без текста (только документ/фото без подписи)
    app.add_handler(
        MessageHandler(
            (filters.Document.ALL | filters.PHOTO) & ~filters.CAPTION,
            handle_feature_message,
        )
    )

    logger.info("Бот запущен. Ожидаю сообщения...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
