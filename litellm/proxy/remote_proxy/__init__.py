"""
Remote Proxy Mode

This package provides functionality for running LiteLLM as a "remote proxy"
that communicates with a central management server instead of requiring
direct database access.

Usage:
    Configure remote_proxy in your config.yaml:
    
    ```yaml
    general_settings:
      remote_proxy:
        enabled: true
        central_server_url: "https://central.example.com"
        service_key: "os.environ/CENTRAL_SERVICE_KEY"
    ```

The remote proxy will:
- Authenticate API keys by calling the central server
- Report spend data to the central server
- Fetch model configuration from the central server
- Cache authentication results locally for performance
"""

from litellm.proxy.remote_proxy.initialization import (
    initialize_remote_proxy_mode,
    is_remote_proxy_mode,
    shutdown_remote_proxy_mode,
    get_remote_proxy_settings,
)

__all__ = [
    "initialize_remote_proxy_mode",
    "is_remote_proxy_mode",
    "shutdown_remote_proxy_mode",
    "get_remote_proxy_settings",
]
