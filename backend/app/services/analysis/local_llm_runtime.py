"""Managed llama.cpp runtime for local text and vision models."""

from __future__ import annotations

import logging
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class LocalLlamaCppRuntime:
    """Own one lazily started llama-server process and stop it after inactivity."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[Any] | None = None
        self._base_url = ""
        self._signature: tuple[Any, ...] | None = None
        self._timer: threading.Timer | None = None
        self._lock = threading.RLock()

    def ensure(self, config: dict[str, Any]) -> str:
        signature = self._signature_for(config)
        with self._lock:
            if self._process and self._process.poll() is None and self._signature == signature:
                self._schedule_stop(float(config.get("keepalive_sec") or 0))
                return self._base_url

            self.stop()
            binary = self._resolve_binary(str(config.get("binary_path") or ""))
            model_path = Path(str(config.get("model_path") or "")).expanduser()
            mmproj_value = str(config.get("mmproj_path") or "").strip()
            if not model_path.is_file():
                raise RuntimeError(f"Local GGUF model does not exist: {model_path}")
            if mmproj_value and not Path(mmproj_value).expanduser().is_file():
                raise RuntimeError(f"Local multimodal projector does not exist: {mmproj_value}")

            port = self._free_port()
            args = [
                binary,
                "--host", "127.0.0.1",
                "--port", str(port),
                "--model", str(model_path),
            ]
            if mmproj_value:
                args.extend(["--mmproj", mmproj_value])
            args.extend([
                "-c", str(max(2048, int(config.get("ctx") or 8192))),
                "-np", str(max(1, int(config.get("parallel") or 1))),
                *self._device_args(config),
                "--cache-type-k", "q8_0",
                "--cache-type-v", "q8_0",
                "-a", str(config.get("alias") or "Local-LLM"),
            ])
            logger.info("Starting local llama.cpp model: %s", " ".join(args))
            self._process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._base_url = f"http://localhost:{port}"
            self._signature = signature
            try:
                self._wait_until_ready(float(config.get("startup_timeout_sec") or 300))
                self._schedule_stop(float(config.get("keepalive_sec") or 0))
            except Exception:
                self.stop()
                raise
            return self._base_url

    def stop(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            process = self._process
            self._process = None
            self._base_url = ""
            self._signature = None
            if process and process.poll() is None:
                logger.info("Stopping local llama.cpp model")
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()

    def _schedule_stop(self, keepalive_sec: float) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if keepalive_sec <= 0:
            return
        self._timer = threading.Timer(keepalive_sec, self.stop)
        self._timer.daemon = True
        self._timer.start()

    @staticmethod
    def _signature_for(config: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(config.get(key) for key in (
            "binary_path", "model_path", "mmproj_path", "device", "ctx",
            "n_gpu_layers", "parallel", "alias",
        ))

    @staticmethod
    def _resolve_binary(value: str) -> str:
        binary = value.strip() or shutil.which("llama-server") or shutil.which("llama-server.exe") or ""
        if not binary or not Path(binary).is_file():
            raise RuntimeError("llama-server is unavailable; configure llama_cpp_binary_path")
        return binary

    @staticmethod
    def _device_args(config: dict[str, Any]) -> list[str]:
        device = str(config.get("device") or "auto").lower()
        if device == "auto":
            device = "cuda" if LocalLlamaCppRuntime._has_nvidia_gpu() else "cpu"
        if device == "cuda":
            layers = int(config.get("n_gpu_layers") if config.get("n_gpu_layers") is not None else 99)
            return ["-fa", "on", "-ngl", str(layers)]
        return ["-ngl", "0"]

    @staticmethod
    def _has_nvidia_gpu() -> bool:
        try:
            result = subprocess.run(
                ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=3, check=False,
            )
            return result.returncode == 0 and "GPU" in result.stdout
        except Exception:
            return False

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _wait_until_ready(self, timeout_sec: float) -> None:
        deadline = time.monotonic() + timeout_sec
        last_error = ""
        while time.monotonic() < deadline:
            if self._process and self._process.poll() is not None:
                raise RuntimeError("Local llama.cpp server exited during startup")
            try:
                response = httpx.get(f"{self._base_url}/health", timeout=2)
                if response.status_code < 500:
                    return
            except Exception as exc:
                last_error = str(exc)
            time.sleep(0.5)
        raise RuntimeError(f"Local llama.cpp server did not become ready: {last_error}")


_runtime: LocalLlamaCppRuntime | None = None


def get_local_llm_runtime() -> LocalLlamaCppRuntime:
    global _runtime
    if _runtime is None:
        _runtime = LocalLlamaCppRuntime()
    return _runtime
