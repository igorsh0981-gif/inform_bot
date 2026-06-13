"""
GDrive service — загрузка через n8n webhook (OAuth2, без Service Account).
n8n workflow: Webhook → Prepare → Move Base64 to File → Google Drive → Respond OK
"""
import os
import base64
import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)

PARENT_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID", "1gK8_0-CjPPbNX1MsodfaZjuqXnOA7vjk")
N8N_GDRIVE_WEBHOOK = os.getenv(
    "N8N_GDRIVE_WEBHOOK",
    "https://fox81.app.n8n.cloud/webhook/gdrive-upload"
)

# Для создания папки всё ещё используем Drive API через Service Account
# (создание папки — только метаданные, квота не нужна)
import io
import json
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials

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


def _create_folder_sync(feature_name: str) -> tuple[str, str]:
    """Создаёт папку через Drive API (только метаданные — квота не нужна)."""
    service = _get_service()
    metadata = {
        "name": feature_name[:100].strip(),
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [PARENT_FOLDER_ID],
    }
    folder = service.files().create(
        body=metadata, fields="id", supportsAllDrives=True
    ).execute()
    folder_id = folder["id"]
    return folder_id, f"https://drive.google.com/drive/folders/{folder_id}"


async def create_feature_folder(feature_name: str) -> tuple[str, str]:
    try:
        folder_id, folder_url = await asyncio.to_thread(_create_folder_sync, feature_name)
        logger.info(f"GDrive папка создана: {feature_name} → {folder_url}")
        return folder_id, folder_url
    except Exception as e:
        logger.error(f"GDrive create_folder ошибка: {e}", exc_info=True)
        return "", ""


async def _upload_via_webhook(folder_id: str, filename: str, content_b64: str) -> str:
    """Загружает файл через n8n webhook (OAuth2 Google Drive)."""
    payload = {
        "filename": filename,
        "folder_id": folder_id,
        "content": content_b64,
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(N8N_GDRIVE_WEBHOOK, json=payload)
            resp.raise_for_status()
            data = resp.json()
            file_id = data.get("file_id", "")
            if file_id:
                logger.info(f"GDrive загружен: {filename} → {file_id}")
            else:
                logger.warning(f"GDrive webhook ответил без file_id: {data}")
            return file_id
    except Exception as e:
        logger.error(f"GDrive webhook ошибка [{filename}]: {e}", exc_info=True)
        return ""


async def upload_text_file(folder_id: str, filename: str, content: str) -> str:
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    return await _upload_via_webhook(folder_id, filename, b64)


async def upload_bytes_file(folder_id: str, filename: str, content: bytes, mimetype: str) -> str:
    b64 = base64.b64encode(content).decode("ascii")
    return await _upload_via_webhook(folder_id, filename, b64)


async def upload_all_artifacts(task) -> bool:
    fid = task.gdrive_feature_folder_id
    tid = task.task_id
    fn = task.feature_name[:40].replace(" ", "_").replace("/", "-")

    if not fid:
        logger.error("upload_all_artifacts: folder_id пустой")
        return False

    # Диагностика пустых полей
    fields = {
        "ba_text": task.ba_text, "sa_text": task.sa_text,
        "qatc_text": task.qatc_text, "pm_protocol": task.pm_protocol,
        "pm_jira": task.pm_jira, "pm_epics": task.pm_epics,
        "pm_risks": task.pm_risks, "pm_raci": task.pm_raci,
        "pm_team": task.pm_team, "pm_template_1": task.pm_template_1,
        "pm_template_2": task.pm_template_2, "pm_projekt_csv": task.pm_projekt_csv,
    }
    empty = [k for k, v in fields.items() if not v]
    if empty:
        logger.warning(f"Пустые поля перед загрузкой: {empty}")

    files_md = [
        (f"ba_{fn}_{tid}.md",             task.ba_text),
        (f"sa_{fn}_{tid}.md",             task.sa_text),
        (f"qatc_{fn}_{tid}.md",           task.qatc_text),
        (f"pm_protocol_{fn}_{tid}.md",    task.pm_protocol),
        (f"pm_jira_{fn}_{tid}.md",        task.pm_jira),
        (f"pm_epics_{fn}_{tid}.md",       task.pm_epics),
        (f"pm_risks_{fn}_{tid}.md",       task.pm_risks),
        (f"pm_raci_{fn}_{tid}.md",        task.pm_raci),
        (f"pm_team_{fn}_{tid}.md",        task.pm_team),
        (f"tz_biz_{fn}_{tid}.md",         task.pm_template_1),
        (f"projekt_tasks_{fn}_{tid}.csv", task.pm_projekt_csv),
    ]

    # Загружаем параллельно батчами по 3 — не перегружаем webhook
    success = True
    uploaded = 0

    async def upload_one(filename, content):
        nonlocal uploaded, success
        if not content:
            logger.warning(f"Пропущен (пустой): {filename}")
            return
        result = await upload_text_file(fid, filename, content)
        if result:
            uploaded += 1
        else:
            success = False

    # Батчи по 3 параллельных загрузки
    for i in range(0, len(files_md), 3):
        batch = files_md[i:i+3]
        await asyncio.gather(*[upload_one(fn, ct) for fn, ct in batch])

    # XLSX
    if task.pm_projekt_xlsx:
        result = await upload_bytes_file(
            fid, f"projekt_tasks_{fn}_{tid}.xlsx",
            task.pm_projekt_xlsx,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        if result:
            uploaded += 1
        else:
            success = False

    logger.info(f"GDrive итог: загружено {uploaded}/{len(files_md)+1} файлов")
    return success
