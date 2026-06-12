import os
import logging
import httpx

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "37de2df6904380fbbb08c55e04ebcb05")

# Кэш page_id чтобы не парсить URL
_page_id_cache: dict[str, str] = {}


def _headers():
    return {
        "Authorization": f"Bearer {os.getenv('NOTION_TOKEN', '')}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


async def create_feature_page(task) -> str:
    token = os.getenv("NOTION_TOKEN", "")
    if not token:
        logger.warning("NOTION_TOKEN не задан")
        return ""

    properties = {
        "Name": {
            "title": [{"text": {"content": (task.feature_name or "Без названия")[:100]}}]
        },
        "Status": {"status": {"name": "Running"}},
        "Command": {"rich_text": [{"text": {"content": "/chainlight"}}]},
        "RunId": {"rich_text": [{"text": {"content": f"{task.chat_id}_{task.task_id}"}}]},
        "Started": {"date": {"start": task.timestamp or "2026-01-01T00:00:00Z"}},
    }

    # ChatId как rich_text (колонка есть в схеме)
    if task.chat_id:
        properties["ChatId"] = {"rich_text": [{"text": {"content": str(task.chat_id)}}]}

    # Channel как rich_text (не select — так безопаснее)
    properties["Channel"] = {"rich_text": [{"text": {"content": "Telegram"}}]}

    if task.gdrive_feature_folder_url:
        properties["Google Doc URL"] = {"url": task.gdrive_feature_folder_url}

    body = {"parent": {"database_id": NOTION_DATABASE_ID}, "properties": properties}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{NOTION_API}/pages", headers=_headers(), json=body)
            if resp.status_code != 200:
                logger.error(f"Notion create {resp.status_code}: {resp.text[:500]}")
                return ""
            data = resp.json()

        page_id = data["id"]  # формат: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        page_url = data.get("url", f"https://notion.so/{page_id.replace('-', '')}")

        # Кэшируем page_id по URL для надёжного update
        _page_id_cache[page_url] = page_id
        logger.info(f"Notion создана: {page_url}")
        return page_url

    except Exception as e:
        logger.error(f"Notion create exception: {e}", exc_info=True)
        return ""


async def update_feature_page(page_url: str, task) -> bool:
    token = os.getenv("NOTION_TOKEN", "")
    if not token or not page_url:
        return False

    # Берём page_id из кэша — самый надёжный способ
    page_id = _page_id_cache.get(page_url)
    if not page_id:
        # Fallback: парсим из URL
        # URL вида: https://notion.so/workspace/Title-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
        # или: https://www.notion.so/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
        raw = page_url.rstrip("/").split("/")[-1]
        # Убираем slug если есть (Title-uuid)
        if "-" in raw and len(raw) > 32:
            raw = raw.split("-")[-1]
        # Добавляем дефисы если UUID без них
        if len(raw) == 32 and "-" not in raw:
            page_id = f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
        else:
            page_id = raw
        logger.warning(f"Notion page_id из URL: {page_id} (кэш не найден)")

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
                logger.error(f"Notion update {resp.status_code}: {resp.text[:500]}")
                return False
        logger.info("Notion обновлён: Done")
        return True
    except Exception as e:
        logger.error(f"Notion update exception: {e}", exc_info=True)
        return False
