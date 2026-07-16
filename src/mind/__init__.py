"""Load mind's modular development source into one shared module namespace."""
import json as _json
from pathlib import Path as _Path

_SOURCE_DIR = _Path(__file__).resolve().parent
_REPOSITORY = _SOURCE_DIR.parent.parent
_MANIFEST = _json.loads(
    (_SOURCE_DIR / "source.json").read_text("utf-8"))
_SOURCE = "".join(
    (_SOURCE_DIR / name).read_text("utf-8")
    for name in _MANIFEST["fragments"]
)
exec(compile(_SOURCE, str(_REPOSITORY / "mind.py"), "exec"), globals())
