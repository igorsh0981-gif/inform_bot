"""
Общие утилиты для агентов.
"""

MAX_NOTIFICATION_LENGTH = 300
MAX_FOLDER_NAME_LENGTH = 100
REVIEWER_SAMPLE_SIZE = 4000


def extract_summary(text: str, max_length: int = 200) -> str:
    """Извлекает краткое резюме из текста артефакта."""
    lines = [l.strip() for l in text.split("\n") if l.strip() and not l.startswith("#")]
    summary = " ".join(lines[:3])
    return summary[:max_length] + "..." if len(summary) > max_length else summary
