"""
Internal Spend Endpoints

POST /internal/v1/spend/record - Record spend from remote proxies
"""

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request

from litellm._logging import verbose_proxy_logger
from litellm.proxy.internal_api_endpoints.internal_router import (
    verify_internal_service_key,
)
from litellm.proxy.internal_api_endpoints.types import (
    InternalServiceKey,
    InternalSpendRecordRequest,
    InternalSpendRecordResponse,
    SpendRecord,
)

router = APIRouter(tags=["internal-spend"])


async def _process_spend_record(record: SpendRecord) -> Dict[str, Any]:
    """
    Process a single spend record and update the database.
    
    This reuses the existing spend tracking infrastructure.
    """
    from litellm.proxy.proxy_server import (
        prisma_client,
        proxy_logging_obj,
        user_api_key_cache,
    )

    if prisma_client is None:
        raise ValueError("Database not configured on central server")

    # Use the existing spend update infrastructure
    await proxy_logging_obj.db_spend_update_writer.update_database(
        token=record.token,
        user_id=record.user_id,
        end_user_id=record.end_user_id,
        team_id=record.team_id,
        org_id=record.org_id,
        kwargs={
            "model": record.model,
            "litellm_params": {
                "metadata": {
                    "user_api_key": record.token,
                    "user_api_key_user_id": record.user_id,
                    "user_api_key_team_id": record.team_id,
                    "user_api_key_org_id": record.org_id,
                    "user_api_key_end_user_id": record.end_user_id,
                },
                "api_base": record.api_base,
                "custom_llm_provider": record.custom_llm_provider,
            },
            "call_type": record.call_type or "completion",
        },
        completion_response=None,  # We don't have the full response
        start_time=record.timestamp,
        end_time=record.timestamp,
        response_cost=record.response_cost,
    )

    return {"request_id": record.request_id, "status": "recorded"}


@router.post(
    "/spend/record",
    response_model=InternalSpendRecordResponse,
    summary="Record Spend",
    description="Record spend data from remote proxies",
)
async def internal_spend_record(
    request: Request,
    body: InternalSpendRecordRequest,
    service_key: InternalServiceKey = Depends(verify_internal_service_key),
) -> InternalSpendRecordResponse:
    """
    Record spend data from a remote proxy.
    
    Remote proxies batch their spend records and periodically send them
    to the central server for persistence and aggregation.
    """
    from litellm.proxy.proxy_server import prisma_client

    verbose_proxy_logger.debug(
        "Internal spend record request: %d records from proxy %s",
        len(body.spend_records),
        body.proxy_id or "unknown",
    )

    if prisma_client is None:
        verbose_proxy_logger.error(
            "Cannot record spend: database not configured on central server"
        )
        return InternalSpendRecordResponse(
            recorded=0,
            errors=[
                {
                    "error": "Database not configured on central server",
                    "error_code": "no_database",
                }
            ],
        )

    recorded = 0
    errors: List[Dict[str, Any]] = []

    for record in body.spend_records:
        try:
            await _process_spend_record(record)
            recorded += 1
        except Exception as e:
            verbose_proxy_logger.error(
                "Failed to record spend for request %s: %s",
                record.request_id,
                str(e),
            )
            errors.append(
                {
                    "request_id": record.request_id,
                    "error": str(e),
                }
            )

    verbose_proxy_logger.debug(
        "Internal spend record complete: %d recorded, %d errors",
        recorded,
        len(errors),
    )

    return InternalSpendRecordResponse(
        recorded=recorded,
        errors=errors,
    )


@router.post(
    "/spend/record/batch",
    response_model=InternalSpendRecordResponse,
    summary="Record Spend (Optimized Batch)",
    description="Record spend data using optimized batch processing",
)
async def internal_spend_record_batch(
    request: Request,
    body: InternalSpendRecordRequest,
    service_key: InternalServiceKey = Depends(verify_internal_service_key),
) -> InternalSpendRecordResponse:
    """
    Record spend data using optimized batch processing.
    
    This endpoint uses the transaction buffer for more efficient
    database writes when processing large batches of spend records.
    """
    from litellm.proxy.proxy_server import prisma_client, proxy_logging_obj

    verbose_proxy_logger.debug(
        "Internal spend record batch: %d records from proxy %s",
        len(body.spend_records),
        body.proxy_id or "unknown",
    )

    if prisma_client is None:
        return InternalSpendRecordResponse(
            recorded=0,
            errors=[
                {
                    "error": "Database not configured on central server",
                    "error_code": "no_database",
                }
            ],
        )

    recorded = 0
    errors: List[Dict[str, Any]] = []

    # Queue all records for batch processing
    for record in body.spend_records:
        try:
            # Add to the spend update queue for batch processing
            from litellm.proxy._types import Litellm_EntityType, SpendUpdateQueueItem

            # Create queue items for each entity type
            if record.token:
                proxy_logging_obj.db_spend_update_writer.spend_update_queue.add_update(
                    SpendUpdateQueueItem(
                        entity_type=Litellm_EntityType.KEY,
                        entity_id=record.token,
                        response_cost=record.response_cost,
                    )
                )

            if record.user_id:
                proxy_logging_obj.db_spend_update_writer.spend_update_queue.add_update(
                    SpendUpdateQueueItem(
                        entity_type=Litellm_EntityType.USER,
                        entity_id=record.user_id,
                        response_cost=record.response_cost,
                    )
                )

            if record.team_id:
                proxy_logging_obj.db_spend_update_writer.spend_update_queue.add_update(
                    SpendUpdateQueueItem(
                        entity_type=Litellm_EntityType.TEAM,
                        entity_id=record.team_id,
                        response_cost=record.response_cost,
                    )
                )

            if record.org_id:
                proxy_logging_obj.db_spend_update_writer.spend_update_queue.add_update(
                    SpendUpdateQueueItem(
                        entity_type=Litellm_EntityType.ORGANIZATION,
                        entity_id=record.org_id,
                        response_cost=record.response_cost,
                    )
                )

            if record.end_user_id:
                proxy_logging_obj.db_spend_update_writer.spend_update_queue.add_update(
                    SpendUpdateQueueItem(
                        entity_type=Litellm_EntityType.END_USER,
                        entity_id=record.end_user_id,
                        response_cost=record.response_cost,
                    )
                )

            recorded += 1

        except Exception as e:
            verbose_proxy_logger.error(
                "Failed to queue spend for request %s: %s",
                record.request_id,
                str(e),
            )
            errors.append(
                {
                    "request_id": record.request_id,
                    "error": str(e),
                }
            )

    verbose_proxy_logger.debug(
        "Internal spend record batch complete: %d queued, %d errors",
        recorded,
        len(errors),
    )

    return InternalSpendRecordResponse(
        recorded=recorded,
        errors=errors,
    )
