"""Declarative configuration schema for desktop memory providers.

Bundled third-party memory providers have been removed from the to-B server
build. The registry remains as an extension point for future providers, but it
must not expose stale config panels for integrations that are no longer shipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field

KIND_TEXT = "text"
KIND_SELECT = "select"
KIND_SECRET = "secret"


@dataclass(frozen=True)
class ProviderFieldOption:
    """A single choice for a select field."""

    value: str
    label: str
    description: str = ""


@dataclass(frozen=True)
class ProviderField:
    """One configurable field on a memory provider."""

    key: str
    label: str
    kind: str = KIND_TEXT
    default: str = ""
    description: str = ""
    placeholder: str = ""
    options: tuple[ProviderFieldOption, ...] = ()
    env_key: str | None = None
    aliases: tuple[str, ...] = ()
    env_fallbacks: tuple[str, ...] = ()

    @property
    def is_secret(self) -> bool:
        return self.kind == KIND_SECRET

    def allowed_values(self) -> set[str]:
        return {opt.value for opt in self.options}


@dataclass(frozen=True)
class MemoryProvider:
    """A declared memory provider and its configurable fields."""

    name: str
    label: str
    fields: tuple[ProviderField, ...] = dataclass_field(default_factory=tuple)


MEMORY_PROVIDERS: dict[str, MemoryProvider] = {}


def get_memory_provider(name: str) -> MemoryProvider | None:
    """Return the declared provider for name, or None if undeclared."""

    return MEMORY_PROVIDERS.get(name)
