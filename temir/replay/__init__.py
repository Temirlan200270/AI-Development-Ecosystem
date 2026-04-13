"""Replay Engine v2: state machine поверх JSONL journal."""

from __future__ import annotations

from temir.replay.state_machine import (
    REPLAY_ENGINE_VERSION,
    ReplayAggregateStateV2,
    TaskStateV2,
    diff_aggregate_states,
    fold_events_to_state,
    normalize_events_for_replay,
    replay_cursor,
    replay_validation_notes,
    strip_replay_private_fields,
)

__all__ = [
    "REPLAY_ENGINE_VERSION",
    "ReplayAggregateStateV2",
    "TaskStateV2",
    "diff_aggregate_states",
    "fold_events_to_state",
    "normalize_events_for_replay",
    "replay_cursor",
    "replay_validation_notes",
    "strip_replay_private_fields",
]
