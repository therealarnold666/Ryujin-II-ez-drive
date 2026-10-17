from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class SensorProvider:
    """One independently replaceable source of normalized metric fields."""

    name: str
    description: str
    reader: Callable[[], dict[str, Any]]
    enabled: bool = True

    def read(self) -> tuple[dict[str, Any], str | None]:
        if not self.enabled:
            return {}, None
        try:
            return self.reader(), None
        except Exception as exc:
            # Optional hardware sources must not take down LCD rendering.
            return {}, str(exc)


def apply_providers(target: Any, providers: list[SensorProvider]) -> dict[str, str]:
    """Apply providers in priority order; later non-null values win."""

    status: dict[str, str] = {}
    fields = getattr(target, "__dataclass_fields__", {})
    for provider in providers:
        if not provider.enabled:
            continue
        values, error = provider.read()
        for key, value in values.items():
            if key in fields and key != "sources" and value is not None:
                setattr(target, key, value)
        status[f"provider:{provider.name}"] = (
            f"{provider.description}; error={error}" if error else provider.description
        )
    return status
