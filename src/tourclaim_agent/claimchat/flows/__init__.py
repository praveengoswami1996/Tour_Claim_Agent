"""The ported chat components. Each module registers its flow(s) with runtime.flow.register_flow when imported."""

from __future__ import annotations

import importlib
import pkgutil


def load_flows() -> None:
    """Import every flow module in this package, so their flows are registered (called at startup)."""
    for module in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{module.name}")
