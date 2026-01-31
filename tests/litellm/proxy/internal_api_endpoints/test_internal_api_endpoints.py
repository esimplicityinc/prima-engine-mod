"""
Tests for Internal API Endpoints (Remote Proxy Mode)

These tests cover the internal API endpoints that allow remote proxies
to communicate with a central management server.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.internal_api_endpoints.types import (
    InternalAuthValidateRequest,
    InternalAuthValidateResponse,
    InternalConfigModelsResponse,
    InternalConfigSettingsResponse,
    InternalServiceKey,
    InternalSpendRecordRequest,
    InternalSpendRecordResponse,
    ModelConfigItem,
    RemoteProxySettings,
    SpendRecord,
)


class TestInternalServiceKeyAuth:
    """Tests for internal service key authentication."""

    def test_internal_service_key_model(self):
        """Test InternalServiceKey model validation."""
        key = InternalServiceKey(
            key="sk-test-internal-key",
            name="Test Service Key",
            allowed_endpoints=["internal/*"],
        )
        assert key.key == "sk-test-internal-key"
        assert key.name == "Test Service Key"
        assert key.allowed_endpoints == ["internal/*"]

    def test_internal_service_key_with_expiry(self):
        """Test InternalServiceKey with expiration date."""
        expires = datetime(2025, 12, 31, tzinfo=timezone.utc)
        key = InternalServiceKey(
            key="sk-test-key",
            name="Expiring Key",
            expires_at=expires,
        )
        assert key.expires_at == expires


class TestRemoteProxySettings:
    """Tests for RemoteProxySettings model."""

    def test_default_settings(self):
        """Test RemoteProxySettings default values."""
        settings = RemoteProxySettings(
            enabled=True,
            central_server_url="https://central.example.com",
            service_key="sk-service-key",
        )
        assert settings.enabled is True
        assert settings.auth_cache_ttl_seconds == 300
        assert settings.config_poll_interval_seconds == 60
        assert settings.allow_cached_auth_on_central_failure is True
        assert settings.disable_management_routes is True
        assert settings.disable_ui is True

    def test_custom_settings(self):
        """Test RemoteProxySettings with custom values."""
        settings = RemoteProxySettings(
            enabled=True,
            central_server_url="https://central.example.com",
            service_key="sk-service-key",
            auth_cache_ttl_seconds=600,
            config_poll_interval_seconds=120,
            allow_cached_auth_on_central_failure=False,
        )
        assert settings.auth_cache_ttl_seconds == 600
        assert settings.config_poll_interval_seconds == 120
        assert settings.allow_cached_auth_on_central_failure is False


class TestInternalAuthValidateTypes:
    """Tests for auth validation request/response types."""

    def test_auth_validate_request(self):
        """Test InternalAuthValidateRequest model."""
        request = InternalAuthValidateRequest(
            api_key="sk-user-key",
            request_route="/v1/chat/completions",
            request_model="gpt-4",
            end_user_id="user-123",
        )
        assert request.api_key == "sk-user-key"
        assert request.request_route == "/v1/chat/completions"
        assert request.request_model == "gpt-4"
        assert request.end_user_id == "user-123"

    def test_auth_validate_response_success(self):
        """Test InternalAuthValidateResponse for successful validation."""
        response = InternalAuthValidateResponse(
            valid=True,
            user_api_key_auth={
                "token": "hashed-token",
                "user_id": "user-123",
                "models": ["gpt-4"],
            },
            cache_ttl_seconds=300,
        )
        assert response.valid is True
        assert response.user_api_key_auth is not None
        assert response.error is None

    def test_auth_validate_response_failure(self):
        """Test InternalAuthValidateResponse for failed validation."""
        response = InternalAuthValidateResponse(
            valid=False,
            error="API key expired",
            error_code="key_expired",
            cache_ttl_seconds=60,
        )
        assert response.valid is False
        assert response.error == "API key expired"
        assert response.error_code == "key_expired"


class TestInternalSpendRecordTypes:
    """Tests for spend record request/response types."""

    def test_spend_record(self):
        """Test SpendRecord model."""
        record = SpendRecord(
            token="hashed-token",
            user_id="user-123",
            team_id="team-456",
            model="gpt-4",
            response_cost=0.0234,
            total_tokens=1500,
            prompt_tokens=1000,
            completion_tokens=500,
            request_id="req-uuid",
            timestamp=datetime.now(timezone.utc),
        )
        assert record.token == "hashed-token"
        assert record.response_cost == 0.0234
        assert record.total_tokens == 1500

    def test_spend_record_request(self):
        """Test InternalSpendRecordRequest model."""
        records = [
            SpendRecord(
                token="token-1",
                response_cost=0.01,
            ),
            SpendRecord(
                token="token-2",
                response_cost=0.02,
            ),
        ]
        request = InternalSpendRecordRequest(
            spend_records=records,
            proxy_id="proxy-east-1",
        )
        assert len(request.spend_records) == 2
        assert request.proxy_id == "proxy-east-1"

    def test_spend_record_response(self):
        """Test InternalSpendRecordResponse model."""
        response = InternalSpendRecordResponse(
            recorded=5,
            errors=[{"request_id": "req-1", "error": "Database error"}],
        )
        assert response.recorded == 5
        assert len(response.errors) == 1


class TestInternalConfigTypes:
    """Tests for config endpoint types."""

    def test_model_config_item(self):
        """Test ModelConfigItem model."""
        model = ModelConfigItem(
            model_name="gpt-4",
            litellm_params={
                "model": "azure/gpt-4",
                "api_base": "https://example.openai.azure.com",
            },
            model_info={"description": "GPT-4 model"},
        )
        assert model.model_name == "gpt-4"
        assert model.litellm_params["model"] == "azure/gpt-4"

    def test_config_models_response(self):
        """Test InternalConfigModelsResponse model."""
        models = [
            ModelConfigItem(
                model_name="gpt-4",
                litellm_params={"model": "openai/gpt-4"},
            ),
            ModelConfigItem(
                model_name="claude-3",
                litellm_params={"model": "anthropic/claude-3-opus"},
            ),
        ]
        response = InternalConfigModelsResponse(
            models=models,
            last_updated=datetime.now(timezone.utc),
        )
        assert len(response.models) == 2
        assert response.last_updated is not None

    def test_config_settings_response(self):
        """Test InternalConfigSettingsResponse model."""
        response = InternalConfigSettingsResponse(
            general_settings={"max_parallel_requests": 100},
            router_settings={"routing_strategy": "simple-shuffle"},
            litellm_settings={"drop_params": True},
        )
        assert response.general_settings["max_parallel_requests"] == 100
        assert response.router_settings["routing_strategy"] == "simple-shuffle"


class TestRemoteAuthHandler:
    """Tests for RemoteAuthHandler."""

    @pytest.mark.asyncio
    async def test_remote_auth_handler_validate_key_cache_hit(self):
        """Test that RemoteAuthHandler uses cached data when available."""
        from litellm.caching import DualCache
        from litellm.proxy.auth.remote_auth_handler import RemoteAuthHandler

        settings = RemoteProxySettings(
            enabled=True,
            central_server_url="https://central.example.com",
            service_key="sk-service-key",
            auth_cache_ttl_seconds=300,
        )
        
        cache = MagicMock(spec=DualCache)
        
        # Simulate cached data
        import time
        cached_data = {
            "valid": True,
            "user_api_key_auth": {
                "token": "cached-token",
                "user_id": "user-123",
            },
            "cache_ttl_seconds": 300,
            "cached_at": time.time(),  # Just cached
        }
        cache.async_get_cache = AsyncMock(return_value=cached_data)

        handler = RemoteAuthHandler(settings=settings, cache=cache)
        
        result = await handler.validate_key(api_key="sk-test-key")
        
        assert result.token == "cached-token"
        assert result.user_id == "user-123"
        # Should not have made HTTP call since cache was valid
        cache.async_get_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_remote_auth_handler_validate_key_cache_miss(self):
        """Test that RemoteAuthHandler calls central server on cache miss."""
        from litellm.caching import DualCache
        from litellm.proxy.auth.remote_auth_handler import RemoteAuthHandler

        settings = RemoteProxySettings(
            enabled=True,
            central_server_url="https://central.example.com",
            service_key="sk-service-key",
        )
        
        cache = MagicMock(spec=DualCache)
        cache.async_get_cache = AsyncMock(return_value=None)  # Cache miss
        cache.async_set_cache = AsyncMock()

        handler = RemoteAuthHandler(settings=settings, cache=cache)
        
        # Mock the HTTP call
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "valid": True,
            "user_api_key_auth": {
                "token": "new-token",
                "user_id": "user-456",
            },
            "cache_ttl_seconds": 300,
        }
        
        with patch.object(handler, "http_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            
            result = await handler.validate_key(api_key="sk-test-key")
        
        assert result.token == "new-token"
        assert result.user_id == "user-456"
        # Should have set cache with new data
        cache.async_set_cache.assert_called_once()


class TestRemoteSpendReporter:
    """Tests for RemoteSpendReporter callback."""

    def test_spend_reporter_build_spend_record(self):
        """Test that RemoteSpendReporter builds correct spend records."""
        from litellm.proxy.hooks.remote_spend_reporter import RemoteSpendReporter

        settings = RemoteProxySettings(
            enabled=True,
            central_server_url="https://central.example.com",
            service_key="sk-service-key",
        )
        
        reporter = RemoteSpendReporter(settings=settings)
        
        kwargs = {
            "model": "gpt-4",
            "response_cost": 0.05,
            "litellm_call_id": "call-123",
            "litellm_params": {
                "metadata": {
                    "user_api_key": "sk-user-key",
                    "user_api_key_user_id": "user-123",
                    "user_api_key_team_id": "team-456",
                },
                "api_base": "https://api.openai.com",
            },
        }
        
        mock_response = MagicMock()
        mock_response.usage = {
            "total_tokens": 1500,
            "prompt_tokens": 1000,
            "completion_tokens": 500,
        }
        
        with patch("litellm.proxy.utils.hash_token", return_value="hashed-token"):
            record = reporter._build_spend_record(kwargs, mock_response)
        
        assert record.model == "gpt-4"
        assert record.response_cost == 0.05
        assert record.user_id == "user-123"
        assert record.team_id == "team-456"


class TestRemoteConfigLoader:
    """Tests for RemoteConfigLoader."""

    @pytest.mark.asyncio
    async def test_fetch_models(self):
        """Test fetching models from central server."""
        from litellm.proxy.common_utils.remote_config_loader import RemoteConfigLoader

        settings = RemoteProxySettings(
            enabled=True,
            central_server_url="https://central.example.com",
            service_key="sk-service-key",
        )
        
        loader = RemoteConfigLoader(settings=settings)
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {
                    "model_name": "gpt-4",
                    "litellm_params": {"model": "openai/gpt-4"},
                    "model_info": {},
                }
            ],
            "last_updated": "2024-01-15T10:00:00Z",
        }
        
        with patch.object(loader, "http_client") as mock_client:
            mock_client.get = AsyncMock(return_value=mock_response)
            
            models = await loader.fetch_models()
        
        assert len(models) == 1
        assert models[0]["model_name"] == "gpt-4"
        
        await loader.close()


class TestRemoteProxyInitialization:
    """Tests for remote proxy initialization."""

    def test_parse_settings_from_config(self):
        """Test parsing remote proxy settings from config."""
        from litellm.proxy.remote_proxy.initialization import (
            _parse_remote_proxy_settings,
        )

        general_settings = {
            "remote_proxy": {
                "enabled": True,
                "central_server_url": "https://central.example.com",
                "service_key": "sk-test-key",
                "auth_cache_ttl_seconds": 600,
            }
        }
        
        settings = _parse_remote_proxy_settings(general_settings)
        
        assert settings is not None
        assert settings.enabled is True
        assert settings.central_server_url == "https://central.example.com"
        assert settings.service_key == "sk-test-key"
        assert settings.auth_cache_ttl_seconds == 600

    def test_parse_settings_disabled(self):
        """Test that disabled settings return None."""
        from litellm.proxy.remote_proxy.initialization import (
            _parse_remote_proxy_settings,
        )

        general_settings = {
            "remote_proxy": {
                "enabled": False,
            }
        }
        
        settings = _parse_remote_proxy_settings(general_settings)
        
        assert settings is None

    def test_parse_settings_not_configured(self):
        """Test that missing settings return None."""
        from litellm.proxy.remote_proxy.initialization import (
            _parse_remote_proxy_settings,
        )

        general_settings = {}
        
        settings = _parse_remote_proxy_settings(general_settings)
        
        assert settings is None
