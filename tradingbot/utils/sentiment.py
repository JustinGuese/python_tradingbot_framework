"""
Sentiment adapters (e.g. Fear & Greed index) behind a stable utils API.
"""

from __future__ import annotations

import logging
from typing import Optional

import fear_and_greed

logger = logging.getLogger(__name__)


def get_fear_greed_index() -> Optional[int]:
    """
    Return the current Fear & Greed index as an integer, or None on failure.

    This centralizes access to the external fear_and_greed package so that
    higher-level modules (bots, regime logic) only deal with plain ints.
    """
    try:
        value = fear_and_greed.get().value
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"Failed to fetch Fear & Greed index: {exc}")
        return None

    try:
        # Some implementations may return float; normalize to int for consistency
        return int(value)
    except (TypeError, ValueError):
        return None

