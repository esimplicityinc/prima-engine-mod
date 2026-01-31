"""
Internal Auth Endpoints

POST /internal/v1/auth/validate - Validate API keys for remote proxies
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from litellm._logging import verbose_proxy_logger
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.internal_api_endpoints.internal_router import (
    verify_internal_service_key,
)
from litellm.proxy.internal_api_endpoints.types import (
    InternalAuthValidateRequest,
    InternalAuthValidateResponse,
    InternalServiceKey,
)

router = APIRouter(tags=["internal-auth"])


def _user_api_key_auth_to_dict(auth: UserAPIKeyAuth) -> Dict[str, Any]:
    """
    Convert UserAPIKeyAuth to a dictionary suitable for JSON serialization.
    
    Excludes sensitive fields and handles non-serializable types.
    """
    # Use model_dump if available (Pydantic v2), otherwise dict() (v1)
    if hasattr(auth, "model_dump"):
        data = auth.model_dump(exclude_none=True)
    else:
        data = auth.dict(exclude_none=True)

    # Remove any fields that shouldn't be sent to remote proxies
    sensitive_fields = ["parent_otel_span"]
    for field in sensitive_fields:
        data.pop(field, None)

    return data


@router.post(
    "/auth/validate",
    response_model=InternalAuthValidateResponse,
    summary="Validate API Key",
    description="Validate an API key and return authorization data for remote proxies",
)
async def internal_auth_validate(
    request: Request,
    body: InternalAuthValidateRequest,
    service_key: InternalServiceKey = Depends(verify_internal_service_key),
) -> InternalAuthValidateResponse:
    """
    Validate an API key from a remote proxy.
    
    This endpoint performs the same authentication as the regular proxy,
    but returns the full UserAPIKeyAuth data so remote proxies can
    cache it and perform local authorization checks.
    """
    from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
    from litellm.proxy.proxy_server import general_settings, user_api_key_cache

    verbose_proxy_logger.debug(
        "Internal auth validate request for route: %s, model: %s",
        body.request_route,
        body.request_model,
    )

    try:
        # Build a mock request object for the auth function
        # We need to pass the API key and relevant context
        class MockRequest:
            def __init__(self, api_key: str, route: str, model: Optional[str]):
                self._api_key = api_key
                self._route = route
                self._model = model
                self.headers = {"authorization": f"Bearer {api_key}"}
                self.url = type("URL", (), {"path": route})()
                self.query_params = {}
                self.client = request.client
                self.state = type("State", (), {})()
                self._body = None

            async def body(self):
                if self._body is None:
                    # Construct a minimal request body
                    body_dict = {}
                    if self._model:
                        body_dict["model"] = self._model
                    import json

                    self._body = json.dumps(body_dict).encode()
                return self._body

            async def json(self):
                import json

                return json.loads(await self.body())

        mock_request = MockRequest(
            api_key=body.api_key,
            route=body.request_route or "/v1/chat/completions",
            model=body.request_model,
        )

        # Perform authentication
        user_api_key_auth_obj = await user_api_key_auth(
            request=mock_request,  # type: ignore
            api_key=f"Bearer {body.api_key}",
        )

        # Get cache TTL from settings
        cache_ttl = general_settings.get("remote_proxy_auth_cache_ttl_seconds", 300)

        return InternalAuthValidateResponse(
            valid=True,
            user_api_key_auth=_user_api_key_auth_to_dict(user_api_key_auth_obj),
            cache_ttl_seconds=cache_ttl,
        )

    except HTTPException as e:
        verbose_proxy_logger.debug(
            "Internal auth validate failed: %s - %s",
            e.status_code,
            e.detail,
        )
        return InternalAuthValidateResponse(
            valid=False,
            error=str(e.detail),
            error_code=f"http_{e.status_code}",
            cache_ttl_seconds=60,  # Cache failures for shorter time
        )

    except Exception as e:
        verbose_proxy_logger.exception(
            "Internal auth validate unexpected error: %s",
            str(e),
        )
        return InternalAuthValidateResponse(
            valid=False,
            error="Internal authentication error",
            error_code="internal_error",
            cache_ttl_seconds=0,  # Don't cache unexpected errors
        )


@router.post(
    "/auth/validate/batch",
    response_model=Dict[str, InternalAuthValidateResponse],
    summary="Validate Multiple API Keys",
    description="Validate multiple API keys in a single request",
)
async def internal_auth_validate_batch(
    request: Request,
    body: Dict[str, InternalAuthValidateRequest],
    service_key: InternalServiceKey = Depends(verify_internal_service_key),
) -> Dict[str, InternalAuthValidateResponse]:
    """
    Batch validate multiple API keys.
    
    Useful for remote proxies to warm their cache on startup.
    
    Request body is a dictionary mapping arbitrary keys to validation requests.
    Response uses the same keys to map to validation responses.
    """
    import asyncio

    results: Dict[str, InternalAuthValidateResponse] = {}

    # Process all validations concurrently
    async def validate_one(
        key: str, req: InternalAuthValidateRequest
    ) -> tuple[str, InternalAuthValidateResponse]:
        response = await internal_auth_validate(request, req, service_key)
        return key, response

    tasks = [validate_one(k, v) for k, v in body.items()]
    completed = await asyncio.gather(*tasks, return_exceptions=True)

    for result in completed:
        if isinstance(result, Exception):
            verbose_proxy_logger.error("Batch auth validation error: %s", result)
            continue
        key, response = result
        results[key] = response

    return results
