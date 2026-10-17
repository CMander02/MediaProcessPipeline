"""Managed llama.cpp runtime for local text and vision models."""

from __future__ import annotations

import logging
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from contextvars import copy_context
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

import httpx

from app.core.logging_setup import log_event
from app.core.paths import get_workspace_paths

logger = logging.getLogger(__name__)


class LocalLlamaCppStartupError(RuntimeError):
    """The local server cannot serve requests; retrying individual batches cannot help."""


class LocalLlamaCppRuntime:
    """Own one lazily started llama-server process and stop it after inactivity."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[Any] | None = None
        self._base_url = ""
        self._signature: tuple[Any, ...] | None = None
        self._timer: threading.Timer | None = None
        self._log_file: BinaryIO | None = None
        self._log_path: Path | None = None
        self._lock = threading.RLock()

    def ensure(self, config: dict[str, Any]) -> str:
        signature = self._signature_for(config)
        with self._lock:
            if self._process and self._process.poll() is None and self._signature == signature:
                self._schedule_stop(float(config.get("keepalive_sec") or 0))
                return self._base_url

            self.stop()
            self._log_path = None
            try:
                self._start(config, signature)
                self._wait_until_ready(float(config.get("startup_timeout_sec") or 300))
                self._schedule_stop(float(config.get("keepalive_sec") or 0))
            except Exception as exc:
                detail = (str(exc) if isinstance(exc, LocalLlamaCppStartupError)
                          else f"{type(exc).__name__}: {exc}")
                log_event(logger, logging.ERROR, "llama_cpp.startup_failed",
                          error=detail, log_path=self._log_path)
                if self._log_path:
                    detail += f"; server log: {self._log_path}"
                try:
                    self.stop()
                except Exception:
                    logger.exception("Failed to stop local llama.cpp after startup failure")
                raise LocalLlamaCppStartupError(detail) from exc
            log_event(logger, logging.INFO, "llama_cpp.ready",
                      base_url=self._base_url, log_path=self._log_path)
            return self._base_url

    def _start(self, config: dict[str, Any], signature: tuple[Any, ...]) -> None:
        binary = self._resolve_binary(str(config.get("binary_path") or ""))
        model_path = Path(str(config.get("model_path") or "")).expanduser()
        mmproj_value = str(config.get("mmproj_path") or "").strip()
        if not model_path.is_file():
            raise LocalLlamaCppStartupError(f"Local GGUF model does not exist: {model_path}")
        if mmproj_value and not Path(mmproj_value).expanduser().is_file():
            raise LocalLlamaCppStartupError(
                f"Local multimodal projector does not exist: {mmproj_value}",
            )

        port = self._free_port()
        args = [
            binary,
            # Match _free_port()'s IPv4 socket; Windows can resolve localhost to
            # IPv6 even when local IPv6 connections are unavailable.
            "--host", "127.0.0.1",
            "--port", str(port),
            "--model", str(model_path),
        ]
        if mmproj_value:
            args.extend(["--mmproj", mmproj_value])
        parallel = max(1, int(config.get("parallel") or 1))
        # llama.cpp divides total context across slots; ctx is per request.
        args.extend([
            "-c", str(max(2048, int(config.get("ctx") or 8192)) * parallel),
            "-np", str(parallel),
            *self._device_args(config),
            "--cache-type-k", str(config.get("cache_type") or "q8_0"),
            "--cache-type-v", str(config.get("cache_type") or "q8_0"),
            "-a", str(config.get("alias") or "Local-LLM"),
        ])
        if config.get("cache_ram") is not None:
            args.extend(["--cache-ram", str(config["cache_ram"])])
        log_dir = get_workspace_paths().logs
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self._log_path = log_dir / f"llama_cpp_{stamp}_{port}.log"
        self._log_file = self._log_path.open("wb")
        log_event(logger, logging.INFO, "llama_cpp.starting",
                  command=" ".join(args), parallel=parallel, log_path=self._log_path)
        self._process = subprocess.Popen(
            args,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._base_url = f"http://localhost:{port}"
        self._signature = signature

    def stop(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            process = self._process
            self._process = None
            self._base_url = ""
            self._signature = None
            try:
                if process and process.poll() is None:
                    log_event(logger, logging.INFO, "llama_cpp.stopping",
                              log_path=self._log_path)
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
            finally:
                if self._log_file:
                    self._log_file.close()
                    self._log_file = None

    def _schedule_stop(self, keepalive_sec: float) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if keepalive_sec <= 0:
            return
        self._timer = threading.Timer(keepalive_sec, copy_context().run, args=(self.stop,))
        self._timer.daemon = True
        self._timer.start()

    @staticmethod
    def _signature_for(config: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(config.get(key) for key in (
            "binary_path", "model_path", "mmproj_path", "device", "ctx",
            "n_gpu_layers", "parallel", "alias", "cache_type", "cache_ram",
        ))

    @staticmethod
    def _resolve_binary(value: str) -> str:
        binary = (
            value.strip() or shutil.which("llama-server") or shutil.which("llama-server.exe") or ""
        )
        if not binary or not Path(binary).is_file():
            raise LocalLlamaCppStartupError(
                "llama-server is unavailable; configure llama_cpp_binary_path",
            )
        return binary

    @staticmethod
    def _resolve_device(value: str) -> str:
        device = str(value or "auto").lower()
        if device == "auto":
            if sys.platform == "darwin" and platform.machine() == "arm64":
                return "metal"
            return "cuda" if LocalLlamaCppRuntime._has_nvidia_gpu() else "cpu"
        return device

    @staticmethod
    def _device_args(config: dict[str, Any]) -> list[str]:
        device = LocalLlamaCppRuntime._resolve_device(str(config.get("device") or "auto"))
        if device in {"cuda", "metal"}:
            layers = int(
                config.get("n_gpu_layers") if config.get("n_gpu_layers") is not None else 99
            )
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
        last_error = "Health check did not run before the startup deadline"
        # Loopback traffic must bypass Windows/system proxies as well as env proxies.
        with httpx.Client(timeout=2, trust_env=False) as client:
            while time.monotonic() < deadline:
                if self._process and (code := self._process.poll()) is not None:
                    raise LocalLlamaCppStartupError(
                        f"Local llama.cpp server exited during startup (exit code {code})",
                    )
                try:
                    response = client.get(f"{self._base_url}/health")
                    if response.status_code == 200:
                        return
                    body = " ".join(response.text[:300].split())
                    last_error = f"HTTP {response.status_code}: {body}"
                except httpx.HTTPError as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(0.5)
        raise LocalLlamaCppStartupError(
            f"Local llama.cpp server did not become ready within {timeout_sec:g}s: {last_error}",
        )


_runtime: LocalLlamaCppRuntime | None = None


def get_local_llm_runtime() -> LocalLlamaCppRuntime:
    global _runtime
    if _runtime is None:
        _runtime = LocalLlamaCppRuntime()
    return _runtime


def release_local_llm_runtime() -> None:
    if _runtime is not None:
        _runtime.stop()
