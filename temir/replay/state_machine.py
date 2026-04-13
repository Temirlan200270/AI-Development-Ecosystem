"""
Execution Replay Engine v2: детерминированный fold событий журнала в агрегированное состояние.
Поддерживает restore at seq N, сравнение состояний (diff runs), основу для branch replay.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REPLAY_ENGINE_VERSION = "2.0"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


_NO_SEQ_SORT_BASE = 10**15


def _sort_key_raw(line_index: int, envelope: Mapping[str, Any]) -> Tuple[int, int]:
    """Упорядочивание: сначала положительный seq, иначе стабильный хвост по номеру строки JSONL."""
    s = _safe_int(envelope.get("seq"), 0)
    if s > 0:
        return (s, line_index)
    return (_NO_SEQ_SORT_BASE + line_index, line_index)


def normalize_events_for_replay(
    events: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Детерминированный порядок replay и назначение _temir_replay_ord (1..n) на копиях envelope.
    """
    indexed: List[Tuple[int, Dict[str, Any]]] = []
    for i, raw in enumerate(events):
        ev = dict(raw)
        indexed.append((i, ev))
    indexed.sort(key=lambda t: _sort_key_raw(t[0], t[1]))
    out: List[Dict[str, Any]] = []
    for ord1, (_, ev) in enumerate(indexed, start=1):
        ev["_temir_replay_ord"] = ord1
        out.append(ev)
    return out


def replay_cursor(envelope: Mapping[str, Any]) -> int:
    """
    Курсор для until_seq: envelope.seq если задан, иначе _temir_replay_ord после normalize_events_for_replay.
    """
    s = _safe_int(envelope.get("seq"), 0)
    if s > 0:
        return s
    o = envelope.get("_temir_replay_ord")
    if isinstance(o, int) and o > 0:
        return o
    return 0


def strip_replay_private_fields(envelope: Mapping[str, Any]) -> Dict[str, Any]:
    """Убрать служебные поля перед записью в JSONL (branch / export)."""
    d = dict(envelope)
    d.pop("_temir_replay_ord", None)
    return d


def replay_validation_notes(
    events: Sequence[Mapping[str, Any]],
) -> Tuple[bool, List[str]]:
    """Проверка монотонности replay_cursor в отсортированном журнале."""
    notes: List[str] = []
    ordered = normalize_events_for_replay(events)
    prev_eff = -1
    regressions = 0
    for ev in ordered:
        eff = replay_cursor(ev)
        if eff < prev_eff:
            regressions += 1
        prev_eff = eff
    if regressions:
        notes.append(
            f"replay_cursor не монотонен после normalize ({regressions} регрессий) — проверьте seq в журнале.",
        )
    return (len(notes) == 0, notes)


@dataclass(frozen=True)
class TaskStateV2:
    task_id: str
    status: str
    executor: str
    last_error: Optional[str] = None


@dataclass(frozen=True)
class ReplayAggregateStateV2:
    """Снимок состояния пайплайна после применения префикса журнала."""

    replay_engine_version: str = REPLAY_ENGINE_VERSION
    applied_through_seq: int = 0
    events_applied: int = 0
    pipeline_completed: Optional[bool] = None
    pipeline_failed_phase: Optional[str] = None
    pipeline_error: Optional[str] = None
    user_request: Optional[str] = None
    output_dir: Optional[str] = None
    planned_task_ids: Tuple[str, ...] = ()
    cost_usd_total: float = 0.0
    last_decision_payload: Optional[Dict[str, Any]] = None
    last_patch_preview: Optional[str] = None
    llm_completed_calls: int = 0
    llm_failed_calls: int = 0
    branch_parent_run_id: Optional[str] = None
    branch_fork_seq: Optional[int] = None
    branch_child_run_id: Optional[str] = None
    topic_counts: Tuple[Tuple[str, int], ...] = ()
    tasks: Tuple[Tuple[str, TaskStateV2], ...] = ()

    def tasks_as_map(self) -> Dict[str, TaskStateV2]:
        return dict(self.tasks)

    def to_jsonable(self) -> Dict[str, Any]:
        """Сериализация для REST / UI."""
        return {
            "replay_engine_version": self.replay_engine_version,
            "applied_through_seq": self.applied_through_seq,
            "events_applied": self.events_applied,
            "pipeline_completed": self.pipeline_completed,
            "pipeline_failed_phase": self.pipeline_failed_phase,
            "pipeline_error": self.pipeline_error,
            "user_request": self.user_request,
            "output_dir": self.output_dir,
            "planned_task_ids": list(self.planned_task_ids),
            "cost_usd_total": self.cost_usd_total,
            "last_decision_payload": self.last_decision_payload,
            "last_patch_preview": self.last_patch_preview,
            "llm_completed_calls": self.llm_completed_calls,
            "llm_failed_calls": self.llm_failed_calls,
            "branch_parent_run_id": self.branch_parent_run_id,
            "branch_fork_seq": self.branch_fork_seq,
            "branch_child_run_id": self.branch_child_run_id,
            "topic_counts": {k: v for k, v in self.topic_counts},
            "tasks": {
                tid: {
                    "task_id": t.task_id,
                    "status": t.status,
                    "executor": t.executor,
                    "last_error": t.last_error,
                }
                for tid, t in self.tasks
            },
        }


