"""
Remote Spend Reporter Callback

This callback reports spend data to a central management server
instead of writing directly to a local database.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from litellm._logging import verbose_proxy_logger
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy.internal_api_endpoints.types import (
    RemoteProxySettings,
    SpendRecord,
)


class RemoteSpendReporter(CustomLogger):
    """
    Reports spend to a central server instead of local database.
    
    This callback batches spend records and periodically flushes them
    to the central management server.
    """

    def __init__(
        self,
        settings: RemoteProxySettings,
        proxy_id: Optional[str] = None,
    ):
        self.settings = settings
        self.proxy_id = proxy_id or self._generate_proxy_id()
        
        self._spend_buffer: List[SpendRecord] = []
        self._buffer_lock = asyncio.Lock()
        self._last_flush_time = time.time()
        self._flush_task: Optional[asyncio.Task] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._shutdown = False

    def _generate_proxy_id(self) -> str:
        """Generate a unique proxy ID."""
        import socket
        import uuid

        hostname = socket.gethostname()
        unique_id = str(uuid.uuid4())[:8]
        return f"{hostname}-{unique_id}"

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
        """Shutdown the reporter and flush remaining records."""
        self._shutdown = True
        
        # Cancel the flush task if running
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        # Final flush
        await self._flush_to_central()

        # Close HTTP client
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    def _build_spend_record(
        self,
        kwargs: Dict[str, Any],
        response_obj: Any,
    ) -> SpendRecord:
        """Build a SpendRecord from callback kwargs and response."""
        litellm_params = kwargs.get("litellm_params", {})
        metadata = litellm_params.get("metadata", {})

        # Get token info
        token = metadata.get("user_api_key")
        if token and not token.startswith("sk-"):
            # Already hashed
            pass
        elif token:
            # Hash the token
            from litellm.proxy.utils import hash_token

            token = hash_token(token)

        return SpendRecord(
            token=token,
            user_id=metadata.get("user_api_key_user_id"),
            team_id=metadata.get("user_api_key_team_id"),
            org_id=metadata.get("user_api_key_org_id"),
            end_user_id=metadata.get("user_api_key_end_user_id"),
            model=kwargs.get("model"),
            response_cost=kwargs.get("response_cost", 0.0),
            total_tokens=getattr(response_obj, "usage", {}).get("total_tokens")
            if hasattr(response_obj, "usage")
            else None,
            prompt_tokens=getattr(response_obj, "usage", {}).get("prompt_tokens")
            if hasattr(response_obj, "usage")
            else None,
            completion_tokens=getattr(response_obj, "usage", {}).get("completion_tokens")
            if hasattr(response_obj, "usage")
            else None,
            request_id=kwargs.get("litellm_call_id"),
            timestamp=datetime.now(timezone.utc),
            api_base=litellm_params.get("api_base"),
            custom_llm_provider=litellm_params.get("custom_llm_provider"),
            call_type=kwargs.get("call_type"),
        )

    async def _flush_to_central(self) -> None:
        """Flush buffered spend records to central server."""
        async with self._buffer_lock:
            if not self._spend_buffer:
                return

            records = self._spend_buffer.copy()
            self._spend_buffer.clear()

        if not records:
            return

        verbose_proxy_logger.debug(
            "Remote spend reporter: flushing %d records to central",
            len(records),
        )

        try:
            # Convert to dicts for JSON serialization
            records_data = []
            for record in records:
                if hasattr(record, "model_dump"):
                    record_dict = record.model_dump(exclude_none=True)
                else:
                    record_dict = record.dict(exclude_none=True)
                
                # Convert datetime to ISO string
                if "timestamp" in record_dict and record_dict["timestamp"]:
                    record_dict["timestamp"] = record_dict["timestamp"].isoformat()
                
                records_data.append(record_dict)

            response = await self.http_client.post(
                "/internal/v1/spend/record/batch",
                json={
                    "spend_records": records_data,
                    "proxy_id": self.proxy_id,
                },
            )

            if response.status_code != 200:
                verbose_proxy_logger.error(
                    "Remote spend reporter: central server returned %s - %s",
                    response.status_code,
                    response.text,
                )
                # Re-queue failed records
                async with self._buffer_lock:
                    self._spend_buffer.extend(records)
            else:
                result = response.json()
                verbose_proxy_logger.debug(
                    "Remote spend reporter: recorded %d, errors: %d",
                    result.get("recorded", 0),
                    len(result.get("errors", [])),
                )

        except Exception as e:
            verbose_proxy_logger.error(
                "Remote spend reporter: failed to send to central: %s",
                str(e),
            )
            # Re-queue failed records (with limit to prevent unbounded growth)
            async with self._buffer_lock:
                max_buffer_size = self.settings.spend_report_batch_size * 10
                if len(self._spend_buffer) + len(records) <= max_buffer_size:
                    self._spend_buffer.extend(records)
                else:
                    verbose_proxy_logger.warning(
                        "Remote spend reporter: buffer full, dropping %d records",
                        len(records),
                    )

        self._last_flush_time = time.time()

    async def _maybe_flush(self) -> None:
        """Check if we should flush based on buffer size or time."""
        should_flush = False

        async with self._buffer_lock:
            buffer_size = len(self._spend_buffer)

        # Flush if buffer is large enough
        if buffer_size >= self.settings.spend_report_batch_size:
            should_flush = True

        # Flush if enough time has passed
        time_since_flush = time.time() - self._last_flush_time
        if time_since_flush >= self.settings.spend_report_interval_seconds and buffer_size > 0:
            should_flush = True

        if should_flush:
            await self._flush_to_central()

    async def async_log_success_event(
        self,
        kwargs: Dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        """
        Called on successful LLM request completion.
        
        Buffers the spend record and flushes to central when appropriate.
        """
        if self._shutdown:
            return

        try:
            record = self._build_spend_record(kwargs, response_obj)

            async with self._buffer_lock:
                self._spend_buffer.append(record)

            # Check if we should flush
            await self._maybe_flush()

        except Exception as e:
            verbose_proxy_logger.error(
                "Remote spend reporter: error processing spend: %s",
                str(e),
            )

    def log_success_event(
        self,
        kwargs: Dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        """Sync version - schedules async processing."""
        try:
            asyncio.create_task(
                self.async_log_success_event(kwargs, response_obj, start_time, end_time)
            )
        except RuntimeError:
            # No event loop running
            pass

    async def async_log_failure_event(
        self,
        kwargs: Dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        """Called on failed LLM request - we don't report failed requests."""
        pass

    def log_failure_event(
        self,
        kwargs: Dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        """Sync version for failures - no-op."""
        pass


# Global instance
_remote_spend_reporter: Optional[RemoteSpendReporter] = None


def get_remote_spend_reporter() -> Optional[RemoteSpendReporter]:
    """Get the global remote spend reporter instance."""
    return _remote_spend_reporter


def set_remote_spend_reporter(reporter: Optional[RemoteSpendReporter]) -> None:
    """Set the global remote spend reporter instance."""
    global _remote_spend_reporter
    _remote_spend_reporter = reporter
