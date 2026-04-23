"""FastAPI backend for the youtube-video-ingestor UI.

Loads the same NVIDIA DLL setup ingest.py uses, before any heavy ML imports
elsewhere in the package import a CTranslate2-backed library.

Also auto-detects Deno in the WinGet default install path — yt-dlp needs a
JS runtime to solve YouTube's challenge (`n` param) and unlock real stream
URLs; WinGet adds deno to user PATH but only new shells see it.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _ensure_deno_on_path() -> None:
    if shutil.which("deno"):
        return
    if sys.platform != "win32":
        return
    localappdata = os.environ.get("LOCALAPPDATA") or ""
    if not localappdata:
        return
    winget = Path(localappdata) / "Microsoft" / "WinGet" / "Packages"
    if not winget.exists():
        return
    for p in winget.iterdir():
        if not p.is_dir() or "DenoLand.Deno" not in p.name:
            continue
        exe = p / "deno.exe"
        if exe.exists():
            os.environ["PATH"] = str(p) + os.pathsep + os.environ.get("PATH", "")
            return


_ensure_deno_on_path()


if sys.platform == "win32":
    for _pkg in (
        "nvidia.cublas",
        "nvidia.cudnn",
        "nvidia.cuda_runtime",
        "nvidia.cuda_nvrtc",
    ):
        _spec = importlib.util.find_spec(_pkg)
        if _spec and _spec.submodule_search_locations:
            _bin = Path(_spec.submodule_search_locations[0]) / "bin"
            if _bin.exists():
                os.add_dll_directory(str(_bin))
                os.environ["PATH"] = str(_bin) + os.pathsep + os.environ.get("PATH", "")
