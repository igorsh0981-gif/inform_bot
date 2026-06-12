import os
import io
import json
import logging
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

PARENT_FOLDER_ID = os.getenv(
    "GDRIVE_FOLDER_ID",
    "1gK8_0-CjPPbNX1MsodfaZjuqXnOA7vjk"
)
SCOPES = ["https://www.googleapis.com/auth/drive"]


def _get_service():
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS_JSON не задан")

    creds_data = json.loads(creds_json)
    if creds_data.get("type") == "service_account":
        creds = service_account.Credentials.from_service_account_info(
            creds_data, scopes=SCOPES
        )
    else:
        creds = Credentials(
            token=creds_data.get("token"),
            refresh_token=creds_data.get("refresh_token"),
            token_uri=creds_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=creds_data.get("client_id"),
            client_secret=creds_data.get("client_secret"),
        )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


async def create_feature_folder(feature_name: str) -> tuple[str, str]:
    """
    Создаёт папку /Запрос на разработку/{feature_name}/
    Возвращает (folder_id, folder_url)
    """
    try:
        service = _get_service()
        safe_name = feature_name[:100].strip()

        metadata = {
            "name": safe_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [PARENT_FOLDER_ID],
        }
        folder = service.files().create(
            body=metadata, fields="id"
        ).execute()

        folder_id = folder["id"]
        folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
        logger.info(f"Папка создана: {safe_name} → {folder_url}")
        return folder_id, folder_url

    except Exception as e:
        logger.error(f"Ошибка создания папки GDrive: {e}")
        return "", ""


async def upload_text_file(
    folder_id: str,
    filename: str,
    content: str,
) -> str:
    """Загружает текстовый файл (.md) в папку. Возвращает file_id."""
    try:
        service = _get_service()
        media = MediaIoBaseUpload(
            io.BytesIO(content.encode("utf-8")),
            mimetype="text/markdown",
        )
        metadata = {"name": filename, "parents": [folder_id]}
        file = service.files().create(
            body=metadata, media_body=media, fields="id"
        ).execute()
        logger.info(f"Загружен: {filename}")
        return file["id"]
    except Exception as e:
        logger.error(f"Ошибка загрузки {filename}: {e}")
        return ""


async def upload_bytes_file(
    folder_id: str,
    filename: str,
    content: bytes,
    mimetype: str,
) -> str:
    """Загружает бинарный файл (.xlsx) в папку. Возвращает file_id."""
    try:
        service = _get_service()
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mimetype)
        metadata = {"name": filename, "parents": [folder_id]}
        file = service.files().create(
            body=metadata, media_body=media, fields="id"
        ).execute()
        logger.info(f"Загружен: {filename}")
        return file["id"]
    except Exception as e:
        logger.error(f"Ошибка загрузки {filename}: {e}")
        return ""


async def upload_all_artifacts(task) -> bool:
    """
    Загружает все 14 артефактов в папку фичи.
    Возвращает True при успехе.
    """
    fid = task.gdrive_feature_folder_id
    tid = task.task_id
    fn = task.feature_name[:40].replace(" ", "_")

    files_md = [
        (f"ba_{fn}_{tid}.md",           task.ba_text),
        (f"ba_full_{fn}_{tid}.md",       task.ba_text),     # расширенная версия
        (f"sa_{fn}_{tid}.md",           task.sa_text),
        (f"sa_full_{fn}_{tid}.md",       task.sa_text),
        (f"qatc_{fn}_{tid}.md",         task.qatc_text),
        (f"qatc_full_{fn}_{tid}.md",    task.qatc_text),
        (f"pm_protocol_{fn}_{tid}.md",  task.pm_protocol),
        (f"pm_jira_{fn}_{tid}.md",      task.pm_jira),
        (f"pm_epics_{fn}_{tid}.md",     task.pm_epics),
        (f"pm_risks_{fn}_{tid}.md",     task.pm_risks),
        (f"pm_raci_{fn}_{tid}.md",      task.pm_raci),
        (f"pm_team_{fn}_{tid}.md",      task.pm_team),
        (f"projekt_tasks_{fn}_{tid}.csv", task.pm_projekt_csv),
    ]

    success = True
    for filename, content in files_md:
        if content:
            result = await upload_text_file(fid, filename, content)
            if not result:
                success = False

    # XLSX отдельно (бинарный)
    if task.pm_projekt_xlsx:
        result = await upload_bytes_file(
            fid,
            f"projekt_tasks_{fn}_{tid}.xlsx",
            task.pm_projekt_xlsx,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        if not result:
            success = False

    return success
