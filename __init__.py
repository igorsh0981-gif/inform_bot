from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Task:
    # Идентификация
    task_id: str                          # уникальный ID (message_id)
    timestamp: datetime = field(default_factory=datetime.utcnow)

    # Суть задачи
    feature_name: str = ""                # название фичи (Claude)
    raw_message: str = ""                 # исходное сообщение дословно
    summary: str = ""                     # суть задачи (Claude Haiku)

    # Автор
    author_username: str = ""             # @username
    author_name: str = ""                 # полное имя
    author_id: int = 0                    # Telegram user_id

    # Чат-источник
    chat_id: int = 0                      # ID чата
    chat_name: str = ""                   # название группы
    chat_type: str = ""                   # group / supergroup / private

    # Вложения
    has_attachment: bool = False
    attachment_type: str = ""             # pdf / image / docx / —
    attachment_file_id: str = ""          # Telegram file_id
    attachment_base64: str = ""           # base64 содержимое
    attachment_mime: str = ""             # MIME type

    # Figma
    figma_url: str = ""                   # ссылка на Figma
    figma_content: str = ""               # текстовое описание из Figma API

    # Статус и интеграции
    status: str = "pending"               # pending / in_progress / done / error
    release_agent_run_id: str = ""        # ID запуска в ReleaseAgent
    gdrive_folder_url: str = ""           # ссылка на папку GDrive
    notion_page_url: str = ""             # ссылка на страницу Notion

    def to_sheets_row(self) -> list:
        """Строка для записи в Google Sheets (лист Inform, 16 колонок)"""
        return [
            self.task_id,
            self.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            self.feature_name,
            self.raw_message[:500],       # обрезаем длинные сообщения
            self.summary,
            self.author_username,
            self.author_name,
            str(self.chat_id),
            self.chat_name,
            self.chat_type,
            str(self.has_attachment),
            self.attachment_type or "—",
            self.status,
            self.release_agent_run_id,
            self.gdrive_folder_url,
            self.notion_page_url,
        ]

    def to_agent_payload(self) -> dict:
        """JSON для передачи в @ReleaseAgent_Bot"""
        return {
            "task_id": self.task_id,
            "feature_name": self.feature_name,
            "raw_message": self.raw_message,
            "summary": self.summary,
            "author_username": self.author_username,
            "author_name": self.author_name,
            "chat_id": self.chat_id,
            "chat_name": self.chat_name,
            "timestamp": self.timestamp.isoformat(),
            "has_attachment": self.has_attachment,
            "attachment_type": self.attachment_type,
            "attachment_base64": self.attachment_base64,
            "attachment_mime": self.attachment_mime,
            "figma_url": self.figma_url,
            "figma_content": self.figma_content,
            "gdrive_folder_id": "1gK8_0-CjPPbNX1MsodfaZjuqXnOA7vjk",
        }
