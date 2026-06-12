import os
import json
import logging
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SHEETS_ID = os.getenv(
    "GOOGLE_SHEETS_ID",
    "1ZRqdWSBTXHPDwEoInGix7enElozPnB74mKkDshSd1zw"
)
SHEET_NAME = "Inform"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _get_service():
    """Создаёт Google Sheets клиент из переменной окружения"""
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "")

    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS_JSON не задан")

    creds_data = json.loads(creds_json)

    # Поддерживаем оба типа: service_account и oauth token
    if creds_data.get("type") == "service_account":
        creds = service_account.Credentials.from_service_account_info(
            creds_data, scopes=SCOPES
        )
    else:
        # OAuth2 токен (как у QuestionsNBU через google_token.json)
        creds = Credentials(
            token=creds_data.get("token"),
            refresh_token=creds_data.get("refresh_token"),
            token_uri=creds_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=creds_data.get("client_id"),
            client_secret=creds_data.get("client_secret"),
        )

    return build("sheets", "v4", credentials=creds, cache_discovery=False)


async def append_task(task) -> bool:
    """
    Добавляет строку задачи в лист Inform.
    Возвращает True при успехе.
    """
    try:
        service = _get_service()
        row = task.to_sheets_row()

        body = {"values": [row]}
        service.spreadsheets().values().append(
            spreadsheetId=SHEETS_ID,
            range=f"{SHEET_NAME}!A:P",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body=body,
        ).execute()

        logger.info(f"Задача {task.task_id} записана в Sheets")
        return True

    except Exception as e:
        logger.error(f"Ошибка записи в Sheets: {e}")
        return False


async def update_task_status(task_id: str, status: str, gdrive_url: str = "", notion_url: str = "") -> bool:
    """
    Обновляет статус задачи в Sheets по task_id (колонка A).
    Используется когда ReleaseAgent завершил работу.
    """
    try:
        service = _get_service()

        # Ищем строку по task_id
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEETS_ID,
            range=f"{SHEET_NAME}!A:A",
        ).execute()

        rows = result.get("values", [])
        row_index = None
        for i, row in enumerate(rows):
            if row and str(row[0]) == str(task_id):
                row_index = i + 1  # 1-indexed
                break

        if row_index is None:
            logger.warning(f"task_id {task_id} не найден в Sheets")
            return False

        # Обновляем: status (M), gdrive_url (O), notion_url (P)
        updates = [
            {
                "range": f"{SHEET_NAME}!M{row_index}",
                "values": [[status]],
            },
        ]
        if gdrive_url:
            updates.append({
                "range": f"{SHEET_NAME}!O{row_index}",
                "values": [[gdrive_url]],
            })
        if notion_url:
            updates.append({
                "range": f"{SHEET_NAME}!P{row_index}",
                "values": [[notion_url]],
            })

        service.spreadsheets().values().batchUpdate(
            spreadsheetId=SHEETS_ID,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()

        logger.info(f"Статус задачи {task_id} обновлён → {status}")
        return True

    except Exception as e:
        logger.error(f"Ошибка обновления статуса в Sheets: {e}")
        return False
