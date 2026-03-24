import time
from datetime import datetime
from pathlib import Path

import psutil
import yaml
from prometheus_client import CollectorRegistry, Gauge, write_to_textfile


METRICS_DIR = Path("./metrics")
METRICS_DIR.mkdir(exist_ok=True)

CONFIG_FILE = Path("apps.yaml")
POLL_INTERVAL_SEC = 10


def load_config() -> list[dict]:
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    apps = data.get("apps", [])
    if not isinstance(apps, list):
        raise ValueError("apps.yaml must contain an 'apps' list")

    return apps


def count_processes(process_match: str) -> int:
    """
    Count processes whose command line contains the configured string.
    Keep the match string specific enough to avoid false positives.
    """
    count = 0

    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            joined = " ".join(cmdline)
            if process_match and process_match in joined:
                count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception:
            continue

    return count


def is_expected(start: str, end: str) -> int:
    """
    Return 1 if current time is inside the configured window.
    Supports windows crossing midnight, e.g. 22:00 -> 06:00.
    """
    now = datetime.now().time()
    start_t = datetime.strptime(start, "%H:%M").time()
    end_t = datetime.strptime(end, "%H:%M").time()

    if start_t <= end_t:
        return int(start_t <= now <= end_t)

    return int(now >= start_t or now <= end_t)


def get_heartbeat_timestamp(heartbeat_file: str | None) -> int | None:
    if not heartbeat_file:
        return None

    path = Path(heartbeat_file)
    if not path.exists():
        return None

    return int(path.stat().st_mtime)


def compute_lag_seconds(heartbeat_ts: int | None) -> int | None:
    if heartbeat_ts is None:
        return None
    return int(time.time() - heartbeat_ts)


def write_metrics(app: dict) -> None:
    """
    Write one .prom file per app, so Alloy textfile collector can scrape them.
    """
    name = app["name"]
    process_match = app["process_name"]
    loop_interval_sec = int(app["loop_interval_sec"])

    instances = count_processes(process_match)
    running = int(instances > 0)

    expected = is_expected(app["start_time"], app["end_time"])
    missing = int(expected == 1 and running == 0)
    duplicate = int(instances > 1)

    heartbeat_ts = get_heartbeat_timestamp(app.get("heartbeat_file"))
    lag = compute_lag_seconds(heartbeat_ts)

    stalled = 0
    if lag is not None and lag > loop_interval_sec * 2:
        stalled = 1

    registry = CollectorRegistry()

    g_running = Gauge(
        "app_running",
        "Whether the app process is running (1=yes, 0=no)",
        ["app"],
        registry=registry,
    )
    g_instances = Gauge(
        "app_instances",
        "Number of matching running processes",
        ["app"],
        registry=registry,
    )
    g_expected = Gauge(
        "app_expected",
        "Whether the app is expected to run now (1=yes, 0=no)",
        ["app"],
        registry=registry,
    )
    g_missing = Gauge(
        "app_missing",
        "Whether the app is expected but not running (1=yes, 0=no)",
        ["app"],
        registry=registry,
    )
    g_duplicate = Gauge(
        "app_duplicate",
        "Whether more than one instance is running (1=yes, 0=no)",
        ["app"],
        registry=registry,
    )
    g_lag = Gauge(
        "app_lag_seconds",
        "Seconds since last heartbeat file update",
        ["app"],
        registry=registry,
    )
    g_stalled = Gauge(
        "app_stalled",
        "Whether heartbeat lag exceeds the tolerated threshold (1=yes, 0=no)",
        ["app"],
        registry=registry,
    )
    g_heartbeat_ts = Gauge(
        "app_heartbeat_timestamp",
        "Last heartbeat file modification time as Unix timestamp",
        ["app"],
        registry=registry,
    )

    g_running.labels(app=name).set(running)
    g_instances.labels(app=name).set(instances)
    g_expected.labels(app=name).set(expected)
    g_missing.labels(app=name).set(missing)
    g_duplicate.labels(app=name).set(duplicate)
    g_lag.labels(app=name).set(lag if lag is not None else 0)
    g_stalled.labels(app=name).set(stalled)
    g_heartbeat_ts.labels(app=name).set(heartbeat_ts if heartbeat_ts is not None else 0)

    output_file = METRICS_DIR / f"{name}.prom"
    write_to_textfile(str(output_file), registry)


def main() -> None:
    apps = load_config()

    while True:
        for app in apps:
            try:
                write_metrics(app)
            except Exception as exc:
                print(f"[ERROR] Failed to write metrics for app={app.get('name', '?')}: {exc}")

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()