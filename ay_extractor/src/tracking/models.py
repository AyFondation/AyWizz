# src/tracking/models.py — v1
"""Tracking domain models: LLMCallRecord, AgentStats, SessionStats, GlobalStats, etc.

See spec §20 for full documentation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class LLMCallRecord(BaseModel):
    """Individual LLM API call log entry."""

    call_id: str
    timestamp: datetime
    agent: str
    step: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int
    status: Literal["success", "retry", "failed"]
    retry_count: int = 0
    estimated_cost_usd: float = 0.0


class AgentStats(BaseModel):
    """Per-agent aggregated stats for a single execution."""

    agent: str
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0
    avg_latency_ms: float
    max_latency_ms: int
    retry_count: int = 0
    failure_count: int = 0
    estimated_cost_usd: float = 0.0
    budget_usage_pct: float = 0.0


class SessionStats(BaseModel):
    """Consolidated view of a full document execution."""

    document_id: str
    session_id: str
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    document_size_chars: int
    document_size_tokens_est: int
    total_llm_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0
    estimated_cost_usd: float = 0.0
    cost_per_1k_chars: float = 0.0
    agents: dict[str, AgentStats] = {}
    budget_total_allocated: int = 0
    budget_total_consumed: int = 0
    budget_usage_pct: float = 0.0
    steps_degraded: list[str] = []
    steps_failed: list[str] = []


class TypeStats(BaseModel):
    """Per document-type aggregated stats."""

    count: int
    avg_tokens: float
    avg_cost_usd: float
    avg_duration_seconds: float
    avg_chunks: float


class CumulativeAgentStats(BaseModel):
    """Per-agent cumulative stats across all documents."""

    total_calls: int
    total_tokens: int
    avg_tokens_per_call: float
    failure_rate: float
    avg_latency_ms: float
    pct_of_total_cost: float


class ModelStats(BaseModel):
    """Per-LLM-model aggregated stats."""

    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float


class DailyStats(BaseModel):
    """Daily consumption entry for cost trend tracking."""

    date: str
    documents_processed: int
    total_tokens: int
    total_cost_usd: float


class GlobalStats(BaseModel):
    """Cross-document cumulative statistics."""

    total_documents_processed: int
    total_tokens_consumed: int
    total_estimated_cost_usd: float
    avg_tokens_per_document: float
    avg_cost_per_document: float
    avg_duration_per_document: float
    by_document_type: dict[str, TypeStats] = {}
    by_agent: dict[str, CumulativeAgentStats] = {}
    by_model: dict[str, ModelStats] = {}
    cost_trend: list[DailyStats] = []
    last_updated: datetime


class ModelPricing(BaseModel):
    """LLM model pricing configuration."""

    model: str
    input_price_per_1m: float
    output_price_per_1m: float
    cache_read_per_1m: float = 0.0
    cache_write_per_1m: float = 0.0
