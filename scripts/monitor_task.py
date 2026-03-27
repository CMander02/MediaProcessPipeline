"""
Submit a task and monitor CPU/GPU usage per pipeline step.

Usage:
    cd backend && uv run python ../scripts/monitor_task.py <source_url>
"""

import json
import sys
import time
import subprocess
import threading
import urllib.request
import urllib.error

API = "http://localhost:18000"


def get_gpu_usage() -> dict:
    """Query nvidia-smi for GPU utilization and memory."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            parts = r.stdout.strip().split(", ")
            return {
                "gpu_util": f"{parts[0]}%",
                "gpu_mem": f"{parts[1]}/{parts[2]} MB",
                "gpu_temp": f"{parts[3]}°C",
            }
    except Exception:
        pass
    return {"gpu_util": "N/A", "gpu_mem": "N/A", "gpu_temp": "N/A"}


def get_cpu_usage() -> str:
    """Get CPU usage via wmic (Windows)."""
    try:
        r = subprocess.run(
            ["wmic", "cpu", "get", "loadpercentage", "/value"],
            capture_output=True, text=True, timeout=5,
        )
        for line in r.stdout.strip().split("\n"):
            if "LoadPercentage" in line:
                return line.split("=")[1].strip() + "%"
    except Exception:
        pass
    return "N/A"


def get_task_status(task_id: str) -> dict | None:
    try:
        req = urllib.request.Request(f"{API}/api/tasks/{task_id}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def submit_task(source: str) -> str:
    data = json.dumps({"task_type": "pipeline", "source": source}).encode()
    req = urllib.request.Request(
        f"{API}/api/tasks",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
        return result["id"]


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else None
    if not source:
        print("Usage: monitor_task.py <source_url_or_file>")
        sys.exit(1)

    print(f"Submitting: {source}")
    task_id = submit_task(source)
    print(f"Task ID: {task_id}")
    print()
    print(f"{'Time':>8}  {'Step':<16}  {'CPU':>5}  {'GPU':>5}  {'GPU Mem':>14}  {'Temp':>5}  Status")
    print("-" * 80)

    start = time.time()
    last_step = ""

    while True:
        elapsed = time.time() - start
        mins, secs = divmod(int(elapsed), 60)

        task = get_task_status(task_id)
        if not task:
            print(f"{mins:02d}:{secs:02d}     (no response)")
            time.sleep(3)
            continue

        status = task["status"]
        step = task.get("current_step") or "-"
        gpu = get_gpu_usage()
        cpu = get_cpu_usage()

        if step != last_step:
            if last_step:
                print(f"{'':>8}  {'--- ' + last_step + ' done ---':<16}")
            last_step = step

        print(
            f"{mins:02d}:{secs:02d}     {step:<16}  {cpu:>5}  {gpu['gpu_util']:>5}  {gpu['gpu_mem']:>14}  {gpu['gpu_temp']:>5}  {status}"
        )

        if status in ("completed", "failed", "cancelled"):
            print()
            if status == "failed":
                print(f"ERROR: {task.get('error')}")
            else:
                print(f"Done in {mins}m {secs}s")
            break

        time.sleep(5)


if __name__ == "__main__":
    main()
