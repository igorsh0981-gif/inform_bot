import re
import os
import logging
import httpx

logger = logging.getLogger(__name__)

FIGMA_URL_RE = re.compile(
    r'https://(?:www\.)?figma\.com/(?:file|design)/([a-zA-Z0-9_-]+)'
)

FIGMA_TOKEN = os.getenv("FIGMA_TOKEN", "")


def extract_figma_key(text: str) -> str | None:
    """Извлекает fileKey из Figma URL в тексте сообщения"""
    match = FIGMA_URL_RE.search(text)
    return match.group(1) if match else None


def extract_figma_url(text: str) -> str:
    """Возвращает полный Figma URL или пустую строку"""
    match = FIGMA_URL_RE.search(text)
    return match.group(0) if match else ""


async def read_figma(file_key: str) -> str:
    """
    Читает Figma файл через REST API.
    Возвращает текстовое описание структуры документа.
    """
    if not FIGMA_TOKEN:
        logger.warning("FIGMA_TOKEN не задан — пропускаем чтение Figma")
        return "[Figma: токен не настроен]"

    url = f"https://api.figma.com/v1/files/{file_key}"
    headers = {"X-Figma-Token": FIGMA_TOKEN}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        # Извлекаем текстовое содержимое из документа
        name = data.get("name", "Без названия")
        pages = data.get("document", {}).get("children", [])

        description_parts = [f"Figma документ: {name}"]

        for page in pages[:3]:  # макс 3 страницы
            page_name = page.get("name", "")
            frames = page.get("children", [])
            frame_names = [f.get("name", "") for f in frames[:10]]
            description_parts.append(
                f"Страница '{page_name}': {', '.join(frame_names)}"
            )

        return "\n".join(description_parts)

    except httpx.HTTPStatusError as e:
        logger.error(f"Figma API ошибка {e.response.status_code}: {e}")
        return f"[Figma: ошибка доступа {e.response.status_code}]"
    except Exception as e:
        logger.error(f"Figma чтение не удалось: {e}")
        return f"[Figma: ошибка чтения — {e}]"
