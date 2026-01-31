"""
Internal API Router for Remote Proxy Mode

This router handles internal service-to-service API calls from remote proxies
to the central management server.
"""

import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security.api_key import APIKeyHeader

from litellm._logging import verbose_proxy_logger
from litellm.proxy.internal_api_endpoints.types import InternalServiceKey

internal_api_router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
)

# Header for internal service key authentication
internal_service_key_header = APIKeyHeader(
    name="X-Internal-Service-Key",
    auto_error=False,
    description="Service key for remote proxy authentication",
)


def get_internal_service_keys() -> List[InternalServiceKey]:
    """
    Get the list of configured internal service keys.
    
    These can be configured via:
    - general_settings.internal_service_keys in config.yaml
    - LITELLM_INTERNAL_SERVICE_KEY environment variable (single key)
    """
    import os

    from litellm.proxy.proxy_server import general_settings

    service_keys: List[InternalServiceKey] = []

    # Check for environment variable (simple single-key setup)
    env_key = os.environ.get("LITELLM_INTERNAL_SERVICE_KEY")
    if env_key:
        service_keys.append(
            InternalServiceKey(
                key=env_key,
                name="env-service-key",
                allowed_endpoints=["internal/*"],
            )
        )

    # Check for configured service keys in general_settings
    configured_keys = general_settings.get("internal_service_keys", [])
    if configured_keys:
        for key_config in configured_keys:
            if isinstance(key_config, dict):
                service_keys.append(InternalServiceKey(**key_config))
            elif isinstance(key_config, InternalServiceKey):
                service_keys.append(key_config)

    return service_keys


async def verify_internal_service_key(
    request: Request,
    service_key: Optional[str] = Depends(internal_service_key_header),
) -> InternalServiceKey:
    """
    Dependency to verify internal service key authentication.
    
    Remote proxies must provide a valid service key in the X-Internal-Service-Key header.
    """
    # Also check Authorization header as fallback
    if not service_key:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            service_key = auth_header[7:]

    if not service_key:
        verbose_proxy_logger.warning(
            "Internal API request without service key from %s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing internal service key",
        )

    # Get configured service keys
    valid_keys = get_internal_service_keys()

    if not valid_keys:
        verbose_proxy_logger.error(
            "No internal service keys configured. "
            "Set LITELLM_INTERNAL_SERVICE_KEY or general_settings.internal_service_keys"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal API not configured",
        )

    # Check if the provided key matches any configured key
    for valid_key in valid_keys:
        if secrets.compare_digest(service_key, valid_key.key):
            # Check expiration
            if valid_key.expires_at:
                from datetime import datetime, timezone

                if datetime.now(timezone.utc) > valid_key.expires_at:
                    verbose_proxy_logger.warning(
                        "Expired internal service key used: %s",
                        valid_key.name,
                    )
                    continue

            verbose_proxy_logger.debug(
                "Internal API authenticated with service key: %s",
                valid_key.name,
            )
            return valid_key

    verbose_proxy_logger.warning(
        "Invalid internal service key from %s",
        request.client.host if request.client else "unknown",
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid internal service key",
    )


def is_internal_api_enabled() -> bool:
    """Check if internal API endpoints should be enabled."""
    import os

    from litellm.proxy.proxy_server import general_settings

    # Enabled if any service keys are configured
    if os.environ.get("LITELLM_INTERNAL_SERVICE_KEY"):
        return True

    if general_settings.get("internal_service_keys"):
        return True

    return False


# Import and include endpoint routers
from litellm.proxy.internal_api_endpoints.auth_endpoints import router as auth_router
from litellm.proxy.internal_api_endpoints.config_endpoints import (
    router as config_router,
)
from litellm.proxy.internal_api_endpoints.spend_endpoints import (
    router as spend_router,
)

internal_api_router.include_router(auth_router)
internal_api_router.include_router(spend_router)
internal_api_router.include_router(config_router)
