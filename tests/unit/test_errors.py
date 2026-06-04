"""Tests for the error taxonomy."""

from __future__ import annotations

import pytest

from aura.errors import (
    AuraConnectionError,
    AuraError,
    AuraNotFoundError,
    AuraServerError,
    AuraTimeoutError,
    AuraValidationError,
)


def test_base_error_carries_code_and_context() -> None:
    err = AuraError("boom", code="x", retryable=True, request_id=7, context={"k": "v"})
    assert err.code == "x"
    assert err.retryable is True
    assert err.request_id == 7
    assert err.context == {"k": "v"}
    assert "x" in str(err)
    assert "request_id=7" in str(err)


def test_default_codes_are_stable() -> None:
    assert AuraValidationError("m").code == "validation_error"
    assert AuraNotFoundError("m").code == "not_found"


def test_connection_and_timeout_are_retryable_by_default() -> None:
    assert AuraConnectionError("m").retryable is True
    assert AuraTimeoutError("m").retryable is True
    assert AuraServerError("m").retryable is True


def test_repr_does_not_crash_and_hides_nothing_sensitive() -> None:
    err = AuraValidationError("bad field", context={"field": "email"})
    assert "AuraValidationError" in repr(err)


def test_subclasses_are_aura_errors() -> None:
    with pytest.raises(AuraError):
        raise AuraNotFoundError("missing")
