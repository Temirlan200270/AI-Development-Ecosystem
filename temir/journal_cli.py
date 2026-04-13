"""
CLI для event journal: человекочитаемый вывод без Debug Web UI.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer

from temir.smoke_v1 import load_events_jsonl
from temir.storage.event_journal import get_journal_base, sanitize_run_id
from temir.storage.run_store import list_run_ids

journal_app = typer.Typer(
    help="Журнал событий (JSONL): cat / tail / список runs — удобнее, чем только GUI.",
    no_args_is_help=True,
)


def _resolve_events_path(
    run_id: Optional[str],
    journal: Optional[Path],
) -> Path:
    if journal is not None:
        if not journal.is_file():
            typer.secho(f"Journal not found: {journal}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        return journal
    rid = run_id
    if not rid:
        ids = list_run_ids()
        if not ids:
            typer.secho(
                "Нет runs в journal. Укажите --run-id или --journal PATH.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)
        rid = ids[-1]
        typer.echo(f"Using latest run_id: {rid}")
    safe = sanitize_run_id(rid)
    p = get_journal_base() / safe / "events.jsonl"
    if not p.is_file():
        typer.secho(f"events.jsonl не найден: {p}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    return p


def format_event_line(ev: Dict[str, Any], *, full: bool = False) -> str:
    if full:
        return json.dumps(ev, ensure_ascii=False, indent=2)
    topic = ev.get("topic", "?")
    seq = ev.get("seq", "")
    rid = ev.get("run_id", "")
    payload = ev.get("payload")
    pl = ""
    if isinstance(payload, dict):
        parts: List[str] = []
        for k in list(payload.keys())[:8]:
            v = payload[k]
            if isinstance(v, str) and len(v) > 72:
                v = v[:69] + "..."
            parts.append(f"{k}={v!r}")
        pl = " | " + " ".join(parts)
    return f"seq={seq} topic={topic} run={rid}{pl}"


def _filter_events(
    events: List[Dict[str, Any]],
    topic_sub: Optional[str],
) -> List[Dict[str, Any]]:
    if not topic_sub:
        return events
    tlow = topic_sub.lower()
    return [e for e in events if tlow in str(e.get("topic", "")).lower()]


@journal_app.command("runs")
def journal_runs() -> None:
    """Список run_id, для которых есть events.jsonl."""
    ids = list_run_ids()
    if not ids:
        typer.echo("(нет runs — каталог journal пуст или нет events.jsonl)")
        raise typer.Exit(code=0)
    base = get_journal_base()
    for rid in ids:
        p = base / rid / "events.jsonl"
        try:
            with p.open(encoding="utf-8") as fh:
                n = sum(1 for line in fh if line.strip())
        except OSError:
            n = 0
        typer.echo(f"{rid}\t{n} events\t{p}")


@journal_app.command("cat")
def journal_cat(
    run_id: Optional[str] = typer.Option(
        None,
        "--run-id",
        "-r",
        help="Каталог run под .andromeda/runs/<id>/",
    ),
    journal: Optional[Path] = typer.Option(
        None,
        "--journal",
        "-j",
        help="Прямой путь к events.jsonl",
    ),
    topic: Optional[str] = typer.Option(
        None,
        "--topic",
        "-t",
        help="Подстрока в topic (фильтр)",
    ),
    last: Optional[int] = typer.Option(
        None,
        "--last",
        "-n",
        help="Только последние N событий",
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="Полный JSON на каждое событие",
    ),
) -> None:
    """Вывести события из JSONL (по умолчанию — последний run)."""
    path = _resolve_events_path(run_id, journal)
    events = load_events_jsonl(path)
    events = _filter_events(events, topic)
    if last is not None and last > 0:
        events = events[-last:]
    typer.echo(f"# {path} ({len(events)} lines)", err=True)
    for ev in events:
        typer.echo(format_event_line(ev, full=full))


@journal_app.command("tail")
def journal_tail(
    run_id: Optional[str] = typer.Option(None, "--run-id", "-r"),
    journal: Optional[Path] = typer.Option(None, "--journal", "-j"),
    lines: int = typer.Option(40, "-n", "--lines", help="Сколько последних событий показать (без -f)"),
    follow: bool = typer.Option(False, "-f", "--follow", help="Ждать новые строки (как tail -f)"),
    poll: float = typer.Option(0.4, "--poll", help="Интервал опроса сек (только с -f)"),
) -> None:
    """Последние N событий или поток в реальном времени (-f)."""
    path = _resolve_events_path(run_id, journal)
    if not follow:
        events = load_events_jsonl(path)
        chunk = events[-lines:] if lines > 0 else events
        typer.echo(f"# {path} (last {len(chunk)} of {len(events)})", err=True)
        for ev in chunk:
            typer.echo(format_event_line(ev, full=False))
        return

    typer.echo(f"# following {path} (Ctrl+C to stop)", err=True)
    try:
        with path.open(encoding="utf-8") as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                        typer.echo(format_event_line(ev, full=False))
                    except json.JSONDecodeError:
                        typer.secho(f"[invalid json] {line[:120]}...", fg=typer.colors.RED)
                else:
                    time.sleep(poll)
    except KeyboardInterrupt:
        typer.echo("", err=True)
