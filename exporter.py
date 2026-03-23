import yaml
import psutil
import time
from pathlib import Path
from datetime import datetime

METRICS_DIR = Path("./metrics")
METRICS_DIR.mkdir(exist_ok=True)


def load_config():
    with open("apps.yaml") as f:
        return yaml.safe_load(f)["apps"]


def count_processes(name):

    count = 0

    for p in psutil.process_iter(["cmdline"]):
        try:
            cmd = p.info.get("cmdline") or []
            if name in " ".join(cmd):
                count += 1
        except Exception:
            pass

    return count


def is_expected(start, end):

    now = datetime.now().time()
    s = datetime.strptime(start, "%H:%M").time()
    e = datetime.strptime(end, "%H:%M").time()

    return 1 if s <= now <= e else 0


def compute_lag(heartbeat_file):

    path = Path(heartbeat_file)

    if not path.exists():
        return None

    last = path.stat().st_mtime
    return int(time.time() - last)


def write_metrics(app):

    name = app["name"]
    proc = app["process_name"]

    instances = count_processes(proc)
    running = 1 if instances > 0 else 0

    expected = is_expected(app["start_time"], app["end_time"])
    missing = 1 if expected == 1 and running == 0 else 0
    duplicate = 1 if instances > 1 else 0

    lag = compute_lag(app.get("heartbeat_file", ""))

    stalled = 0
    if lag is not None:
        if lag > app["loop_interval_sec"] * 2:
            stalled = 1

    now = int(time.time())

    content = f"""
# TYPE app_running gauge
app_running{{app="{name}"}} {running}

# TYPE app_instances gauge
app_instances{{app="{name}"}} {instances}

# TYPE app_expected gauge
app_expected{{app="{name}"}} {expected}

# TYPE app_missing gauge
app_missing{{app="{name}"}} {missing}

# TYPE app_duplicate gauge
app_duplicate{{app="{name}"}} {duplicate}

# TYPE app_lag_seconds gauge
app_lag_seconds{{app="{name}"}} {lag or 0}

# TYPE app_stalled gauge
app_stalled{{app="{name}"}} {stalled}

# TYPE app_last_seen_timestamp gauge
app_last_seen_timestamp{{app="{name}"}} {now}
"""

    path = METRICS_DIR / f"{name}.prom"
    tmp = path.with_suffix(".tmp")

    tmp.write_text(content.strip() + "\n")
    tmp.replace(path)


def main():

    apps = load_config()

    while True:

        for app in apps:
            write_metrics(app)

        time.sleep(10)


if __name__ == "__main__":
    main()