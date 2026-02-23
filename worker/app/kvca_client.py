from __future__ import annotations

from typing import Any

import httpx

from .auth import KVCAAuthManager
from .config import Settings


class KVCAClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        timeout = httpx.Timeout(settings.kvca_request_timeout_ms / 1000)
        self._http = httpx.AsyncClient(base_url=settings.kvca_base_url, timeout=timeout)
        self._auth = KVCAAuthManager(settings, self._http)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def fetch_categories(self) -> list[dict[str, Any]]:
        data = await self._request_json("/api/category/list", {"categoryid": "all"})
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    async def fetch_courses_by_category(self, category_id: int) -> list[dict[str, Any]]:
        payload = {"categoryid": category_id}
        data = await self._request_json("/api/course/category/course", payload)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return [item for item in data.values() if isinstance(item, dict)]
        return []

    async def fetch_class_status_all(self, course_id: int) -> list[dict[str, Any]]:
        data = await self._request_json("/api/course/classStatusAll", {"courseid": course_id})
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    async def fetch_enrolment_user_info(self, term_id: int, user_id: str) -> dict[str, Any]:
        payload = {"termId": term_id, "userId": user_id}
        data = await self._request_json("/api/enrolment/getEnrolmentUserInfo", payload)
        if isinstance(data, dict):
            return data
        return {}

    async def _request_json(self, path: str, payload: dict[str, Any]) -> Any:
        headers = await self._auth.get_auth_header()
        response = await self._http.post(path, json=payload, headers=headers)
        if response.status_code == 401 and self._settings.kvca_retry_on_401:
            await self._auth.force_relogin()
            headers = await self._auth.get_auth_header()
            response = await self._http.post(path, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()
