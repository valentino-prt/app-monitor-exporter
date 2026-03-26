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


# Status codes
STATUS_OK = 0
STATUS_DELAYED = 1
STATUS_STALLED = 2
STATUS_DUPLICATE = 3
STATUS_NOT_HERE = 4
STATUS_DOWN = 5
STATUS_IDLE = 6


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


def get_delay_threshold_sec(app: dict) -> int:
    """
    Threshold for 'delayed' status.
    Default: one loop interval.
    """
    return int(app.get("delay_threshold_sec", app["loop_interval_sec"]))


def get_stall_threshold_sec(app: dict) -> int:
    """
    Threshold for 'stalled' status.
    Default: two loop intervals.
    """
    return int(app.get("stall_threshold_sec", int(app["loop_interval_sec"]) * 2))


def evaluate_status(
    *,
    expected: int,
    running: int,
    duplicate: int,
    lag: int | None,
    delayed: int,
    stalled: int,
) -> tuple[int, str, str]:
    """
    Decision tree:

    expected?
      no  -> IDLE
      yes ->
        running?
          no ->
            stalled?
              yes -> DOWN
              no  -> NOT HERE
          yes ->
            duplicate?
              yes -> DUPLICATE
              no ->
                stalled?
                  yes -> STALLED
                  no ->
                    delayed?
                      yes -> DELAYED
                      no  -> OK
    """
    if expected == 0:
        return STATUS_IDLE, "IDLE", "Not scheduled to run"

    if running == 0:
        if stalled == 1:
            return STATUS_DOWN, "DOWN", "App down — restart required"
        return STATUS_NOT_HERE, "NOT_HERE", "Not running here — likely elsewhere"

    if duplicate == 1:
        return STATUS_DUPLICATE, "DUPLICATE", "Multiple instances — check"

    if stalled == 1:
        return STATUS_STALLED, "STALLED", "Alive but stuck — investigate"

    if delayed == 1:
        return STATUS_DELAYED, "DELAYED", "Delayed — monitor"

    return STATUS_OK, "OK", "OK"


def write_metrics(app: dict) -> None:
    """
    Write one .prom file per app, so Alloy textfile collector can scrape them.
    """
    name = app["name"]
    process_match = app["process_name"]

    instances = count_processes(process_match)
    running = int(instances > 0)

    expected = is_expected(app["start_time"], app["end_time"])
    missing = int(expected == 1 and running == 0)
    duplicate = int(instances > 1)

    heartbeat_ts = get_heartbeat_timestamp(app.get("heartbeat_file"))
    lag = compute_lag_seconds(heartbeat_ts)

    delay_threshold_sec = get_delay_threshold_sec(app)
    stall_threshold_sec = get_stall_threshold_sec(app)

    delayed = int(lag is not None and lag > delay_threshold_sec)
    stalled = int(lag is not None and lag > stall_threshold_sec)

    status_code, status_name, instruction = evaluate_status(
        expected=expected,
        running=running,
        duplicate=duplicate,
        lag=lag,
        delayed=delayed,
        stalled=stalled,
    )

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
    g_delayed = Gauge(
        "app_delayed",
        "Whether heartbeat lag exceeds the delay threshold (1=yes, 0=no)",
        ["app"],
        registry=registry,
    )
    g_stalled = Gauge(
        "app_stalled",
        "Whether heartbeat lag exceeds the stall threshold (1=yes, 0=no)",
        ["app"],
        registry=registry,
    )
    g_heartbeat_ts = Gauge(
        "app_heartbeat_timestamp",
        "Last heartbeat file modification time as Unix timestamp",
        ["app"],
        registry=registry,
    )
    g_status_code = Gauge(
        "app_status_code",
        "Application status code: 0=OK, 1=DELAYED, 2=STALLED, 3=DUPLICATE, 4=NOT_HERE, 5=DOWN, 6=IDLE",
        ["app"],
        registry=registry,
    )

    # Optional one-hot status metrics: very handy in Grafana and alerting
    g_status_ok = Gauge(
        "app_status_ok",
        "Status is OK (1=yes, 0=no)",
        ["app"],
        registry=registry,
    )
    g_status_delayed = Gauge(
        "app_status_delayed",
        "Status is DELAYED (1=yes, 0=no)",
        ["app"],
        registry=registry,
    )
    g_status_stalled = Gauge(
        "app_status_stalled",
        "Status is STALLED (1=yes, 0=no)",
        ["app"],
        registry=registry,
    )
    g_status_duplicate = Gauge(
        "app_status_duplicate",
        "Status is DUPLICATE (1=yes, 0=no)",
        ["app"],
        registry=registry,
    )
    g_status_not_here = Gauge(
        "app_status_not_here",
        "Status is NOT_HERE (1=yes, 0=no)",
        ["app"],
        registry=registry,
    )
    g_status_down = Gauge(
        "app_status_down",
        "Status is DOWN (1=yes, 0=no)",
        ["app"],
        registry=registry,
    )
    g_status_idle = Gauge(
        "app_status_idle",
        "Status is IDLE (1=yes, 0=no)",
        ["app"],
        registry=registry,
    )

    g_running.labels(app=name).set(running)
    g_instances.labels(app=name).set(instances)
    g_expected.labels(app=name).set(expected)
    g_missing.labels(app=name).set(missing)
    g_duplicate.labels(app=name).set(duplicate)
    g_lag.labels(app=name).set(lag if lag is not None else 0)
    g_delayed.labels(app=name).set(delayed)
    g_stalled.labels(app=name).set(stalled)
    g_heartbeat_ts.labels(app=name).set(heartbeat_ts if heartbeat_ts is not None else 0)
    g_status_code.labels(app=name).set(status_code)

    g_status_ok.labels(app=name).set(int(status_code == STATUS_OK))
    g_status_delayed.labels(app=name).set(int(status_code == STATUS_DELAYED))
    g_status_stalled.labels(app=name).set(int(status_code == STATUS_STALLED))
    g_status_duplicate.labels(app=name).set(int(status_code == STATUS_DUPLICATE))
    g_status_not_here.labels(app=name).set(int(status_code == STATUS_NOT_HERE))
    g_status_down.labels(app=name).set(int(status_code == STATUS_DOWN))
    g_status_idle.labels(app=name).set(int(status_code == STATUS_IDLE))

    output_file = METRICS_DIR / f"{name}.prom"
    write_to_textfile(str(output_file), registry)

    print(
        f"[INFO] {name}: status={status_name} "
        f"instruction='{instruction}' running={running} instances={instances} "
        f"expected={expected} missing={missing} duplicate={duplicate} "
        f"lag={lag} delayed={delayed} stalled={stalled}"
    )


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