"""Short-lived GPU UVR worker used to isolate ONNX Runtime DLLs on Windows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.preprocessing.uvr import UVRService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()

    service = UVRService()
    try:
        result = service.separate(
            str(args.audio.resolve()),
            output_dir=args.output_dir.resolve(),
        )
        args.result.write_text(
            json.dumps(result, ensure_ascii=False),
            encoding="utf-8",
        )
    finally:
        service.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
