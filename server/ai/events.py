"""Normalized event envelope every analysis provider emits.

Keep this surface small and stable — the SSE layer serializes these dicts
verbatim to the frontend, so every field added here becomes part of the
contract.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict


class AnalysisEvent(TypedDict, total=False):
    type: Literal["stage", "progress", "usage", "done", "error"]
    stage: str               # human-readable label: "Starting session", "Running tool: Read"
    message: str             # sub-stage detail (optional)
    tokens_in: int
    tokens_out: int
    cost_usd: float
    cached_tokens: int
    result: dict[str, Any]   # on `done`: the parsed analysis JSON
    file_path: str           # on `done`: relative path under the video folder
    duration_ms: int
    error_message: str
