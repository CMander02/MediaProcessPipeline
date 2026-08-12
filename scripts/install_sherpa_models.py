"""Install and verify the four default sherpa-onnx ASR model bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

QWEN_REPO = "thieunv/sherpa-onnx-qwen3-asr-1.7B-int8"
QWEN_REVISION = "69eb686fd94a4a865bb5340a3d6ac0d7f1fec0d5"
ASSET_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
VAD_SHA256 = "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"

MODELS = {
    "qwen3-asr-1.7b-onnx": {
        "display_name": "Qwen3-ASR 1.7B INT8",
        "family": "qwen3_asr",
        "source": f"https://huggingface.co/{QWEN_REPO}",
        "revision": QWEN_REVISION,
        "license": "Apache-2.0",
        "files": {
            "conv_frontend": "conv_frontend.onnx",
            "encoder": "encoder.int8.onnx",
            "decoder": "decoder.int8.onnx",
            "tokenizer": "tokenizer",
        },
        "languages": ["auto", "zh", "en", "yue", "ja", "ko", "de", "fr", "es"],
        "supports_hotwords": True,
        "defaults": {"max_total_len": 2048, "max_new_tokens": 1024, "max_chunk_sec": 30},
        "checksums": {
            "conv_frontend.onnx": (
                "3cb27a9fe94d95c938e476f2012b21aba2ec0bfceef33b0e58acd208946bafdd"
            ),
            "encoder.int8.onnx": "a5deedae034ece715de8ed204378d8c77f889af3a60c2566581135e84cced7cd",
            "decoder.int8.onnx": "c43c853fa6e97d08365cb8a5502b360b595cd43c00dc60e4d8ca7cc18cad460b",
            "tokenizer/merges.txt": (
                "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5"
            ),
            "tokenizer/tokenizer_config.json": (
                "4942d005604266809309cabc9f4e9cb89ce855d59b14681fdc0e1cc62ea26c4c"
            ),
            "tokenizer/vocab.json": (
                "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"
            ),
        },
    },
    "sensevoice-small-int8": {
        "display_name": "SenseVoice Small INT8",
        "family": "sense_voice",
        "archive": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
        "source": f"{ASSET_BASE}/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
        "license": "Model bundle LICENSE",
        "files": {"model": "model.int8.onnx", "tokens": "tokens.txt"},
        "languages": ["auto", "zh", "en", "yue", "ja", "ko"],
        "supports_token_timestamps": True,
        "defaults": {"use_itn": True, "max_chunk_sec": 30},
        "checksums": {
            "model.int8.onnx": "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51",
            "tokens.txt": "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc",
        },
    },
    "paraformer-zh-int8": {
        "display_name": "Paraformer Chinese INT8",
        "family": "paraformer",
        "archive": "sherpa-onnx-paraformer-zh-int8-2025-10-07.tar.bz2",
        "source": f"{ASSET_BASE}/sherpa-onnx-paraformer-zh-int8-2025-10-07.tar.bz2",
        "license": "Apache-2.0",
        "files": {"model": "model.int8.onnx", "tokens": "tokens.txt"},
        "languages": ["zh", "en"],
        "defaults": {"max_chunk_sec": 30},
        "checksums": {
            "model.int8.onnx": "53813ee1d41722cc6370a571c887e6d0b391d25b8312cf714a31af85ea603812",
            "tokens.txt": "59aba8873a2ed1e122c25fee421e25f283b63290efbde85c1f01a853d83cb6e6",
        },
    },
    "whisper-small-multi-int8": {
        "display_name": "Whisper Small Multilingual INT8",
        "family": "whisper",
        "archive": "sherpa-onnx-whisper-small.tar.bz2",
        "source": f"{ASSET_BASE}/sherpa-onnx-whisper-small.tar.bz2",
        "license": "MIT",
        "files": {
            "encoder": "small-encoder.int8.onnx",
            "decoder": "small-decoder.int8.onnx",
            "tokens": "small-tokens.txt",
        },
        "languages": ["auto", "zh", "en", "yue", "ja", "ko", "de", "fr", "es"],
        "supports_native_timestamps": True,
        "defaults": {
            "task": "transcribe",
            "enable_token_timestamps": False,
            "max_chunk_sec": 30,
        },
        "checksums": {
            "small-encoder.int8.onnx": (
                "4cbe7b22fa9026b843b60a68640c747de05bafb1a11b57edc0e66c232d9f33a9"
            ),
            "small-decoder.int8.onnx": (
                "acad50b5c782696e91b55914cc5ab4f756f1532f76e22aa6fc615f39fb69a8ee"
            ),
            "small-tokens.txt": "b34b360dbb493e781e479794586d661700670d65564001f23024971d1f2fa126",
        },
    },
}


def default_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "MediaProcessPipeline" / "models" / "sherpa-onnx"
    return Path.home() / ".local" / "share" / "MediaProcessPipeline" / "models" / "sherpa-onnx"


def complete(model_dir: Path, metadata: dict) -> bool:
    required = {
        *metadata["files"].values(),
        *metadata["checksums"].keys(),
    }
    return all((model_dir / relative).exists() for relative in required)


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 0:
        return
    temporary = destination.with_suffix(destination.suffix + ".part")
    print(f"Downloading {url}")
    urllib.request.urlretrieve(url, temporary)
    temporary.replace(destination)


def safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    resolved_destination = destination.resolve()
    with tarfile.open(archive, "r:bz2") as package:
        members = package.getmembers()
        top_levels = {
            Path(member.name).parts[0]
            for member in members
            if member.name and Path(member.name).parts
        }
        strip_top = len(top_levels) == 1
        for member in members:
            parts = Path(member.name).parts
            relative_parts = parts[1:] if strip_top else parts
            if not relative_parts:
                continue
            target = (destination / Path(*relative_parts)).resolve()
            if resolved_destination not in target.parents and target != resolved_destination:
                raise RuntimeError(f"Archive member escapes destination: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = package.extractfile(member)
            if source is None:
                continue
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(model_id: str, root: Path, metadata: dict) -> None:
    model_dir = root / model_id
    manifest = {
        "id": model_id,
        "sherpa_onnx_min_version": "1.13.4",
        **{key: value for key, value in metadata.items() if key != "archive"},
    }
    (model_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def verify_model_files(model_id: str, root: Path, metadata: dict) -> None:
    model_dir = root / model_id
    for relative, expected in metadata["checksums"].items():
        path = model_dir / relative
        if not path.is_file():
            raise RuntimeError(f"{model_id}: checksum target missing: {relative}")
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"{model_id}: checksum mismatch for {relative}; "
                f"expected {expected}, got {actual}"
            )


def verify_manifest(model_id: str, root: Path, metadata: dict) -> None:
    manifest_path = root / model_id / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"{model_id}: manifest.json is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("id") != model_id:
        raise RuntimeError(f"{model_id}: manifest id mismatch")
    if manifest.get("checksums") != metadata["checksums"]:
        raise RuntimeError(f"{model_id}: manifest checksums do not match the pinned model set")


def install_qwen(root: Path) -> None:
    model_dir = root / "qwen3-asr-1.7b-onnx"
    if complete(model_dir, MODELS["qwen3-asr-1.7b-onnx"]):
        return
    command = [
        "hf",
        "download",
        QWEN_REPO,
        "--revision",
        QWEN_REVISION,
        "--local-dir",
        str(model_dir),
        "--max-workers",
        "4",
    ]
    subprocess.run(command, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-root", type=Path, default=default_root())
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    root = args.model_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    if not args.verify_only:
        install_qwen(root)
        downloads = root / "_downloads"
        for model_id, metadata in MODELS.items():
            if model_id == "qwen3-asr-1.7b-onnx":
                continue
            model_dir = root / model_id
            if complete(model_dir, metadata):
                continue
            archive = downloads / metadata["archive"]
            download(metadata["source"], archive)
            safe_extract(archive, model_dir)
        vad_path = root / "silero_vad.onnx"
        download(f"{ASSET_BASE}/silero_vad.onnx", vad_path)

    errors: list[str] = []
    for model_id, metadata in MODELS.items():
        model_dir = root / model_id
        if complete(model_dir, metadata):
            try:
                verify_model_files(model_id, root, metadata)
                if args.verify_only:
                    verify_manifest(model_id, root, metadata)
                else:
                    write_manifest(model_id, root, metadata)
                print(f"OK {model_id}: {model_dir}")
            except (OSError, ValueError, RuntimeError) as exc:
                errors.append(str(exc))
        else:
            missing = [
                relative
                for relative in metadata["files"].values()
                if not (model_dir / relative).exists()
            ]
            errors.append(f"{model_id}: missing {', '.join(missing)}")
    if errors:
        raise SystemExit("\n".join(errors))

    vad_path = root / "silero_vad.onnx"
    if not vad_path.is_file():
        raise SystemExit(f"VAD model is missing: {vad_path}")
    actual_vad_sha256 = sha256(vad_path)
    if actual_vad_sha256 != VAD_SHA256:
        raise SystemExit(
            f"VAD checksum mismatch: expected {VAD_SHA256}, got {actual_vad_sha256}"
        )

    print(f"Model root: {root}")
    print(f"VAD model: {vad_path}")


if __name__ == "__main__":
    main()
