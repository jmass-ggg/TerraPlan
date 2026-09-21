"""
Response handling and data-mode serialization for FarmTwin backend.

This module implements honest data-mode headers and ensures runtime configuration
is not treated as record provenance.

Requirements: 12.2, 12.3, 12.4
"""

from uuid import UUID

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings


class DataModeHeaderMiddleware:
    """
    Middleware that adds operational mode headers to all responses.
    
    Headers identify runtime context without treating configuration as
    record provenance. Source-level modes in evidence metadata are preserved.
    
    Requirements: 12.2
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process request and add mode headers to response"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            """Add headers to response start messages"""
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)

                # Add operational mode headers (Req 12.2)
                headers.append(
                    "X-FarmTwin-Data-Mode", self.settings.data_mode.value
                )
                headers.append(
                    "X-FarmTwin-Auth-Mode", self.settings.auth.mode.value
                )

            await send(message)

        await self.app(scope, receive, send_with_headers)


def get_operational_mode_headers(settings: Settings) -> dict[str, str]:
    """
    Get operational mode headers for including in responses.
    
    These identify runtime operational context, not per-record provenance.
    
    Requirements: 12.2
    """
    return {
        "X-FarmTwin-Data-Mode": settings.data_mode.value,
        "X-FarmTwin-Auth-Mode": settings.auth.mode.value,
    }


def is_non_live_mode(settings: Settings) -> bool:
    """
    Determine if current mode is non-live (historical or demo).
    
    Requirements: 12.2
    """
    from app.core.config import DataMode

    return settings.data_mode in (
        DataMode.HISTORICAL_REPLAY,
        DataMode.DEMONSTRATION,
    )


class HonestEvidenceSerializer:
    """
    Utility for serializing evidence with honest data modes.
    
    Preserves source-level modes when evidence is mixed and does not
    overwrite them with runtime defaults.
    
    Requirements: 12.3, 12.4
    """

    @staticmethod
    def preserve_source_mode(
        evidence_records: list[dict],
        runtime_mode: str,
    ) -> list[dict]:
        """
        Ensure source-level data modes are preserved in evidence records.
        
        Does NOT overwrite existing source-level modes with runtime defaults.
        Only adds runtime mode if no source-level mode exists.
        
        Args:
            evidence_records: List of evidence dictionaries with optional
                             'data_mode' field
            runtime_mode: Runtime operational data mode (for context only)
        
        Returns:
            Evidence records with preserved source modes
            
        Requirements: 12.3
        """
        result = []
        for record in evidence_records:
            # Create a copy to avoid mutating input
            record_copy = record.copy()

            # ONLY add runtime mode if record has no source-level mode
            # This prevents overwriting historical/demo records with live mode
            if "data_mode" not in record_copy:
                # Record came from a system without mode tracking
                # Use runtime as default only in this case
                record_copy["data_mode"] = runtime_mode

            result.append(record_copy)

        return result

    @staticmethod
    def create_evidence_with_mode(
        source_id: str,
        valid_time: str,
        value: float | None,
        data_mode: str,
        quality: str | None = None,
        missing_reason: str | None = None,
    ) -> dict:
        """
        Create an evidence record with explicit source-level data mode.
        
        The data_mode parameter is the SOURCE mode, not the runtime mode.
        Historical fixtures retain their historical mode even when served
        in different runtime configurations.
        
        Requirements: 12.3, 12.4
        """
        record = {
            "source_id": source_id,
            "valid_time": valid_time,
            "data_mode": data_mode,  # Source mode, not runtime mode
            "value": value,
        }

        if quality is not None:
            record["quality"] = quality

        if missing_reason is not None:
            record["missing_reason"] = missing_reason

        return record


class DecisionMetadataBuilder:
    """
    Builder for decision reference metadata.
    
    Requirements: 12.4
    """

    @staticmethod
    def create_decision_reference(
        snapshot_id: UUID | None = None,
        geometry_revision: int | None = None,
        engine_version: str | None = None,
    ) -> dict:
        """
        Create decision reference metadata.
        
        Preserves unknowns as null with reasons rather than fabricating values.
        
        Requirements: 12.4
        """
        return {
            "snapshot_id": str(snapshot_id) if snapshot_id else None,
            "geometry_revision": geometry_revision,
            "engine_version": engine_version,
        }