def _bump_topic_counts(
    counts: Dict[str, int],
    topic: str,
) -> Tuple[Tuple[str, int], ...]:
    counts = dict(counts)
    counts[topic] = counts.get(topic, 0) + 1
    return tuple(sorted(counts.items()))


def _tasks_dict(state: ReplayAggregateStateV2) -> Dict[str, TaskStateV2]:
    return dict(state.tasks)


def _commit_tasks(d: Dict[str, TaskStateV2]) -> Tuple[Tuple[str, TaskStateV2], ...]:
    return tuple(sorted(d.items()))


def _patch_preview_from_payload(payload: Mapping[str, Any]) -> Optional[str]:
    if not payload:
        return None
    if isinstance(payload.get("diff"), str):
        return payload["diff"]
    summary = payload.get("summary")
    if isinstance(summary, dict) and isinstance(summary.get("diff_preview"), str):
        return summary["diff_preview"]
    return None


def fold_one_event(
    state: ReplayAggregateStateV2,
    envelope: Mapping[str, Any],
) -> ReplayAggregateStateV2:
    """Один шаг state machine (чистая функция)."""
    topic = str(envelope.get("topic") or "")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    seq = replay_cursor(envelope)
    counts_map: Dict[str, int] = {k: v for k, v in state.topic_counts}
    topic_tuple = _bump_topic_counts(counts_map, topic)

    tasks = _tasks_dict(state)
    ur = state.user_request
    od = state.output_dir
    planned = list(state.planned_task_ids)
    pc = state.pipeline_completed
    pfp = state.pipeline_failed_phase
    perr = state.pipeline_error
    cost = state.cost_usd_total
    dec = state.last_decision_payload
    patch = state.last_patch_preview
    llm_ok = state.llm_completed_calls
    llm_fail = state.llm_failed_calls
    br_par = state.branch_parent_run_id
    br_fork = state.branch_fork_seq
    br_child = state.branch_child_run_id

    if topic == "pipeline.started":
        ur = str(payload.get("user_request")) if payload.get("user_request") is not None else ur
        od = str(payload.get("output_dir")) if payload.get("output_dir") is not None else od
    elif topic == "pipeline.plan_ready":
        raw_ids = payload.get("task_ids")
        if isinstance(raw_ids, list):
            planned = [str(x) for x in raw_ids]
        elif isinstance(raw_ids, tuple):
            planned = [str(x) for x in raw_ids]
    elif topic == "pipeline.completed":
        pc = bool(payload.get("success")) if "success" in payload else pc
    elif topic == "pipeline.failed":
        pfp = str(payload.get("phase")) if payload.get("phase") is not None else pfp
        perr = str(payload.get("error")) if payload.get("error") is not None else perr
        pc = False
    elif topic == "task.created":
        tid = str(payload.get("task_id") or "")
        if tid:
            ex = str(payload.get("executor") or "")
            tasks[tid] = TaskStateV2(task_id=tid, status="pending", executor=ex)
    elif topic == "task.started":
        tid = str(payload.get("task_id") or "")
        if tid in tasks:
            t = tasks[tid]
            tasks[tid] = TaskStateV2(
                task_id=tid,
                status="running",
                executor=str(payload.get("executor") or t.executor),
                last_error=t.last_error,
            )
        elif tid:
            tasks[tid] = TaskStateV2(
                task_id=tid,
                status="running",
                executor=str(payload.get("executor") or ""),
            )
    elif topic in ("task.completed",):
        tid = str(payload.get("task_id") or "")
        if tid:
            prev = tasks.get(tid)
            ex = str(payload.get("executor") or (prev.executor if prev else ""))
            tasks[tid] = TaskStateV2(task_id=tid, status="completed", executor=ex, last_error=None)
    elif topic == "task.failed":
        tid = str(payload.get("task_id") or "")
        if tid:
            prev = tasks.get(tid)
            ex = str(payload.get("executor") or (prev.executor if prev else ""))
            err = str(payload.get("error") or "")
            tasks[tid] = TaskStateV2(task_id=tid, status="failed", executor=ex, last_error=err)
    elif topic == "task.skipped":
        tid = str(payload.get("task_id") or "")
        if tid:
            prev = tasks.get(tid)
            ex = str(prev.executor if prev else "")
            tasks[tid] = TaskStateV2(task_id=tid, status="skipped", executor=ex)
    elif topic == "cost.tick":
        if "usd_total" in payload and isinstance(payload.get("usd_total"), (int, float)):
            cost = float(payload["usd_total"])
        elif "usd_delta" in payload and isinstance(payload.get("usd_delta"), (int, float)):
            cost = cost + float(payload["usd_delta"])
    elif topic in ("decision.selected", "decision.alternatives"):
        dec = copy.deepcopy(payload)
    elif topic == "patch.proposed":
        pv = _patch_preview_from_payload(payload)
        if pv is not None:
            patch = pv
    elif topic == "llm.completed":
        ok = bool(payload.get("success"))
        if ok:
            llm_ok += 1
        else:
            llm_fail += 1
    elif topic == "replay.branch_created":
        if payload.get("parent_run_id") is not None:
            br_par = str(payload.get("parent_run_id") or "")
        if payload.get("fork_seq") is not None:
            br_fork = _safe_int(payload.get("fork_seq"), 0)
        if payload.get("child_run_id") is not None:
            br_child = str(payload.get("child_run_id") or "")

    return ReplayAggregateStateV2(
        replay_engine_version=REPLAY_ENGINE_VERSION,
        applied_through_seq=max(state.applied_through_seq, seq),
        events_applied=state.events_applied + 1,
        pipeline_completed=pc,
        pipeline_failed_phase=pfp,
        pipeline_error=perr,
        user_request=ur,
        output_dir=od,
        planned_task_ids=tuple(planned),
        cost_usd_total=cost,
        last_decision_payload=dec,
        last_patch_preview=patch,
        llm_completed_calls=llm_ok,
        llm_failed_calls=llm_fail,
        branch_parent_run_id=br_par or None,
        branch_fork_seq=br_fork,
        branch_child_run_id=br_child or None,
        topic_counts=topic_tuple,
        tasks=_commit_tasks(tasks),
    )


