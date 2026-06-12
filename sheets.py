import base64
import logging
from telegram import Message, Bot

logger = logging.getLogger(__name__)

# Поддерживаемые типы файлов
SUPPORTED_MIME = {
    "application/pdf": "pdf",
    "image/png": "image",
    "image/jpeg": "image",
    "image/jpg": "image",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "docx",
}


def detect_attachment(message: Message) -> tuple[str, str, str]:
    """
    Определяет тип вложения в сообщении.
    Возвращает (file_id, attachment_type, mime_type)
    """
    if message.document:
        doc = message.document
        mime = doc.mime_type or ""
        att_type = SUPPORTED_MIME.get(mime, "file")
        return doc.file_id, att_type, mime

    if message.photo:
        # Берём фото с максимальным разрешением
        photo = message.photo[-1]
        return photo.file_id, "image", "image/jpeg"

    return "", "", ""


async def download_file(bot: Bot, file_id: str) -> tuple[bytes, str]:
    """
    Скачивает файл из Telegram.
    Возвращает (bytes содержимое, file_path)
    """
    try:
        file_info = await bot.get_file(file_id)
        file_path = file_info.file_path

        # Скачиваем через встроенный метод
        file_bytes = await file_info.download_as_bytearray()
        return bytes(file_bytes), file_path

    except Exception as e:
        logger.error(f"Ошибка скачивания файла {file_id}: {e}")
        return b"", ""


async def process_attachment(bot: Bot, file_id: str, mime_type: str) -> tuple[str, str]:
    """
    Полный цикл: скачать → base64.
    Возвращает (base64_string, mime_type)
    """
    if not file_id:
        return "", ""

    content, _ = await download_file(bot, file_id)
    if not content:
        return "", ""

    b64 = base64.b64encode(content).decode("utf-8")
    logger.info(f"Файл обработан: {len(content)} байт → base64 {len(b64)} символов")
    return b64, mime_type
