"""
Пост-проверка уровня DAG: все шаги учтены, нет дубликата intent при успехах.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


class LevelCompletionError(RuntimeError):
    """Уровень завершился с нарушением инвариантов."""


def validate_level_completion(
    level_steps: Sequence[Any],
    records: Sequence[Mapping[str, Any]],
    *,
    idempotency_enabled: bool,
) -> None:
    """
    records: по одному dict на шаг уровня с ключами step_id, intent_sha256, completed (bool), skipped_idempotent (bool).
    """
    expected = {getattr(s, "id", str(s)) for s in level_steps}
    seen: set[str] = set()
    intents_ok: list[str] = []
    for rec in records:
        sid = str(rec.get("step_id") or "")
        seen.add(sid)
        if rec.get("completed") is not True:
            raise LevelCompletionError(
                f"step {sid!r} is not in a closed state (completed=True required)",
            )
        h = rec.get("intent_sha256")
        if isinstance(h, str) and h:
            intents_ok.append(h)
    if expected != seen:
        raise LevelCompletionError(
            f"level step set mismatch: expected={sorted(expected)} seen={sorted(seen)}",
        )
    if idempotency_enabled and len(intents_ok) != len(set(intents_ok)):
        raise LevelCompletionError(
            f"duplicate successful intent_sha256 within level: {intents_ok!r}",
        )
