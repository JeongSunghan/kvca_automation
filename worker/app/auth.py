from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings


@dataclass
class TokenBundle:
    grant_type: str
    access_token: str
    refresh_token: str
    expires_at_ms: int

    @classmethod
    def from_login_response(cls, payload: dict[str, Any]) -> "TokenBundle":
        return cls(
            grant_type=str(payload.get("grantType", "Bearer")),
            access_token=str(payload["accessToken"]),
            refresh_token=str(payload.get("refreshToken", "")),
            expires_at_ms=int(payload["accessTokenExpiresIn"]),
        )


class KVCAAuthManager:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client
        self._token: TokenBundle | None = None
        self._lock = asyncio.Lock()

    async def get_auth_header(self) -> dict[str, str]:
        token = await self._get_valid_token()
        return {"Authorization": f"{token.grant_type} {token.access_token}"}

    async def force_relogin(self) -> None:
        async with self._lock:
            self._token = None
            self._token = await self._login()

    async def _get_valid_token(self) -> TokenBundle:
        if self._token is not None and not self._is_expiring_soon(self._token):
            return self._token
        async with self._lock:
            if self._token is None or self._is_expiring_soon(self._token):
                self._token = await self._login()
            return self._token

    def _is_expiring_soon(self, token: TokenBundle) -> bool:
        now_ms = int(time.time() * 1000)
        skew_ms = self._settings.kvca_token_skew_seconds * 1000
        return now_ms + skew_ms >= token.expires_at_ms

    async def _login(self) -> TokenBundle:
        payload = {
            "userId": self._settings.kvca_admin_user_id,
            "userPassword": self._settings.kvca_admin_user_password,
            "submit": None,
        }
        response = await self._client.post("/api/auth/login", json=payload)
        response.raise_for_status()
        data = response.json()
        return TokenBundle.from_login_response(data)
