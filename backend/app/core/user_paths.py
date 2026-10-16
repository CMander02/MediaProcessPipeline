"""Platform defaults for persistent user data, independent of the checkout."""

import os
import sys
from pathlib import Path


def default_data_root() -> Path:
    """Return the user-owned MPP library location without creating it."""
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    elif sys.platform == "win32":
        configured = os.environ.get("LOCALAPPDATA", "")
        base = Path(configured) if configured else home / "AppData" / "Local"
    else:
        configured = os.environ.get("XDG_DATA_HOME", "")
        base = Path(configured) if configured else home / ".local" / "share"
        # The XDG specification requires absolute base directories.
        if not base.is_absolute():
            base = home / ".local" / "share"
    return base / "MediaProcessPipeline"