def fold_events_to_state(
    events: Sequence[Mapping[str, Any]],
    *,
    until_seq: Optional[int] = None,
    max_events: Optional[int] = None,
    raw_end_inclusive: Optional[int] = None,
) -> ReplayAggregateStateV2:
    """
    Свернуть журнал в состояние.
    until_seq: включительно по replay_cursor (см. replay_cursor) после normalize.
    raw_end_inclusive: взять только events[0..raw_end_inclusive] до normalize (как префикс JSONL для UI).
    max_events: ограничить числом первых событий после нормализации (отладка).
    """
    ev_list: List[Mapping[str, Any]] = list(events)
    if raw_end_inclusive is not None:
        if raw_end_inclusive < 0:
            ev_list = []
        else:
            ev_list = ev_list[: raw_end_inclusive + 1]
    ordered = normalize_events_for_replay(ev_list)
    state = ReplayAggregateStateV2()
    applied = 0
    for env in ordered:
        if max_events is not None and applied >= max_events:
            break
        cur = replay_cursor(env)
        if until_seq is not None and cur > until_seq:
            break
        state = fold_one_event(state, env)
        applied += 1
    return state


def diff_aggregate_states(
    a: ReplayAggregateStateV2,
    b: ReplayAggregateStateV2,
) -> Dict[str, Any]:
    """Структурное сравнение двух снимков (для diff runs)."""
    ja = a.to_jsonable()
    jb = b.to_jsonable()
    keys = (
        "pipeline_completed",
        "pipeline_failed_phase",
        "cost_usd_total",
        "events_applied",
        "applied_through_seq",
        "llm_completed_calls",
        "llm_failed_calls",
        "user_request",
        "output_dir",
        "branch_parent_run_id",
        "branch_fork_seq",
        "branch_child_run_id",
    )
    field_diffs: Dict[str, Any] = {}
    for k in keys:
        if ja.get(k) != jb.get(k):
            field_diffs[k] = {"left": ja.get(k), "right": jb.get(k)}
    tasks_a = ja.get("tasks") or {}
    tasks_b = jb.get("tasks") or {}
    all_ids = sorted(set(tasks_a.keys()) | set(tasks_b.keys()))
    task_diffs: List[Dict[str, Any]] = []
    for tid in all_ids:
        if tasks_a.get(tid) != tasks_b.get(tid):
            task_diffs.append(
                {
                    "task_id": tid,
                    "left": tasks_a.get(tid),
                    "right": tasks_b.get(tid),
                },
            )
    tc_a = ja.get("topic_counts") or {}
    tc_b = jb.get("topic_counts") or {}
    topics = sorted(set(tc_a.keys()) | set(tc_b.keys()))
    topic_diffs: Dict[str, Any] = {}
    for t in topics:
        ca = tc_a.get(t, 0)
        cb = tc_b.get(t, 0)
        if ca != cb:
            topic_diffs[t] = {"left": ca, "right": cb}
    return {
        "replay_engine_version": REPLAY_ENGINE_VERSION,
        "fields": field_diffs,
        "tasks": task_diffs,
        "topic_counts": topic_diffs,
    }
