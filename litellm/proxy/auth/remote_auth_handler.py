"""
Remote Auth Handler for Remote Proxy Mode

This module provides authentication by calling a central management server
instead of performing local database lookups.
"""

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from litellm._logging import verbose_proxy_logger
from litellm.caching import DualCache
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.internal_api_endpoints.types import RemoteProxySettings


class CentralServerUnreachable(Exception):
    """Raised when the central server cannot be reached."""

    pass


class RemoteAuthHandler:
    """
    Handles authentication by calling a central management server.
    
    This is used in remote proxy mode where the proxy doesn't have direct
    database access and instead relies on a central server for auth.
    """

    def __init__(
        self,
        settings: RemoteProxySettings,
        cache: DualCache,
    ):
        self.settings = settings
        self.cache = cache
        self._http_client: Optional[httpx.AsyncClient] = None

    @property
    def http_client(self) -> httpx.AsyncClient:
        """Lazy initialization of HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self.settings.central_server_url,
                timeout=httpx.Timeout(30.0),
                headers={
                    "X-Internal-Service-Key": self.settings.service_key,
                    "Content-Type": "application/json",
                },
            )
        return self._http_client

    async def close(self):
        """Close the HTTP client."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    def _get_cache_key(self, api_key: str) -> str:
        """Generate cache key for an API key."""
        # Hash the API key for the cache key
        import hashlib

        key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]
        return f"remote_auth:{key_hash}"

    def _is_cache_valid(self, cached_data: Dict[str, Any]) -> bool:
        """Check if cached data is still valid based on TTL."""
        cached_at = cached_data.get("cached_at", 0)
        ttl = cached_data.get("cache_ttl_seconds", self.settings.auth_cache_ttl_seconds)
        return time.time() - cached_at < ttl

    def _is_within_max_stale_age(self, cached_data: Dict[str, Any]) -> bool:
        """Check if cached data is within the max stale age for fallback."""
        cached_at = cached_data.get("cached_at", 0)
        return time.time() - cached_at < self.settings.max_cache_age_on_failure_seconds

    async def _call_central_auth(
        self,
        api_key: str,
        route: Optional[str],
        model: Optional[str],
        end_user_id: Optional[str],
    ) -> Dict[str, Any]:
        """Call the central server's auth validation endpoint."""
        try:
            response = await self.http_client.post(
                "/internal/v1/auth/validate",
                json={
                    "api_key": api_key,
                    "request_route": route,
                    "request_model": model,
                    "end_user_id": end_user_id,
                },
            )

            if response.status_code != 200:
                verbose_proxy_logger.error(
                    "Central auth server returned error: %s - %s",
                    response.status_code,
                    response.text,
                )
                raise CentralServerUnreachable(
                    f"Central server returned {response.status_code}"
                )

            return response.json()

        except httpx.RequestError as e:
            verbose_proxy_logger.error(
                "Failed to reach central auth server: %s",
                str(e),
            )
            raise CentralServerUnreachable(str(e)) from e

    async def validate_key(
        self,
        api_key: str,
        route: Optional[str] = None,
        model: Optional[str] = None,
        end_user_id: Optional[str] = None,
    ) -> UserAPIKeyAuth:
        """
        Validate an API key, using cache and central server.
        
        Flow:
        1. Check local cache
        2. If cache miss or expired, call central server
        3. Cache the result
        4. On central server failure, optionally use stale cache
        
        Args:
            api_key: The API key to validate
            route: The route being accessed
            model: The model being requested
            end_user_id: End user ID if provided
            
        Returns:
            UserAPIKeyAuth object with authorization data
            
        Raises:
            HTTPException: If validation fails
        """
        from fastapi import HTTPException, status

        cache_key = self._get_cache_key(api_key)

        # 1. Check local cache
        cached_data = await self.cache.async_get_cache(cache_key)

        if cached_data is not None:
            if isinstance(cached_data, str):
                import json

                cached_data = json.loads(cached_data)

            if self._is_cache_valid(cached_data):
                verbose_proxy_logger.debug(
                    "Remote auth: using cached auth data (age: %ds)",
                    int(time.time() - cached_data.get("cached_at", 0)),
                )

                if not cached_data.get("valid", False):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail=cached_data.get("error", "Invalid API key"),
                    )

                return UserAPIKeyAuth(**cached_data["user_api_key_auth"])

        # 2. Call central server
        try:
            response = await self._call_central_auth(
                api_key=api_key,
                route=route,
                model=model,
                end_user_id=end_user_id,
            )

            # 3. Cache the result
            response["cached_at"] = time.time()
            await self.cache.async_set_cache(
                cache_key,
                response,
                ttl=response.get("cache_ttl_seconds", self.settings.auth_cache_ttl_seconds),
            )

            if not response.get("valid", False):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=response.get("error", "Invalid API key"),
                )

            return UserAPIKeyAuth(**response["user_api_key_auth"])

        except CentralServerUnreachable:
            # 4. Fallback to stale cache if allowed
            if self.settings.allow_cached_auth_on_central_failure and cached_data:
                if self._is_within_max_stale_age(cached_data):
                    verbose_proxy_logger.warning(
                        "Remote auth: central server unreachable, using stale cache (age: %ds)",
                        int(time.time() - cached_data.get("cached_at", 0)),
                    )

                    if not cached_data.get("valid", False):
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail=cached_data.get("error", "Invalid API key"),
                        )

                    return UserAPIKeyAuth(**cached_data["user_api_key_auth"])

            verbose_proxy_logger.error(
                "Remote auth: central server unreachable and no valid cache available"
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service temporarily unavailable",
            )

    async def check_central_health(self) -> bool:
        """Check if the central server is reachable."""
        try:
            response = await self.http_client.get(
                "/internal/v1/config/health",
            )
            return response.status_code == 200
        except Exception as e:
            verbose_proxy_logger.debug(
                "Central server health check failed: %s", str(e)
            )
            return False


# Global instance for remote proxy mode
_remote_auth_handler: Optional[RemoteAuthHandler] = None


def get_remote_auth_handler() -> Optional[RemoteAuthHandler]:
    """Get the global remote auth handler instance."""
    return _remote_auth_handler


def set_remote_auth_handler(handler: Optional[RemoteAuthHandler]) -> None:
    """Set the global remote auth handler instance."""
    global _remote_auth_handler
    _remote_auth_handler = handler


async def remote_auth_validate_key(
    api_key: str,
    route: Optional[str] = None,
    model: Optional[str] = None,
    end_user_id: Optional[str] = None,
) -> Optional[UserAPIKeyAuth]:
    """
    Validate a key using the remote auth handler if available.
    
    Returns None if remote auth is not enabled, allowing fallback
    to normal authentication.
    """
    handler = get_remote_auth_handler()
    if handler is None:
        return None

    return await handler.validate_key(
        api_key=api_key,
        route=route,
        model=model,
        end_user_id=end_user_id,
    )
