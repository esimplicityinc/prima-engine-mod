"""
Remote Config Loader for Remote Proxy Mode

This module handles fetching configuration (models, settings) from a central
management server instead of loading from a local database or config file.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from litellm._logging import verbose_proxy_logger
from litellm.proxy.internal_api_endpoints.types import (
    ModelConfigItem,
    RemoteProxySettings,
)


class RemoteConfigLoader:
    """
    Loads configuration from a central management server.
    
    Used by remote proxies to fetch model configuration and settings
    from the central server.
    """

    def __init__(self, settings: RemoteProxySettings):
        self.settings = settings
        self._http_client: Optional[httpx.AsyncClient] = None
        self._last_config_fetch: Optional[datetime] = None
        self._cached_models: List[ModelConfigItem] = []
        self._cached_settings: Dict[str, Any] = {}

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

    async def fetch_models(self) -> List[Dict[str, Any]]:
        """
        Fetch model configuration from the central server.
        
        Returns:
            List of model configurations in the format expected by the router.
        """
        verbose_proxy_logger.debug(
            "Remote config loader: fetching models from central server"
        )

        try:
            response = await self.http_client.get("/internal/v1/config/models")

            if response.status_code != 200:
                verbose_proxy_logger.error(
                    "Remote config loader: central server returned %s - %s",
                    response.status_code,
                    response.text,
                )
                # Return cached models if available
                return self._models_to_router_format(self._cached_models)

            data = response.json()
            models = data.get("models", [])

            # Cache the models
            self._cached_models = [ModelConfigItem(**m) for m in models]
            self._last_config_fetch = datetime.now(timezone.utc)

            verbose_proxy_logger.debug(
                "Remote config loader: fetched %d models from central",
                len(models),
            )

            return self._models_to_router_format(self._cached_models)

        except Exception as e:
            verbose_proxy_logger.error(
                "Remote config loader: failed to fetch models: %s",
                str(e),
            )
            # Return cached models if available
            return self._models_to_router_format(self._cached_models)

    def _models_to_router_format(
        self, models: List[ModelConfigItem]
    ) -> List[Dict[str, Any]]:
        """
        Convert ModelConfigItem list to the format expected by the router.
        """
        router_models = []
        for model in models:
            router_model = {
                "model_name": model.model_name,
                "litellm_params": model.litellm_params,
            }
            if model.model_info:
                router_model["model_info"] = model.model_info
            router_models.append(router_model)
        return router_models

    async def fetch_settings(self) -> Dict[str, Any]:
        """
        Fetch proxy settings from the central server.
        
        Returns:
            Dict containing general_settings, router_settings, etc.
        """
        verbose_proxy_logger.debug(
            "Remote config loader: fetching settings from central server"
        )

        try:
            response = await self.http_client.get("/internal/v1/config/settings")

            if response.status_code != 200:
                verbose_proxy_logger.error(
                    "Remote config loader: central server returned %s - %s",
                    response.status_code,
                    response.text,
                )
                return self._cached_settings

            data = response.json()
            self._cached_settings = data

            verbose_proxy_logger.debug(
                "Remote config loader: fetched settings from central"
            )

            return data

        except Exception as e:
            verbose_proxy_logger.error(
                "Remote config loader: failed to fetch settings: %s",
                str(e),
            )
            return self._cached_settings

    async def check_central_health(self) -> bool:
        """Check if the central server is reachable."""
        try:
            response = await self.http_client.get("/internal/v1/config/health")
            return response.status_code == 200
        except Exception as e:
            verbose_proxy_logger.debug(
                "Remote config loader: health check failed: %s", str(e)
            )
            return False


async def load_models_from_central(
    settings: RemoteProxySettings,
    llm_router: Any,
) -> int:
    """
    Load models from central server and add to the router.
    
    This is called periodically to sync model configuration.
    
    Args:
        settings: Remote proxy settings
        llm_router: The LLM router to add models to
        
    Returns:
        Number of models loaded
    """
    from litellm.router import Deployment
    from litellm.types.router import LiteLLM_Params

    loader = RemoteConfigLoader(settings)
    try:
        models = await loader.fetch_models()

        if not models:
            verbose_proxy_logger.warning(
                "Remote config loader: no models returned from central"
            )
            return 0

        # Add models to router
        added = 0
        for model_config in models:
            try:
                # Build LiteLLM_Params
                litellm_params = model_config.get("litellm_params", {})
                
                # Create deployment
                deployment = Deployment(
                    model_name=model_config.get("model_name", ""),
                    litellm_params=LiteLLM_Params(**litellm_params),
                    model_info=model_config.get("model_info", {}),
                )

                # Upsert to router (sync method)
                llm_router.upsert_deployment(deployment=deployment)
                added += 1

            except Exception as e:
                verbose_proxy_logger.error(
                    "Remote config loader: failed to add model %s: %s",
                    model_config.get("model_name"),
                    str(e),
                )

        verbose_proxy_logger.info(
            "Remote config loader: loaded %d models from central",
            added,
        )
        return added

    finally:
        await loader.close()


# Global loader instance
_remote_config_loader: Optional[RemoteConfigLoader] = None


def get_remote_config_loader() -> Optional[RemoteConfigLoader]:
    """Get the global remote config loader instance."""
    return _remote_config_loader


def set_remote_config_loader(loader: Optional[RemoteConfigLoader]) -> None:
    """Set the global remote config loader instance."""
    global _remote_config_loader
    _remote_config_loader = loader
