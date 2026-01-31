"""
Internal API Endpoints for Remote Proxy Mode

This package provides internal API endpoints that remote (thin) proxies
can call to authenticate requests, report spend, and fetch configuration
from a central management server.

Endpoints:
- POST /internal/v1/auth/validate - Validate API keys
- POST /internal/v1/spend/record - Record spend from remote proxies
- GET /internal/v1/config/models - Fetch model configuration
- GET /internal/v1/config/settings - Fetch general settings
"""

from litellm.proxy.internal_api_endpoints.internal_router import (
    internal_api_router,
)

__all__ = ["internal_api_router"]
