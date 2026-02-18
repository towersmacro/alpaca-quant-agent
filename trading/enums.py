"""
Trading enums for QuantAgent.
"""

from enum import Enum


class TradeStatus(Enum):
    """Trade status enumeration."""
    OPEN = "OPEN"
    CLOSE = "CLOSED"
