from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Task:
    # Идентификация
    task_id: str
    timestamp: str = ""
    feature_name: str = ""
    raw_message: str = ""
    summary: str = ""

    # Автор и источник
    author_username: str = ""
    author_name: str = ""
    chat_id: int = 0
    chat_name: str = ""

    # Вложения
    has_attachment: bool = False
    attachment_type: str = ""
    attachment_base64: str = ""
    attachment_mime: str = ""

    # Figma
    figma_url: str = ""
    figma_content: str = ""

    # GDrive
    gdrive_folder_id: str = "1gK8_0-CjPPbNX1MsodfaZjuqXnOA7vjk"
    gdrive_feature_folder_id: str = ""
    gdrive_feature_folder_url: str = ""

    # Артефакты агентов
    ba_text: str = ""
    ba_summary: str = ""
    sa_text: str = ""
    sa_summary: str = ""
    qatc_text: str = ""
    qatc_summary: str = ""
    pm_text: str = ""
    pm_summary: str = ""

    # PM артефакты (6 файлов)
    pm_protocol: str = ""
    pm_jira: str = ""
    pm_epics: str = ""
    pm_risks: str = ""
    pm_raci: str = ""
    pm_team: str = ""
    pm_projekt_csv: str = ""
    pm_projekt_xlsx: bytes = field(default_factory=bytes)

    # Статус
    status: str = "in_progress"
    notion_page_url: str = ""

    # BA вопросы
    ba_questions_count: int = 0      # сколько раз BA задавал вопросы
    ba_answers: list = field(default_factory=list)  # собранные ответы

    @classmethod
    def from_payload(cls, payload: dict) -> "Task":
        """Создаёт Task из JSON переданного inform_bot"""
        return cls(
            task_id=str(payload.get("task_id", "")),
            timestamp=payload.get("timestamp", datetime.utcnow().isoformat()),
            feature_name=payload.get("feature_name", ""),
            raw_message=payload.get("raw_message", ""),
            summary=payload.get("summary", ""),
            author_username=payload.get("author_username", ""),
            author_name=payload.get("author_name", ""),
            chat_id=payload.get("chat_id", 0),
            chat_name=payload.get("chat_name", ""),
            has_attachment=payload.get("has_attachment", False),
            attachment_type=payload.get("attachment_type", ""),
            attachment_base64=payload.get("attachment_base64", ""),
            attachment_mime=payload.get("attachment_mime", ""),
            figma_url=payload.get("figma_url", ""),
            figma_content=payload.get("figma_content", ""),
            gdrive_folder_id=payload.get(
                "gdrive_folder_id", "1gK8_0-CjPPbNX1MsodfaZjuqXnOA7vjk"
            ),
        )

    def build_context(self) -> str:
        """Формирует общий контекст для агентов"""
        parts = [
            f"Задача: {self.feature_name}",
            f"Описание: {self.summary}",
            f"Исходное сообщение: {self.raw_message}",
        ]
        if self.figma_content:
            parts.append(f"Figma: {self.figma_content}")
        if self.ba_answers:
            parts.append(f"Уточнения от PM: {chr(10).join(self.ba_answers)}")
        return "\n".join(parts)
