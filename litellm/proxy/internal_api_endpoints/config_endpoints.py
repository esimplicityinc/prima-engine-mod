"""
Internal Config Endpoints

GET /internal/v1/config/models - Fetch model configuration
GET /internal/v1/config/settings - Fetch general settings
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request

from litellm._logging import verbose_proxy_logger
from litellm.proxy.internal_api_endpoints.internal_router import (
    verify_internal_service_key,
)
from litellm.proxy.internal_api_endpoints.types import (
    InternalConfigModelsResponse,
    InternalConfigSettingsResponse,
    InternalServiceKey,
    ModelConfigItem,
)

router = APIRouter(tags=["internal-config"])


def _sanitize_litellm_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize litellm_params to remove sensitive information.
    
    Since remote proxies have their own LLM API keys, we don't need
    to send the central server's keys. We only send routing information.
    """
    # Fields to exclude from remote proxies (they have their own credentials)
    sensitive_fields = [
        "api_key",
        "aws_access_key_id",
        "aws_secret_access_key",
        "vertex_credentials",
        "azure_ad_token",
    ]

    sanitized = {}
    for key, value in params.items():
        if key in sensitive_fields:
            # Mark that a key exists but don't send the value
            sanitized[f"_{key}_configured"] = True
        else:
            sanitized[key] = value

    return sanitized


@router.get(
    "/config/models",
    response_model=InternalConfigModelsResponse,
    summary="Get Model Configuration",
    description="Fetch model configuration for remote proxies",
)
async def internal_config_models(
    request: Request,
    include_api_keys: bool = Query(
        default=False,
        description="Include API keys in response (requires additional permissions)",
    ),
    service_key: InternalServiceKey = Depends(verify_internal_service_key),
) -> InternalConfigModelsResponse:
    """
    Get model configuration for remote proxies.
    
    Returns the list of configured models and their routing information.
    By default, API keys are not included (remote proxies use their own keys).
    """
    from litellm.proxy.proxy_server import llm_router, prisma_client

    verbose_proxy_logger.debug("Internal config models request")

    models: List[ModelConfigItem] = []
    last_updated: Optional[datetime] = None

    # Get models from the router
    if llm_router is not None:
        model_list = llm_router.get_model_list()

        for model in model_list:
            litellm_params = model.get("litellm_params", {})

            # Sanitize params unless explicitly requested to include keys
            if not include_api_keys:
                litellm_params = _sanitize_litellm_params(litellm_params)

            models.append(
                ModelConfigItem(
                    model_name=model.get("model_name", ""),
                    litellm_params=litellm_params,
                    model_info=model.get("model_info"),
                )
            )

    # Try to get last updated time from database
    if prisma_client is not None:
        try:
            # Get the most recent model update time
            latest_model = await prisma_client.db.litellm_proxymodeltable.find_first(
                order={"updated_at": "desc"}
            )
            if latest_model and latest_model.updated_at:
                last_updated = latest_model.updated_at
        except Exception as e:
            verbose_proxy_logger.debug(
                "Could not get model last_updated time: %s", str(e)
            )

    if last_updated is None:
        last_updated = datetime.now(timezone.utc)

    verbose_proxy_logger.debug(
        "Internal config models response: %d models", len(models)
    )

    return InternalConfigModelsResponse(
        models=models,
        last_updated=last_updated,
    )


@router.get(
    "/config/settings",
    response_model=InternalConfigSettingsResponse,
    summary="Get Proxy Settings",
    description="Fetch proxy settings for remote proxies",
)
async def internal_config_settings(
    request: Request,
    service_key: InternalServiceKey = Depends(verify_internal_service_key),
) -> InternalConfigSettingsResponse:
    """
    Get proxy settings for remote proxies.
    
    Returns settings that remote proxies need to operate consistently
    with the central server.
    """
    from litellm.proxy.proxy_server import general_settings, llm_router

    verbose_proxy_logger.debug("Internal config settings request")

    # Filter general_settings to only include relevant fields for remote proxies
    remote_relevant_settings = [
        "max_parallel_requests",
        "global_max_parallel_requests",
        "max_request_size_mb",
        "max_response_size_mb",
        "allowed_routes",
        "alerting_threshold",
        # Rate limiting related
        "default_team_settings",
        # Caching related
        "cache",
        "cache_params",
    ]

    filtered_general_settings: Dict[str, Any] = {}
    for key in remote_relevant_settings:
        if key in general_settings:
            filtered_general_settings[key] = general_settings[key]

    # Get router settings
    router_settings: Optional[Dict[str, Any]] = None
    if llm_router is not None:
        router_settings = {
            "routing_strategy": getattr(llm_router, "routing_strategy", None),
            "num_retries": getattr(llm_router, "num_retries", None),
            "timeout": getattr(llm_router, "timeout", None),
            "retry_after": getattr(llm_router, "retry_after", None),
            "allowed_fails": getattr(llm_router, "allowed_fails", None),
            "cooldown_time": getattr(llm_router, "cooldown_time", None),
        }
        # Remove None values
        router_settings = {k: v for k, v in router_settings.items() if v is not None}

    # Get litellm settings that affect behavior
    import litellm

    litellm_settings: Dict[str, Any] = {
        "drop_params": getattr(litellm, "drop_params", None),
        "request_timeout": getattr(litellm, "request_timeout", None),
        "max_budget": getattr(litellm, "max_budget", None),
        "budget_duration": getattr(litellm, "budget_duration", None),
    }
    litellm_settings = {k: v for k, v in litellm_settings.items() if v is not None}

    return InternalConfigSettingsResponse(
        general_settings=filtered_general_settings,
        router_settings=router_settings,
        litellm_settings=litellm_settings if litellm_settings else None,
    )


@router.get(
    "/config/health",
    summary="Check Central Server Health",
    description="Health check endpoint for remote proxies to verify connectivity",
)
async def internal_config_health(
    request: Request,
    service_key: InternalServiceKey = Depends(verify_internal_service_key),
) -> Dict[str, Any]:
    """
    Health check for remote proxies to verify central server connectivity.
    
    Returns basic health information about the central server.
    """
    from litellm.proxy.proxy_server import llm_router, prisma_client

    health_info: Dict[str, Any] = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database_connected": prisma_client is not None,
        "router_initialized": llm_router is not None,
    }

    if llm_router is not None:
        health_info["model_count"] = len(llm_router.get_model_list())

    return health_info
