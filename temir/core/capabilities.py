"""
IR v4 — capability-based authorization: each tool action declares required capability tokens.

Policy is separate from the tool registry (what exists) vs what the run is allowed to use.
Env: TEMIR_CAPABILITY_ALLOWLIST=fs.write,fs.read,git,shell.limited (comma-separated).
Empty / unset → all known capabilities allowed (backward compatible).
"""
from __future__ import annotations

import os
from typing import Any, FrozenSet, Iterable

# action_name -> required capability tokens (subset must be granted)
ACTION_REQUIRED_CAPABILITIES: dict[str, frozenset[str]] = {
    "write_file": frozenset({"fs.write"}),
    "append_file": frozenset({"fs.write"}),
    "create_directory": frozenset({"fs.write"}),
    "read_file": frozenset({"fs.read"}),
    "list_directory": frozenset({"fs.read"}),
    "file_exists": frozenset({"fs.read"}),
    "directory_exists": frozenset({"fs.read"}),
    "smart_patch": frozenset({"fs.read", "fs.write"}),
    "remove_path": frozenset({"fs.delete"}),
    "copy_path": frozenset({"fs.read", "fs.write"}),
    "execute_shell": frozenset({"shell.limited"}),
    "run_tests": frozenset({"proc.run"}),
    "run_linter": frozenset({"proc.run"}),
    "install_package": frozenset({"pkg.install"}),
    "get_system_info": frozenset({"sys.info"}),
    "git_init": frozenset({"git"}),
    "git_add": frozenset({"git"}),
    "git_commit": frozenset({"git"}),
    "git_status": frozenset({"git"}),
    "git_diff": frozenset({"git"}),
}

ALL_KNOWN_CAPABILITIES: FrozenSet[str] = frozenset().union(*ACTION_REQUIRED_CAPABILITIES.values())


class CapabilityDeniedError(PermissionError):
    """Missing capability mapping or policy denies required capability."""

    def __init__(
        self,
        message: str,
        *,
        action: str,
        missing: frozenset[str],
        code: str = "deny",
    ) -> None:
        self.action = action
        self.missing = missing
        self.code = code
        super().__init__(message)


def capabilities_required_for_action(action: str) -> frozenset[str]:
    caps = ACTION_REQUIRED_CAPABILITIES.get(action)
    if caps is None:
        raise CapabilityDeniedError(
            f"No IR v4 capability mapping for action {action!r} "
            f"(extend temir.core.capabilities.ACTION_REQUIRED_CAPABILITIES).",
            action=action,
            missing=frozenset(),
            code="unmapped",
        )
    return caps


def parse_allowlist(raw: str) -> FrozenSet[str]:
    parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    return frozenset(p for p in parts if p)


def resolve_allowed_capabilities(
    *,
    config: dict[str, Any] | None,
) -> FrozenSet[str]:
    """
    config keys:
 - allowed_capabilities: list[str] — highest priority if set
    - capability_restrict: if True and no list/env, allow none (empty set)
    """
    if config:
        ac = config.get("allowed_capabilities")
        if isinstance(ac, (list, tuple, set)) and len(ac) > 0:
            return frozenset(str(x) for x in ac)
    env = (os.environ.get("TEMIR_CAPABILITY_ALLOWLIST") or "").strip()
    if env:
        return parse_allowlist(env)
    if config and config.get("capability_restrict") is True:
        return frozenset()
    return ALL_KNOWN_CAPABILITIES


def authorize_plan_steps(
    steps: Iterable[Any],
    allowed: FrozenSet[str],
) -> None:
    """Raise CapabilityDeniedError if any step's action needs a missing capability."""
    for s in steps:
        action = str(getattr(s, "action", ""))
        need = capabilities_required_for_action(action)
        if need == frozenset({"dangerous.unmapped"}):
            raise CapabilityDeniedError(
                f"Action {action!r} has no capability mapping (IR v4). Refuse by default.",
                action=action,
                missing=need,
            )
        denied = need - allowed
        if denied:
            raise CapabilityDeniedError(
                f"Action {action!r} requires capabilities {sorted(need)}; "
                f"denied (missing {sorted(denied)}). Allow via config "
                f"'allowed_capabilities' or TEMIR_CAPABILITY_ALLOWLIST.",
                action=action,
                missing=denied,
            )
