"""
Remote Proxy Mode Initialization

This module handles the initialization and shutdown of remote proxy mode,
coordinating the remote auth handler, spend reporter, and config loader.
"""

import os
from typing import Any, Dict, Optional

import litellm
from litellm._logging import verbose_proxy_logger
from litellm.caching import DualCache
from litellm.proxy.auth.remote_auth_handler import (
    RemoteAuthHandler,
    get_remote_auth_handler,
    set_remote_auth_handler,
)
from litellm.proxy.common_utils.remote_config_loader import (
    RemoteConfigLoader,
    get_remote_config_loader,
    load_models_from_central,
    set_remote_config_loader,
)
from litellm.proxy.hooks.remote_spend_reporter import (
    RemoteSpendReporter,
    get_remote_spend_reporter,
    set_remote_spend_reporter,
)
from litellm.proxy.internal_api_endpoints.types import RemoteProxySettings

# Global state
_remote_proxy_settings: Optional[RemoteProxySettings] = None
_initialized: bool = False


def is_remote_proxy_mode() -> bool:
    """Check if the proxy is running in remote proxy mode."""
    return _initialized and _remote_proxy_settings is not None


def get_remote_proxy_settings() -> Optional[RemoteProxySettings]:
    """Get the current remote proxy settings."""
    return _remote_proxy_settings


def _parse_remote_proxy_settings(
    general_settings: Dict[str, Any]
) -> Optional[RemoteProxySettings]:
    """
    Parse remote proxy settings from general_settings.
    
    Settings can come from:
    - general_settings.remote_proxy (dict in config)
    - Environment variables (REMOTE_PROXY_*)
    """
    # Check for config-based settings
    remote_proxy_config = general_settings.get("remote_proxy")
    
    if remote_proxy_config and isinstance(remote_proxy_config, dict):
        # Resolve environment variable references
        central_url = remote_proxy_config.get("central_server_url", "")
        if central_url.startswith("os.environ/"):
            env_var = central_url.replace("os.environ/", "")
            central_url = os.environ.get(env_var, "")
            
        service_key = remote_proxy_config.get("service_key", "")
        if service_key.startswith("os.environ/"):
            env_var = service_key.replace("os.environ/", "")
            service_key = os.environ.get(env_var, "")
        
        if remote_proxy_config.get("enabled", False):
            return RemoteProxySettings(
                enabled=True,
                central_server_url=central_url,
                service_key=service_key,
                auth_cache_ttl_seconds=remote_proxy_config.get(
                    "auth_cache_ttl_seconds", 300
                ),
                config_poll_interval_seconds=remote_proxy_config.get(
                    "config_poll_interval_seconds", 60
                ),
                allow_cached_auth_on_central_failure=remote_proxy_config.get(
                    "allow_cached_auth_on_central_failure", True
                ),
                max_cache_age_on_failure_seconds=remote_proxy_config.get(
                    "max_cache_age_on_failure_seconds", 3600
                ),
                disable_management_routes=remote_proxy_config.get(
                    "disable_management_routes", True
                ),
                disable_ui=remote_proxy_config.get("disable_ui", True),
                spend_report_batch_size=remote_proxy_config.get(
                    "spend_report_batch_size", 100
                ),
                spend_report_interval_seconds=remote_proxy_config.get(
                    "spend_report_interval_seconds", 10
                ),
            )

    # Check for environment variable-based settings
    if os.environ.get("REMOTE_PROXY_ENABLED", "").lower() == "true":
        return RemoteProxySettings(
            enabled=True,
            central_server_url=os.environ.get("REMOTE_PROXY_CENTRAL_URL", ""),
            service_key=os.environ.get("REMOTE_PROXY_SERVICE_KEY", ""),
            auth_cache_ttl_seconds=int(
                os.environ.get("REMOTE_PROXY_AUTH_CACHE_TTL", "300")
            ),
            config_poll_interval_seconds=int(
                os.environ.get("REMOTE_PROXY_CONFIG_POLL_INTERVAL", "60")
            ),
            allow_cached_auth_on_central_failure=os.environ.get(
                "REMOTE_PROXY_ALLOW_CACHED_AUTH_ON_FAILURE", "true"
            ).lower() == "true",
            max_cache_age_on_failure_seconds=int(
                os.environ.get("REMOTE_PROXY_MAX_CACHE_AGE_ON_FAILURE", "3600")
            ),
            disable_management_routes=os.environ.get(
                "REMOTE_PROXY_DISABLE_MANAGEMENT_ROUTES", "true"
            ).lower() == "true",
            disable_ui=os.environ.get(
                "REMOTE_PROXY_DISABLE_UI", "true"
            ).lower() == "true",
        )

    return None


