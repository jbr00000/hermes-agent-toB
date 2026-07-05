"""Tests for the declarative memory-provider registry."""

from hermes_cli.memory_providers import (
    KIND_SECRET,
    ProviderField,
    ProviderFieldOption,
    get_memory_provider,
)


def test_no_bundled_third_party_provider_is_declared():
    assert get_memory_provider("hindsight") is None
    assert get_memory_provider("custom_memory") is None
    assert get_memory_provider("builtin") is None


def test_provider_field_secret_flag_and_select_values():
    secret = ProviderField(key="api_key", label="API key", kind=KIND_SECRET)
    select = ProviderField(
        key="mode",
        label="Mode",
        options=(
            ProviderFieldOption("cloud", "Cloud"),
            ProviderFieldOption("local", "Local"),
        ),
    )

    assert secret.is_secret is True
    assert select.allowed_values() == {"cloud", "local"}
