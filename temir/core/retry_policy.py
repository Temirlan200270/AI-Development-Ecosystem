"""
Политика retry для Level 2: какие коды контракта можно чинить через repair loop.
"""

from __future__ import annotations

# IRV3ContractError.code
_RETRYABLE_IR_CODES: frozenset[str] = frozenset(
    {"platform_mismatch", "schema", "normalize"},
)
_TERMINAL_IR_CODES: frozenset[str] = frozenset(
    {"unknown_action", "capability_denied", "graph", "batch_limit"},
)


def ir_contract_error_retryable(code: str) -> bool:
    if code in _TERMINAL_IR_CODES:
        return False
    if code in _RETRYABLE_IR_CODES:
        return True
    # неизвестный код: консервативно разрешаем один repair-цикл
    return True


# ActionPreflightViolation.code
_RETRYABLE_PREFLIGHT: frozenset[str] = frozenset(
    {"platform_mismatch", "schema"},
)
_TERMINAL_PREFLIGHT: frozenset[str] = frozenset(
    {
        "unknown_action",
        "capability_denied",
        "graph",
        "blocked_command",
        "blocked_path",
    },
)


def preflight_violation_retryable(code: str) -> bool:
    if code in _TERMINAL_PREFLIGHT:
        return False
    if code in _RETRYABLE_PREFLIGHT:
        return True
    # invalid_args и прочие: допускаем repair loop
    return True