async def initialize_remote_proxy_mode(
    general_settings: Dict[str, Any],
    user_api_key_cache: DualCache,
    llm_router: Any,
    scheduler: Any,
) -> bool:
    """
    Initialize remote proxy mode.
    
    This sets up:
    - Remote auth handler for authentication
    - Remote spend reporter for spend tracking
    - Remote config loader for model configuration
    - Scheduled job for config polling
    
    Args:
        general_settings: The proxy's general_settings dict
        user_api_key_cache: Cache for storing auth data
        llm_router: The LLM router for adding models
        scheduler: APScheduler for periodic jobs
        
    Returns:
        True if remote proxy mode was initialized, False otherwise
    """
    global _remote_proxy_settings, _initialized

    # Parse settings
    settings = _parse_remote_proxy_settings(general_settings)
    
    if settings is None or not settings.enabled:
        verbose_proxy_logger.debug("Remote proxy mode not enabled")
        return False

    # Validate required settings
    if not settings.central_server_url:
        verbose_proxy_logger.error(
            "Remote proxy mode enabled but central_server_url not set"
        )
        return False

    if not settings.service_key:
        verbose_proxy_logger.error(
            "Remote proxy mode enabled but service_key not set"
        )
        return False

    verbose_proxy_logger.info(
        "Initializing remote proxy mode, central server: %s",
        settings.central_server_url,
    )

    _remote_proxy_settings = settings

    # 1. Initialize remote auth handler
    auth_handler = RemoteAuthHandler(
        settings=settings,
        cache=user_api_key_cache,
    )
    set_remote_auth_handler(auth_handler)

    # Check central server connectivity
    if await auth_handler.check_central_health():
        verbose_proxy_logger.info(
            "Remote proxy: central server is reachable"
        )
    else:
        verbose_proxy_logger.warning(
            "Remote proxy: central server is not reachable, will retry"
        )

    # 2. Initialize remote spend reporter
    spend_reporter = RemoteSpendReporter(settings=settings)
    set_remote_spend_reporter(spend_reporter)
    
    # Add to litellm callbacks
    litellm.callbacks.append(spend_reporter)
    verbose_proxy_logger.info(
        "Remote proxy: spend reporter initialized"
    )

    # 3. Initialize remote config loader
    config_loader = RemoteConfigLoader(settings=settings)
    set_remote_config_loader(config_loader)

    # 4. Initial model load
    if llm_router is not None:
        try:
            models_loaded = await load_models_from_central(settings, llm_router)
            verbose_proxy_logger.info(
                "Remote proxy: loaded %d models from central server",
                models_loaded,
            )
        except Exception as e:
            verbose_proxy_logger.error(
                "Remote proxy: failed to load initial models: %s",
                str(e),
            )

    # 5. Schedule config polling
    if scheduler is not None and llm_router is not None:
        async def poll_config():
            try:
                await load_models_from_central(settings, llm_router)
            except Exception as e:
                verbose_proxy_logger.error(
                    "Remote proxy: config poll failed: %s", str(e)
                )

        scheduler.add_job(
            poll_config,
            "interval",
            seconds=settings.config_poll_interval_seconds,
            id="remote_proxy_config_poll",
            replace_existing=True,
        )
        verbose_proxy_logger.info(
            "Remote proxy: config polling scheduled every %d seconds",
            settings.config_poll_interval_seconds,
        )

    _initialized = True
    verbose_proxy_logger.info("Remote proxy mode initialized successfully")
    return True


async def shutdown_remote_proxy_mode():
    """
    Shutdown remote proxy mode and cleanup resources.
    """
    global _remote_proxy_settings, _initialized

    if not _initialized:
        return

    verbose_proxy_logger.info("Shutting down remote proxy mode")

    # Shutdown auth handler
    auth_handler = get_remote_auth_handler()
    if auth_handler is not None:
        await auth_handler.close()
        set_remote_auth_handler(None)

    # Shutdown spend reporter (flush remaining records)
    spend_reporter = get_remote_spend_reporter()
    if spend_reporter is not None:
        await spend_reporter.close()
        set_remote_spend_reporter(None)
        
        # Remove from callbacks
        if spend_reporter in litellm.callbacks:
            litellm.callbacks.remove(spend_reporter)

    # Shutdown config loader
    config_loader = get_remote_config_loader()
    if config_loader is not None:
        await config_loader.close()
        set_remote_config_loader(None)

    _remote_proxy_settings = None
    _initialized = False
    verbose_proxy_logger.info("Remote proxy mode shutdown complete")
