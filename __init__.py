import logging
from telegram import Bot
from models.task import Task

logger = logging.getLogger(__name__)


def format_new_task_message(task: Task) -> str:
    """
    Форматирует уведомление о новой задаче.
    Стиль аналогичен @QuestionsNBU_bot.
    """
    # Иконка модуля
    module_icons = {
        "переводы": "💸",
        "кредиты": "🏦",
        "карты": "💳",
        "депозиты": "💰",
        "общее": "📱",
        "unknown": "📋",
    }
    module = task.summary  # будет заменено полем из parser
    icon = "📋"

    # Иконка приоритета
    priority_icons = {"high": "🔴", "medium": "🟡", "low": "🟢", "unknown": "⚪"}

    # Иконка вложения
    attachment_info = ""
    if task.has_attachment:
        att_icons = {"pdf": "📄", "image": "🖼", "docx": "📝", "file": "📎"}
        att_icon = att_icons.get(task.attachment_type, "📎")
        attachment_info = f"\n{att_icon} Вложение: {task.attachment_type}"

    figma_info = ""
    if task.figma_url:
        figma_info = f"\n🎨 Figma: {task.figma_url}"

    message = (
        f"📋 Новая задача #{task.task_id}\n"
        f"🔵 FEATURE | 📱 {task.feature_name}\n"
        f"👤 Автор: {task.author_username} {task.author_name}\n"
        f"💬 {task.summary}"
        f"{attachment_info}"
        f"{figma_info}\n"
        f"\n⏳ Передаю на анализ...\n"
        f"/status {task.task_id}"
    )
    return message


def format_accepted_message(task: Task) -> str:
    """Подтверждение что задача принята ReleaseAgent"""
    return (
        f"✅ Задача #{task.task_id} принята в работу\n"
        f"🤖 @ReleaseAgent_Bot запущен\n"
        f"📊 Следите за прогрессом анализа"
    )


async def notify_group(bot: Bot, chat_id: int, task: Task) -> None:
    """Отправляет уведомление о новой задаче в группу-источник"""
    try:
        text = format_new_task_message(task)
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=None,  # plain text как у QuestionsNBU
        )
        logger.info(f"Уведомление отправлено в чат {chat_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления: {e}")


async def notify_accepted(bot: Bot, chat_id: int, task: Task) -> None:
    """Отправляет подтверждение принятия задачи"""
    try:
        text = format_accepted_message(task)
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        logger.error(f"Ошибка отправки подтверждения: {e}")
