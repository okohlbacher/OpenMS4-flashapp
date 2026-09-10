"""Track process identity, rather than treating a reusable PID as ownership."""
import json
from pathlib import Path

import psutil


def record_process(directory, pid):
    process = psutil.Process(pid)
    path = Path(directory) / str(pid)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps({"pid": pid, "create_time": process.create_time()}))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def stop_processes(directory, logger, timeout=3):
    """Stop only recorded identities and their children; keep records on failure."""
    directory = Path(directory)
    if not directory.exists():
        return True
    records = {}
    complete = True
    for path in directory.iterdir():
        if path.suffix == ".tmp":
            continue
        try:
            identity = json.loads(path.read_text())
            if (type(identity["pid"]) is not int or identity["pid"] <= 0 or
                    path.name != str(identity["pid"]) or
                    not isinstance(identity["create_time"], (float, int))):
                raise ValueError("Invalid process identity")
            process = psutil.Process(identity["pid"])
            if process.create_time() != identity["create_time"]:
                # The old process is gone. Never signal its replacement.
                path.unlink(missing_ok=True)
                continue
            records[path] = process
        except psutil.NoSuchProcess:
            path.unlink(missing_ok=True)
        except (ValueError, KeyError, OSError, psutil.Error) as error:
            logger.log(f"Cannot verify process record {path.name}: {error}")
            complete = False

    complete = stop_process_trees(records.values(), logger, timeout) and complete
    for path, process in records.items():
        try:
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                path.unlink(missing_ok=True)
        except psutil.NoSuchProcess:
            path.unlink(missing_ok=True)
    if complete:
        try:
            directory.rmdir()
        except FileNotFoundError:
            pass  # The workflow may have removed its now-empty directory.
        except OSError:
            # A concurrent registration must be examined by a subsequent stop.
            complete = False
    return complete


def stop_process_trees(roots, logger, timeout=3):
    """Bounded termination using psutil handles that protect against PID reuse."""
    complete = True
    processes = {}
    for process in roots:
        try:
            processes[(process.pid, process.create_time())] = process
            for child in process.children(recursive=True):
                processes[(child.pid, child.create_time())] = child
        except psutil.NoSuchProcess:
            pass
        except (psutil.Error, OSError) as error:
            logger.log(f"Cannot inspect owned process {process.pid}: {error}")
            complete = False
    for process in reversed(list(processes.values())):
        try:
            process.terminate()  # psutil checks for PID reuse before signalling.
        except psutil.NoSuchProcess:
            pass
        except (psutil.Error, OSError) as error:
            logger.log(f"Cannot stop owned process {process.pid}: {error}")
            complete = False
    _, alive = psutil.wait_procs(list(processes.values()), timeout=timeout)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
        except (psutil.Error, OSError) as error:
            logger.log(f"Cannot kill owned process {process.pid}: {error}")
            complete = False
    _, alive = psutil.wait_procs(alive, timeout=timeout)
    for process in alive:
        try:
            if process.status() != psutil.STATUS_ZOMBIE:
                complete = False
        except psutil.NoSuchProcess:
            pass
    return complete
