# Remote Proxy Docker Setup

This directory contains configuration for running LiteLLM in a distributed "remote proxy" architecture where:

- A **central management server** handles database, admin UI, and key management
- **Remote proxies** run without database access, authenticating via the central server

## Architecture

```
                    ┌─────────────────────────────────────────────────┐
                    │           Central Management Server             │
                    │            http://localhost:4000                │
                    │  ┌──────────────────────────────────────────┐   │
                    │  │  PostgreSQL    Redis    Admin UI         │   │
                    │  │                                          │   │
                    │  │  Internal APIs:                          │   │
                    │  │  POST /internal/v1/auth/validate         │   │
                    │  │  POST /internal/v1/spend/record          │   │
                    │  │  GET  /internal/v1/config/models         │   │
                    │  └──────────────────────────────────────────┘   │
                    └───────────────────────┬─────────────────────────┘
                                            │
                         ┌──────────────────┼──────────────────┐
                         ▼                  ▼                  ▼
                   ┌──────────┐       ┌──────────┐       ┌──────────┐
                   │  Remote  │       │  Remote  │       │   ...    │
                   │  Proxy   │       │  Proxy   │       │          │
                   │  East    │       │  West    │       │          │
                   │ :4001    │       │ :4002    │       │          │
                   └──────────┘       └──────────┘       └──────────┘
```

## Quick Start

1. **Copy the example environment file:**
   ```bash
   cp docker/remote-proxy.env.example .env
   ```

2. **Edit `.env` with your API keys:**
   ```bash
   # Required: Add your LLM API keys
   OPENAI_API_KEY=sk-your-key
   ANTHROPIC_API_KEY=sk-ant-your-key
   
   # Change these for production!
   LITELLM_MASTER_KEY=sk-your-master-key
   LITELLM_INTERNAL_SERVICE_KEY=sk-your-internal-key
   ```

3. **Start the stack:**
   ```bash
   docker-compose -f docker-compose.remote-proxy.yml up
   ```

4. **Access the services:**
   - Central Server (Admin UI): http://localhost:4000/ui
   - Remote Proxy East: http://localhost:4001
   - Remote Proxy West: http://localhost:4002

## Configuration Files

- `central-config.yaml` - Configuration for the central management server
- `remote-proxy-config.yaml` - Configuration for remote proxy instances

## How It Works

### Central Server
- Has full database access (PostgreSQL)
- Manages API keys, teams, budgets via Admin UI
- Exposes internal API endpoints for remote proxies
- Authenticates internal service keys from remote proxies

### Remote Proxies
- No database connection required
- On each API request:
  1. Check local cache for auth data
  2. If cache miss, call central server's `/internal/v1/auth/validate`
  3. Cache the response for configured TTL (default: 5 minutes)
- Batch spend records and send to central server periodically
- Graceful degradation: use stale cache if central is temporarily unreachable

## Security Considerations

1. **Change default keys** - The example keys are not secure for production
2. **Internal network** - Remote proxies should communicate with central over a private network
3. **mTLS recommended** - For production, enable mutual TLS between services
4. **Service key rotation** - Rotate `LITELLM_INTERNAL_SERVICE_KEY` periodically

## Scaling

To add more remote proxies, duplicate the `remote-proxy-east` service in `docker-compose.remote-proxy.yml`:

```yaml
remote-proxy-asia:
  # ... same config as other remote proxies
  ports:
    - "4003:4000"
  environment:
    PROXY_ID: "remote-asia"
    # ... other env vars
```

## Troubleshooting

### Remote proxy can't connect to central
- Check that central server is healthy: `curl http://localhost:4000/health/liveliness`
- Verify `CENTRAL_SERVER_URL` is correct (use Docker service name `central` within compose)
- Check `CENTRAL_SERVICE_KEY` matches `LITELLM_INTERNAL_SERVICE_KEY` on central

### Auth failures on remote proxy
- Check central server logs for authentication errors
- Verify the API key exists in central server's database
- Check if central server is reachable from remote proxy network

### Spend not appearing in central
- Remote proxies batch spend records - wait for the flush interval (default: 10s)
- Check remote proxy logs for spend reporting errors
- Verify central server's database is accessible
