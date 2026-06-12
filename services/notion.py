import os
import json
import logging
import httpx

logger = logging.getLogger(__name__)

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.getenv(
    "NOTION_DATABASE_ID",
    "37de2df6904380fbbb08c55e04ebcb05"
)
NOTION_API = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


async def create_feature_page(task) -> str:
    """
    Создаёт страницу в Notion базе Bank ReleaseAgent.
    Возвращает URL страницы.
    """
    if not NOTION_TOKEN:
        logger.warning("NOTION_TOKEN не задан — пропускаем Notion")
        return ""

    properties = {
        "Name": {
            "title": [{"text": {"content": task.feature_name}}]
        },
        "Channel": {
            "rich_text": [{"text": {"content": "Telegram"}}]
        },
        "ChatId": {
            "rich_text": [{"text": {"content": str(task.chat_id)}}]
        },
        "Command": {
            "rich_text": [{"text": {"content": "/start_analysis"}}]
        },
        "Started": {
            "date": {"start": task.timestamp}
        },
        "Status": {
            "select": {"name": "Running"}
        },
        "RunId": {
            "rich_text": [{"text": {"content": f"{task.chat_id}_{task.task_id}"}}]
        },
        "Google Doc URL": {
            "url": task.gdrive_feature_folder_url or None
        },
    }

    body = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": properties,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{NOTION_API}/pages",
                headers=HEADERS,
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        page_id = data["id"]
        page_url = data.get("url", f"https://notion.so/{page_id.replace('-', '')}")
        logger.info(f"Notion страница создана: {page_url}")
        return page_url

    except Exception as e:
        logger.error(f"Ошибка создания Notion страницы: {e}")
        return ""


async def update_feature_page(page_url: str, task) -> bool:
    """
    Обновляет страницу Notion после завершения всех агентов.
    Ставит Status=Done, Finished, GDrive URL.
    """
    if not NOTION_TOKEN or not page_url:
        return False

    # Извлекаем page_id из URL
    page_id = page_url.rstrip("/").split("/")[-1].split("-")[-1]
    if len(page_id) != 32:
        # Пробуем другой формат
        page_id = page_url.rstrip("/").split("/")[-1]

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    properties = {
        "Status": {"select": {"name": "Done"}},
        "Finished": {"date": {"start": now}},
        "Google Doc URL": {"url": task.gdrive_feature_folder_url or None},
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(
                f"{NOTION_API}/pages/{page_id}",
                headers=HEADERS,
                json={"properties": properties},
            )
            resp.raise_for_status()
        logger.info(f"Notion страница обновлена: Done")
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления Notion: {e}")
        return False
