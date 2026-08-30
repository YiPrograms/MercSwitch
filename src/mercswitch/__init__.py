"""Management toolkit for web-managed MERCURY/TP-Link/FAST switches."""

from .client import MercSwitchClient
from .models import (
    CandidateConfig,
    ChangePlan,
    DeviceCapabilities,
    DeviceIdentity,
    OperationResult,
    SwitchState,
)

__all__ = [
    "CandidateConfig",
    "ChangePlan",
    "DeviceCapabilities",
    "DeviceIdentity",
    "MercSwitchClient",
    "OperationResult",
    "SwitchState",
]

__version__ = "0.1.0"
