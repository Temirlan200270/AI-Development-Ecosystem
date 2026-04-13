"""Deterministic step intent hashing."""
from temir.core.step_audit import compute_plan_intent_sha256, compute_step_intent_sha256


def test_step_hash_stable() -> None:
    _, h1 = compute_step_intent_sha256(
        task_id="t",
        step_id="t:step:0",
        step_seq=0,
        action="write_file",
        args={"path": "a.txt", "content": "x"},
        level_index=0,
        capabilities=["fs.write"],
    )
    _, h2 = compute_step_intent_sha256(
        task_id="t",
        step_id="t:step:0",
        step_seq=0,
        action="write_file",
        args={"content": "x", "path": "a.txt"},
        level_index=0,
        capabilities=["fs.write"],
    )
    assert h1 == h2


def test_different_args_different_hash() -> None:
    _, h1 = compute_step_intent_sha256(
        task_id="t",
        step_id="t:step:0",
        step_seq=0,
        action="write_file",
        args={"path": "a", "content": "1"},
        level_index=0,
        capabilities=["fs.write"],
    )
    _, h2 = compute_step_intent_sha256(
        task_id="t",
        step_id="t:step:0",
        step_seq=0,
        action="write_file",
        args={"path": "a", "content": "2"},
        level_index=0,
        capabilities=["fs.write"],
    )
    assert h1 != h2


def test_plan_hash() -> None:
    r1, _ = compute_step_intent_sha256(
        task_id="j",
        step_id="j:step:0",
        step_seq=0,
        action="write_file",
        args={"path": "a", "content": ""},
        level_index=0,
        capabilities=["fs.write"],
    )
    h = compute_plan_intent_sha256(
        task_id="j",
        execution_mode="dag",
        step_records=[r1],
    )
    assert len(h) == 64
