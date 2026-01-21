"""Start uvicorn server on fixed port 18000."""

import sys
import uvicorn


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
