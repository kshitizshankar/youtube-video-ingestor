"""WebSocket <-> Windows PTY bridge that runs `claude` (or any shell command)
in the configured working directory.

Client messages (JSON):
    {"type": "input", "data": "..."}        # raw text from xterm.js onData
    {"type": "resize", "rows": N, "cols": M}

Server messages: raw text from the PTY (one or more chunks per send).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect
from winpty import PtyProcess


log = logging.getLogger(__name__)

# Default to the Claude Code CLI. Override via env / endpoint param if needed.
DEFAULT_COMMAND = ["claude"]


async def handle_pty_session(
    ws: WebSocket,
    cwd: Path,
    command: list[str] | None = None,
    cols: int = 100,
    rows: int = 30,
) -> None:
    await ws.accept()
    cmd = command or DEFAULT_COMMAND
    proc: PtyProcess | None = None
    pump_task: asyncio.Task | None = None

    try:
        # On Windows, PtyProcess.spawn takes a string command line.
        spawn_str = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        proc = PtyProcess.spawn(spawn_str, cwd=str(cwd), dimensions=(rows, cols))
        log.info("PTY spawned: %s (cwd=%s)", spawn_str, cwd)

        loop = asyncio.get_running_loop()

        async def pump_pty_to_ws() -> None:
            while True:
                try:
                    chunk = await asyncio.to_thread(proc.read, 4096)
                except (EOFError, OSError):
                    break
                if not chunk:
                    break
                try:
                    await ws.send_text(chunk)
                except Exception:
                    break
            try:
                await ws.send_json({"type": "exit", "code": proc.exitstatus})
            except Exception:
                pass

        pump_task = asyncio.create_task(pump_pty_to_ws())

        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                # Treat raw text as input
                await asyncio.to_thread(proc.write, raw)
                continue

            t = msg.get("type")
            if t == "input":
                await asyncio.to_thread(proc.write, msg.get("data", ""))
            elif t == "resize":
                try:
                    proc.setwinsize(int(msg.get("rows", rows)), int(msg.get("cols", cols)))
                except Exception as e:
                    log.warning("resize failed: %s", e)
            else:
                log.debug("unknown msg type: %s", t)

    except WebSocketDisconnect:
        log.info("WebSocket disconnected by client")
    except Exception as e:
        log.exception("PTY session error: %s", e)
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        if pump_task:
            pump_task.cancel()
        if proc and proc.isalive():
            try:
                proc.terminate(force=True)
            except Exception:
                pass
        try:
            await ws.close()
        except Exception:
            pass
