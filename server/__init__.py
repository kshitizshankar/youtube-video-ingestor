"""FastAPI backend for the youtube-video-ingestor UI.

Loads the same NVIDIA DLL setup ingest.py uses, before any heavy ML imports
elsewhere in the package import a CTranslate2-backed library.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


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
