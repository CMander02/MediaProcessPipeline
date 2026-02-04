"""Start uvicorn server on fixed port 18000."""

import sys
import uvicorn

# Force UTF-8 encoding for stdout/stderr on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main():
    port = 18000
    print(f"\n>>> Starting server on http://127.0.0.1:{port}\n")

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=port,
        reload="--reload" in sys.argv,
    )


if __name__ == "__main__":
    main()
