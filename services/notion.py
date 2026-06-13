import os
import logging
import asyncio
import httpx

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "37de2df6904380fbbb08c55e04ebcb05")

_page_id_cache: dict[str, str] = {}


def _headers():
    return {
        "Authorization": f"Bearer {os.getenv('NOTION_TOKEN', '')}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


async def _request_with_retry(method: str, url: str, body: dict) -> dict | None:
    """
    Универсальный HTTP запрос к Notion с retry на 429.
    method: "POST" | "PATCH"
    Возвращает JSON dict или None при ошибке.
    """
    for attempt in range(1, 3):
        async with httpx.AsyncClient(timeout=30) as client:
            if method == "POST":
                resp = await client.post(url, headers=_headers(), json=body)
            else:
                resp = await client.patch(url, headers=_headers(), json=body)

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 429:
            retry_after = min(int(resp.headers.get("Retry-After", 5)), 25)
            logger.warning(f"Notion rate limit {method} (attempt {attempt}), retry через {retry_after}s")
            if attempt < 2:
                await asyncio.sleep(retry_after)
                continue
            logger.error(f"Notion rate limit — исчерпаны попытки ({method} {url})")
            return None

        logger.error(f"Notion {method} {resp.status_code}: {resp.text[:500]}")
        return None

    return None


async def create_feature_page(task) -> str:
    token = os.getenv("NOTION_TOKEN", "")
    if not token:
        logger.warning("NOTION_TOKEN не задан")
        return ""

    properties = {
        "Name": {"title": [{"text": {"content": (task.feature_name or "Без названия")[:100]}}]},
        "Status": {"status": {"name": "Running"}},
        "Command": {"rich_text": [{"text": {"content": "/chainlight"}}]},
        "RunId": {"rich_text": [{"text": {"content": f"{task.chat_id}_{task.task_id}"}}]},
        "Started": {"date": {"start": task.timestamp or "2026-01-01T00:00:00Z"}},
        "Channel": {"select": {"name": "Telegram"}},
    }
    if task.chat_id:
        properties["ChatId"] = {"rich_text": [{"text": {"content": str(task.chat_id)}}]}
    if task.gdrive_feature_folder_url:
        properties["Google Doc URL"] = {"url": task.gdrive_feature_folder_url}

    body = {"parent": {"database_id": NOTION_DATABASE_ID}, "properties": properties}

    try:
        data = await _request_with_retry("POST", f"{NOTION_API}/pages", body)
        if not data:
            return ""
        page_id = data["id"]
        page_url = data.get("url", f"https://notion.so/{page_id.replace('-', '')}")
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

    page_id = _page_id_cache.get(page_url)
    if not page_id:
        raw = page_url.rstrip("/").split("/")[-1]
        if "-" in raw and len(raw) > 32:
            raw = raw.split("-")[-1]
        if len(raw) == 32 and "-" not in raw:
            page_id = f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
        else:
            page_id = raw
        logger.warning(f"Notion page_id из URL: {page_id}")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    properties = {
        "Status": {"status": {"name": "Done"}},
        "Finished": {"date": {"start": now}},
    }
    if task.gdrive_feature_folder_url:
        properties["Google Doc URL"] = {"url": task.gdrive_feature_folder_url}

    try:
        data = await _request_with_retry("PATCH", f"{NOTION_API}/pages/{page_id}", {"properties": properties})
        if data is not None:
            logger.info("Notion обновлён: Done")
            return True
        return False
    except Exception as e:
        logger.error(f"Notion update exception: {e}", exc_info=True)
        return False
