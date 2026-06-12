import os
import logging
import httpx

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_DATABASE_ID = os.getenv(
    "NOTION_DATABASE_ID",
    "37de2df6904380fbbb08c55e04ebcb05"
)


def _headers():
    return {
        "Authorization": f"Bearer {os.getenv('NOTION_TOKEN', '')}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


async def create_feature_page(task) -> str:
    """Создаёт страницу в Notion базе Bank ReleaseAgent."""
    token = os.getenv("NOTION_TOKEN", "")
    if not token:
        logger.warning("NOTION_TOKEN не задан — пропускаем Notion")
        return ""

    # Только поля которые реально есть в схеме базы:
    # Name, Command, Finished, Google Doc URL, Last error, RunId, Started, Status
    properties = {
        "Name": {
            "title": [{"text": {"content": task.feature_name or "Без названия"}}]
        },
        "Status": {
            "status": {"name": "Running"}
        },
        "Command": {
            "rich_text": [{"text": {"content": "/chainlight"}}]
        },
        "RunId": {
            "rich_text": [{"text": {"content": f"{task.chat_id}_{task.task_id}"}}]
        },
        "Started": {
            "date": {"start": task.timestamp or "2026-01-01T00:00:00Z"}
        },
    }

    if task.gdrive_feature_folder_url:
        properties["Google Doc URL"] = {"url": task.gdrive_feature_folder_url}

    body = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": properties,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{NOTION_API}/pages",
                headers=_headers(),
                json=body,
            )
            if resp.status_code != 200:
                logger.error(f"Notion create ошибка {resp.status_code}: {resp.text}")
                return ""
            data = resp.json()

        page_id = data["id"]
        page_url = data.get("url", f"https://notion.so/{page_id.replace('-', '')}")
        logger.info(f"Notion страница создана: {page_url}")
        return page_url

    except Exception as e:
        logger.error(f"Ошибка создания Notion страницы: {e}")
        return ""


async def update_feature_page(page_url: str, task) -> bool:
    """Обновляет страницу Notion после завершения всех агентов."""
    token = os.getenv("NOTION_TOKEN", "")
    if not token or not page_url:
        return False

    # Извлекаем page_id из URL
    page_id = page_url.rstrip("/").split("/")[-1]
    if "-" in page_id:
        page_id = page_id.split("-")[-1]

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    properties = {
        "Status": {"status": {"name": "Done"}},
        "Finished": {"date": {"start": now}},
    }

    if task.gdrive_feature_folder_url:
        properties["Google Doc URL"] = {"url": task.gdrive_feature_folder_url}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(
                f"{NOTION_API}/pages/{page_id}",
                headers=_headers(),
                json={"properties": properties},
            )
            if resp.status_code != 200:
                logger.error(f"Notion update ошибка {resp.status_code}: {resp.text}")
                return False
        logger.info("Notion страница обновлена: Done")
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления Notion: {e}")
        return False
