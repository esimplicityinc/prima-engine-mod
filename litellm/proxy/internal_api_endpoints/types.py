"""
Type definitions for Internal API Endpoints and Remote Proxy Mode
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from litellm.types.utils import LiteLLMPydanticObjectBase


# =============================================================================
# Remote Proxy Configuration Types
# =============================================================================


class RemoteProxySettings(LiteLLMPydanticObjectBase):
    """
    Configuration for remote proxy mode.
    
    When enabled, the proxy operates as a "thin" proxy that communicates
    with a central management server for authentication, spend tracking,
    and configuration instead of requiring direct database access.
    """

    enabled: bool = Field(
        default=False,
        description="Enable remote proxy mode",
    )
    central_server_url: str = Field(
        default="",
        description="URL of the central management server (e.g., 'https://central.example.com')",
    )
    service_key: str = Field(
        default="",
        description="Service key for authenticating with the central server",
    )

    # Caching settings
    auth_cache_ttl_seconds: int = Field(
        default=300,
        description="How long to cache authentication results (default: 5 minutes)",
    )
    config_poll_interval_seconds: int = Field(
        default=60,
        description="How often to poll central server for config changes (default: 60 seconds)",
    )

    # Fallback behavior
    allow_cached_auth_on_central_failure: bool = Field(
        default=True,
        description="Allow using cached auth data if central server is unreachable",
    )
    max_cache_age_on_failure_seconds: int = Field(
        default=3600,
        description="Maximum age of cached auth data to use during central server failure (default: 1 hour)",
    )

    # Feature flags
    disable_management_routes: bool = Field(
        default=True,
        description="Disable management routes on remote proxy (recommended)",
    )
    disable_ui: bool = Field(
        default=True,
        description="Disable admin UI on remote proxy (recommended)",
    )

    # Spend reporting
    spend_report_batch_size: int = Field(
        default=100,
        description="Number of spend records to batch before sending to central",
    )
    spend_report_interval_seconds: int = Field(
        default=10,
        description="How often to flush spend records to central server",
    )


class InternalServiceKey(LiteLLMPydanticObjectBase):
    """
    Configuration for an internal service key used by remote proxies
    to authenticate with the central server.
    """

    key: str = Field(
        description="The service key value",
    )
    name: str = Field(
        description="Human-readable name for this service key",
    )
    allowed_endpoints: List[str] = Field(
        default=["internal/*"],
        description="List of endpoint patterns this key can access",
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="When this key was created",
    )
    expires_at: Optional[datetime] = Field(
        default=None,
        description="When this key expires (None = never)",
    )


# =============================================================================
# Internal API Request/Response Types
# =============================================================================


class InternalAuthValidateRequest(LiteLLMPydanticObjectBase):
    """Request body for POST /internal/v1/auth/validate"""

    api_key: str = Field(
        description="The API key to validate",
    )
    request_route: Optional[str] = Field(
        default=None,
        description="The route being accessed (e.g., '/v1/chat/completions')",
    )
    request_model: Optional[str] = Field(
        default=None,
        description="The model being requested",
    )
    end_user_id: Optional[str] = Field(
        default=None,
        description="End user ID if provided in the request",
    )


class InternalAuthValidateResponse(LiteLLMPydanticObjectBase):
    """Response body for POST /internal/v1/auth/validate"""

    valid: bool = Field(
        description="Whether the API key is valid",
    )
    user_api_key_auth: Optional[Dict[str, Any]] = Field(
        default=None,
        description="UserAPIKeyAuth data if valid",
    )
    cache_ttl_seconds: int = Field(
        default=300,
        description="How long the remote proxy should cache this result",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if validation failed",
    )
    error_code: Optional[str] = Field(
        default=None,
        description="Error code if validation failed",
    )


class SpendRecord(LiteLLMPydanticObjectBase):
    """A single spend record from a remote proxy"""

    token: Optional[str] = Field(
        default=None,
        description="Hashed API key token",
    )
    user_id: Optional[str] = Field(
        default=None,
        description="User ID",
    )
    team_id: Optional[str] = Field(
        default=None,
        description="Team ID",
    )
    org_id: Optional[str] = Field(
        default=None,
        description="Organization ID",
    )
    end_user_id: Optional[str] = Field(
        default=None,
        description="End user ID",
    )
    model: Optional[str] = Field(
        default=None,
        description="Model used",
    )
    response_cost: float = Field(
        description="Cost of this request",
    )
    total_tokens: Optional[int] = Field(
        default=None,
        description="Total tokens used",
    )
    prompt_tokens: Optional[int] = Field(
        default=None,
        description="Prompt tokens used",
    )
    completion_tokens: Optional[int] = Field(
        default=None,
        description="Completion tokens used",
    )
    request_id: Optional[str] = Field(
        default=None,
        description="Unique request ID",
    )
    timestamp: Optional[datetime] = Field(
        default=None,
        description="When this request occurred",
    )
    # Additional metadata
    api_base: Optional[str] = Field(
        default=None,
        description="API base URL used",
    )
    custom_llm_provider: Optional[str] = Field(
        default=None,
        description="LLM provider",
    )
    call_type: Optional[str] = Field(
        default=None,
        description="Type of call (completion, embedding, etc.)",
    )


class InternalSpendRecordRequest(LiteLLMPydanticObjectBase):
    """Request body for POST /internal/v1/spend/record"""

    spend_records: List[SpendRecord] = Field(
        description="List of spend records to record",
    )
    proxy_id: Optional[str] = Field(
        default=None,
        description="Identifier for the remote proxy sending this data",
    )


class InternalSpendRecordResponse(LiteLLMPydanticObjectBase):
    """Response body for POST /internal/v1/spend/record"""

    recorded: int = Field(
        description="Number of records successfully recorded",
    )
    errors: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Any errors that occurred",
    )


class ModelConfigItem(LiteLLMPydanticObjectBase):
    """Configuration for a single model"""

    model_name: str = Field(
        description="Name to use for this model",
    )
    litellm_params: Dict[str, Any] = Field(
        description="LiteLLM parameters (model, api_base, etc.)",
    )
    model_info: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional model metadata",
    )


class InternalConfigModelsResponse(LiteLLMPydanticObjectBase):
    """Response body for GET /internal/v1/config/models"""

    models: List[ModelConfigItem] = Field(
        description="List of model configurations",
    )
    last_updated: Optional[datetime] = Field(
        default=None,
        description="When the config was last updated",
    )


class InternalConfigSettingsResponse(LiteLLMPydanticObjectBase):
    """Response body for GET /internal/v1/config/settings"""

    general_settings: Dict[str, Any] = Field(
        default_factory=dict,
        description="General settings for the proxy",
    )
    router_settings: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Router settings",
    )
    litellm_settings: Optional[Dict[str, Any]] = Field(
        default=None,
        description="LiteLLM settings",
    )
