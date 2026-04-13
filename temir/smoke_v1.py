"""
Full System Smoke Test v1: валидация журнала, seq, артефактов cli_tool/, опционально pytest.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Минимальный набор для «замкнутого» runtime (patch.applied = успешный apply; patch.apply.success не используется).
REQUIRED_TOPICS_V1: Tuple[str, ...] = (
    "llm.requested",
    "llm.completed",
    "task.started",
    "task.completed",
    "patch.proposed",
    "patch.applied",
    "cost.tick",
)

RECOMMENDED_TOPICS_V1: Tuple[str, ...] = (
    "decision.strategy.selected",
    "tool.execution.started",
    "evaluation.test.run",
    "pipeline.started",
    "pipeline.plan_ready",
    "task.created",
)

CLI_TOOL_RELATIVE_PATHS: Tuple[str, ...] = (
    "cli_tool/main.py",
    "cli_tool/utils.py",
    "cli_tool/tests/test_cli.py",
)


def smoke_prompt_file() -> Path:
    return Path(__file__).resolve().parent / "prompts" / "full_system_smoke_v1.txt"


def read_smoke_prompt() -> str:
    path = smoke_prompt_file()
    if not path.is_file():
        raise FileNotFoundError(f"smoke prompt not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def load_events_jsonl(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def _parse_seq(ev: Dict[str, Any]) -> Optional[int]:
    s = ev.get("seq")
    if isinstance(s, int):
        return s
    if isinstance(s, str) and s.isdigit():
        return int(s)
    return None


@dataclass
class SmokeValidationReport:
    passed: bool
    run_id: Optional[str] = None
    journal_path: Optional[str] = None
    event_count: int = 0
    topics_present: Dict[str, int] = field(default_factory=dict)
    missing_required: Tuple[str, ...] = ()
    missing_recommended: Tuple[str, ...] = ()
    seq_issues: Tuple[str, ...] = ()
    run_id_issues: Tuple[str, ...] = ()
    artifact_issues: Tuple[str, ...] = ()
    pytest_note: Optional[str] = None
    replay_note: Optional[str] = None

    def messages(self) -> List[str]:
        lines: List[str] = []
        if self.run_id:
            lines.append(f"run_id: {self.run_id}")
        if self.journal_path:
            lines.append(f"journal: {self.journal_path}")
        lines.append(f"events: {self.event_count}")
        lines.append(f"topics: {dict(sorted(self.topics_present.items()))}")
        if self.missing_required:
            lines.append("MISSING required topics: " + ", ".join(self.missing_required))
        if self.missing_recommended:
            lines.append("missing recommended: " + ", ".join(self.missing_recommended))
        if self.seq_issues:
            lines.append("seq: " + "; ".join(self.seq_issues))
        if self.run_id_issues:
            lines.append("envelope run_id: " + "; ".join(self.run_id_issues))
        if self.artifact_issues:
            lines.append("artifacts: " + "; ".join(self.artifact_issues))
        if self.pytest_note:
            lines.append(f"pytest: {self.pytest_note}")
        if self.replay_note:
            lines.append(f"replay: {self.replay_note}")
        lines.append("RESULT: PASS" if self.passed else "RESULT: FAIL")
        return lines


def validate_journal_events(
    events: Sequence[Dict[str, Any]],
    *,
    expect_run_id: Optional[str] = None,
) -> Tuple[Dict[str, int], Tuple[str, ...], Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]:
    topics: Dict[str, int] = {}
    for ev in events:
        t = str(ev.get("topic") or "")
        if t:
            topics[t] = topics.get(t, 0) + 1
    missing_req = tuple(t for t in REQUIRED_TOPICS_V1 if topics.get(t, 0) == 0)
    missing_rec = tuple(t for t in RECOMMENDED_TOPICS_V1 if topics.get(t, 0) == 0)

    seqs: List[int] = []
    for ev in events:
        p = _parse_seq(ev)
        if p is not None and p > 0:
            seqs.append(p)
    seq_issues: List[str] = []
    if len(seqs) >= 2:
        for i in range(1, len(seqs)):
            if seqs[i] < seqs[i - 1]:
                seq_issues.append(f"seq decreases at index {i}: {seqs[i - 1]} -> {seqs[i]}")
            elif seqs[i] == seqs[i - 1]:
                seq_issues.append(f"duplicate seq at step {i}: {seqs[i]}")

    run_issues: List[str] = []
    for i, ev in enumerate(events):
        rid = ev.get("run_id")
        if rid is None or (isinstance(rid, str) and not rid.strip()):
            run_issues.append(f"line {i + 1}: missing run_id")
            if len(run_issues) >= 5:
                break
            continue
        if expect_run_id and str(rid) != str(expect_run_id):
            run_issues.append(f"line {i + 1}: run_id {rid!r} != {expect_run_id!r}")
            if len(run_issues) >= 5:
                break

    return topics, tuple(seq_issues), tuple(run_issues), missing_req, missing_rec


def validate_cli_tool_tree(output_dir: Path) -> Tuple[str, ...]:
    root = output_dir.resolve()
    issues: List[str] = []
    for rel in CLI_TOOL_RELATIVE_PATHS:
        p = root / rel
        if not p.is_file():
            issues.append(f"missing file: {rel}")
    return tuple(issues)


def run_smoke_pytest(output_dir: Path, timeout: int = 120) -> str:
    tests_dir = output_dir.resolve() / "cli_tool" / "tests"
    if not tests_dir.is_dir():
        return "skipped (no cli_tool/tests)"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(tests_dir), "-q", "--tb=no"],
            cwd=str(output_dir.resolve()),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "timeout"
    if proc.returncode == 0:
        return "exit 0"
    tail = (proc.stdout or "")[-400:] + (proc.stderr or "")[-400:]
    return f"exit {proc.returncode} {tail.strip()[:200]}"


def replay_midpoint_note(events: List[Dict[str, Any]]) -> str:
    if not events:
        return "no events"
    try:
        from temir.replay.state_machine import fold_events_to_state
    except ImportError as e:
        return f"replay import failed: {e}"
    mid = len(events) // 2
    st = fold_events_to_state(events, raw_end_inclusive=max(0, mid - 1))
    j = st.to_jsonable()
    return (
        f"raw index ~{mid}, state events_applied={j.get('events_applied')}, "
        f"cost={j.get('cost_usd_total')}, tasks={len(j.get('tasks') or {})}"
    )


def build_report(
    events: List[Dict[str, Any]],
    *,
    run_id: Optional[str],
    journal_path: Optional[Path],
    output_dir: Path,
    run_pytest: bool,
    strict_recommended: bool = False,
) -> SmokeValidationReport:
    topics, seq_iss, run_iss, miss_req, miss_rec = validate_journal_events(
        events,
        expect_run_id=run_id,
    )
    art_iss = validate_cli_tool_tree(output_dir)
    pytest_note: Optional[str] = None
    if run_pytest:
        pytest_note = run_smoke_pytest(output_dir)
    replay_note = replay_midpoint_note(events)

    passed = (
        len(miss_req) == 0
        and len(seq_iss) == 0
        and len(run_iss) == 0
        and len(art_iss) == 0
        and (not strict_recommended or len(miss_rec) == 0)
    )
    return SmokeValidationReport(
        passed=passed,
        run_id=run_id,
        journal_path=str(journal_path) if journal_path else None,
        event_count=len(events),
        topics_present=topics,
        missing_required=miss_req,
        missing_recommended=miss_rec,
        seq_issues=seq_iss,
        run_id_issues=run_iss,
        artifact_issues=art_iss,
        pytest_note=pytest_note,
        replay_note=replay_note,
    )
