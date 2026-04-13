"""FastAPI-приложение: статика Debug Panel + WebSocket /api."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from temir.env_bootstrap import load_dotenv_if_available

load_dotenv_if_available()

from pydantic import BaseModel, Field

from temir.storage.event_journal import get_journal_base
from temir.storage.run_store import (
    branch_run_journal,
    list_run_ids,
    load_run_events,
)
from temir.replay.state_machine import (
    diff_aggregate_states,
    fold_events_to_state,
    replay_validation_notes,
)
from temir.web.event_schema import SCHEMA_VERSION
from temir.web.hub import get_debug_hub

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"


class ReplayBranchBody(BaseModel):
    parent_run_id: str = Field(..., min_length=1)
    fork_seq: int = Field(..., ge=0)
    child_run_id: str = Field(..., min_length=1)


def create_app() -> Any:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(
        title="Temir Debug Control Panel",
        description="Лёгкая панель: pipeline, логи, diff, cost, decision inspector.",
        version="0.1.0",
    )

    @app.get("/api/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok", "service": "temir-ui"}

    @app.get("/api/info")
    async def info() -> Dict[str, Any]:
        return {
            "bind": "see server startup",
            "websocket": "/ws",
            "events_doc": "interface_layer.event_contract in spec.yaml",
            "event_schema_version": SCHEMA_VERSION,
            "event_schema_strict_env": "TEMIR_EVENT_SCHEMA_STRICT",
            "event_journal_dir": str(get_journal_base().resolve()),
            "event_journal_env": "TEMIR_EVENT_JOURNAL_DIR",
            "replay_engine": "v2",
            "replay_state": "/api/run/{run_id}/replay/v2/state",
            "replay_diff": "/api/replay/v2/diff/{run_a}/{run_b}",
            "replay_branch": "POST /api/replay/v2/branch",
        }

    @app.get("/api/runs")
    async def api_list_runs() -> Dict[str, Any]:
        return {"runs": list_run_ids()}

    @app.get("/api/run/{run_id}")
    async def api_get_run(run_id: str) -> Dict[str, Any]:
        events = load_run_events(run_id)
        return {
            "run_id": run_id,
            "event_count": len(events),
            "events": events,
        }

    @app.get("/api/run/{run_id}/replay/v2/state")
    async def api_replay_v2_state(
        run_id: str,
        until_seq: Optional[int] = None,
        raw_end_inclusive: Optional[int] = None,
    ) -> Dict[str, Any]:
        events = load_run_events(run_id)
        ok, notes = replay_validation_notes(events)
        state = fold_events_to_state(
            events,
            until_seq=until_seq,
            raw_end_inclusive=raw_end_inclusive,
        )
        return {
            "run_id": run_id,
            "until_seq": until_seq,
            "raw_end_inclusive": raw_end_inclusive,
            "validation_ok": ok,
            "validation_notes": notes,
            "state": state.to_jsonable(),
        }

    @app.get("/api/replay/v2/diff/{run_a}/{run_b}")
    async def api_replay_v2_diff(
        run_a: str,
        run_b: str,
        until_seq_a: Optional[int] = None,
        until_seq_b: Optional[int] = None,
    ) -> Dict[str, Any]:
        ev_a = load_run_events(run_a)
        ev_b = load_run_events(run_b)
        st_a = fold_events_to_state(ev_a, until_seq=until_seq_a)
        st_b = fold_events_to_state(ev_b, until_seq=until_seq_b)
        return {
            "run_a": run_a,
            "run_b": run_b,
            "until_seq_a": until_seq_a,
            "until_seq_b": until_seq_b,
            "diff": diff_aggregate_states(st_a, st_b),
            "state_a": st_a.to_jsonable(),
            "state_b": st_b.to_jsonable(),
        }

    @app.post("/api/replay/v2/branch")
    async def api_replay_v2_branch(body: ReplayBranchBody) -> Any:
        try:
            path = branch_run_journal(
                body.parent_run_id,
                body.fork_seq,
                body.child_run_id,
            )
        except FileExistsError as e:
            return JSONResponse(
                status_code=409,
                content={"error": str(e), "code": "journal_exists"},
            )
        except ValueError as e:
            return JSONResponse(
                status_code=400,
                content={"error": str(e), "code": "invalid_branch"},
            )
        return {
            "child_run_id": body.child_run_id,
            "path": str(path),
            "events_written": len(load_run_events(body.child_run_id)),
        }

    @app.websocket("/ws")
    async def websocket_session(websocket: WebSocket) -> None:
        await websocket.accept()
        hub = get_debug_hub()
        await hub.register(websocket)
        await hub.publish("agent.event", {"message": "Клиент подключён к Debug Panel"})
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {"type": "raw", "text": raw}
                await hub.publish("client.message", {"data": data})
                if (
                    isinstance(data, dict)
                    and data.get("type") == "demo"
                    and isinstance(data.get("decision"), dict)
                ):
                    dec = data["decision"]
                    await hub.publish(
                        "decision.selected",
                        {
                            "task_id": "demo",
                            "decision": str(dec.get("chosen", "demo")),
                            "reason": "websocket_demo",
                        },
                    )
                    await hub.publish(
                        "patch.proposed",
                        {
                            "task_id": "demo",
                            "summary": {
                                "action": "demo",
                                "arg_keys": ["diff"],
                                "diff_preview": "--- a/example.py\n+++ b/example.py\n@@ -1,1 +1,2 @@\n+# demo\n",
                            },
                        },
                    )
        except WebSocketDisconnect:
            pass
        finally:
            await hub.unregister(websocket)

    if _STATIC_DIR.is_dir():
        app.mount(
            "/static",
            StaticFiles(directory=str(_STATIC_DIR)),
            name="static",
        )

    @app.get("/")
    async def index() -> FileResponse:
        index_path = _STATIC_DIR / "index.html"
        if not index_path.is_file():
            return JSONResponse(
                status_code=503,
                content={"error": "static UI not found", "path": str(index_path)},
            )
        return FileResponse(index_path)

    return app
