"""IR v4 capability policy."""
import pytest

from temir.core.capabilities import (
    CapabilityDeniedError,
    authorize_plan_steps,
    capabilities_required_for_action,
    resolve_allowed_capabilities,
)


class _S:
    def __init__(self, action: str) -> None:
        self.action = action


def test_write_requires_fs_write() -> None:
    assert "fs.write" in capabilities_required_for_action("write_file")


def test_authorize_denies_missing_cap() -> None:
    allowed = frozenset({"fs.read"})
    with pytest.raises(CapabilityDeniedError) as e:
        authorize_plan_steps([_S("write_file")], allowed)
    assert e.value.code == "deny"


def test_resolve_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEMIR_CAPABILITY_ALLOWLIST", "fs.read,git")
    caps = resolve_allowed_capabilities(config=None)
    assert caps == frozenset({"fs.read", "git"})


def test_unmapped_action() -> None:
    with pytest.raises(CapabilityDeniedError) as e:
        capabilities_required_for_action("nonexistent_tool_xyz")
    assert e.value.code == "unmapped"
