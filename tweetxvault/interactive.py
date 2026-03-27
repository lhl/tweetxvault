"""TTY-gated status and progress helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from rich.console import Console


def _log_phase(console: Console, prefix: str, message: str) -> None:
    console.print(f"{prefix}: {message}", highlight=False)


def emit_status(status: Callable[[str], None] | None, message: str) -> None:
    if status is not None:
        status(message)


def status_printer(
    console: Console, prefix: str, *, force: bool = False
) -> Callable[[str], None] | None:
    if not (force or console.is_terminal):
        return None
    return lambda message: _log_phase(console, prefix, message)


@contextmanager
def progress_callback(
    console: Console,
    *,
    label: str,
    total: int,
    unit: str,
    leave: bool = False,
) -> Iterator[Callable[[int, int], None] | None]:
    if not console.is_terminal or total <= 0:
        yield None
        return

    from tqdm import tqdm

    progress_kwargs: dict[str, Any] = {
        "total": total,
        "desc": label,
        "unit": unit,
        "dynamic_ncols": True,
        "leave": leave,
        "file": console.file,
    }
    if unit == "B":
        progress_kwargs["unit_scale"] = True
        progress_kwargs["unit_divisor"] = 1024

    with tqdm(**progress_kwargs) as progress_bar:
        last_done = 0

        def callback(done: int, _total: int) -> None:
            nonlocal last_done
            delta = done - last_done
            if delta > 0:
                progress_bar.update(delta)
            last_done = done

        yield callback
