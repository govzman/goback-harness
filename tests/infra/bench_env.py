"""Sample GoBack filesystem latency and container overhead on a synthetic folder."""

import argparse
import asyncio
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "client"))
from session import Session


FS_WORKER = """\
import json, os, time
from pathlib import Path

small = Path('tree/item-0.bin')
large = Path('large.bin')
scratch = Path('scratch.bin')
actions = {
    'stat_ms': small.stat,
    'tree_ms': lambda: list(os.walk('tree')),
    'read_small_ms': small.read_bytes,
    'read_1mib_ms': large.read_bytes,
    'write_4kib_ms': lambda: scratch.write_bytes(b'x' * 4096),
}
samples = {key: [] for key in actions}
for _ in range(__SAMPLES__):
    for key, action in actions.items():
        started = time.perf_counter_ns()
        action()
        samples[key].append((time.perf_counter_ns() - started) / 1_000_000)
Path('fs_samples.json').write_text(json.dumps(samples))
"""


async def turn(session, fixture, cursor):
    request = await session.send(f"[fixture:{fixture}]")
    collected = []
    async with asyncio.timeout(100):
        async for event in session.events(after=cursor):
            if event["type"] == "tool_result":
                collected.append(event)
            if event["type"] == "run_finished" and event["run_id"] == request["run_id"]:
                if len(collected) != 1 or collected[0]["status"] != "ok":
                    raise RuntimeError(f"Fixture command failed: {fixture}, {collected}")
                return collected[0]["duration_ms"], event["seq"]
    raise RuntimeError("Benchmark event stream ended early")


def memory_sample(session_id):
    distro = os.environ.get("GOBACK_CHECKER_WSL")
    prefix = ["wsl.exe", "-d", distro, "--"] if os.name == "nt" and distro else []
    command = [*prefix, "docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", f"goback-{session_id}"]
    output = subprocess.check_output(command, text=True, timeout=20).split("/", 1)[0].strip()
    match = re.fullmatch(r"([\d.]+)\s*(B|KiB|MiB|GiB)", output)
    if not match:
        raise ValueError("Cannot parse Docker memory usage")
    factors = {"B": 1 / (1024 * 1024), "KiB": 1 / 1024, "MiB": 1, "GiB": 1024}
    return float(match[1]) * factors[match[2]]


async def bench(args):
    samples = {}
    with tempfile.TemporaryDirectory(prefix="goback-bench-") as folder:
        folder = Path(folder)
        if args.only in {"all", "fs"}:
            (folder / "tree").mkdir()
            for index in range(4):
                (folder / "tree" / f"item-{index}.bin").write_bytes(b"a" * 1024)
            (folder / "large.bin").write_bytes(b"b" * 1024 * 1024)
            (folder / "bench_fs_worker.py").write_text(FS_WORKER.replace("__SAMPLES__", str(args.samples)))
            session = Session(args.server, args.token)
            try:
                await session.mount(folder, "rw")
                await turn(session, "bench_fs", 0)
                samples.update(json.loads((folder / "fs_samples.json").read_text()))
            finally:
                await session.close()
        if args.only in {"all", "sandbox"}:
            samples.update(mount_and_start_ms=[], exec_api_ms=[], sandbox_memory_mib=[])
            for _ in range(args.samples):
                session = Session(args.server, args.token)
                try:
                    start = time.perf_counter_ns()
                    sid = await session.mount(folder, "rw")
                    samples["mount_and_start_ms"].append((time.perf_counter_ns() - start) / 1_000_000)
                    samples["sandbox_memory_mib"].append(await asyncio.to_thread(memory_sample, sid))
                    execution, _ = await turn(session, "bench_exec", 0)
                    samples["exec_api_ms"].append(execution)
                finally:
                    await session.close()
    return {key: {"n": len(values), "median": round(statistics.median(values), 2),
                  "p95": round(sorted(values)[math.ceil(0.95 * len(values)) - 1], 2),
                  "samples": [round(value, 3) for value in values]}
            for key, values in samples.items()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=os.environ.get("GOBACK_TEST_SERVER", "http://127.0.0.1:8080"))
    parser.add_argument("--token", default=os.environ.get("GOBACK_CLIENT_TOKEN", "goback-local-demo"))
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--only", choices=("all", "fs", "sandbox"), default="all")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")
    output = json.dumps(asyncio.run(bench(args)), indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output, end="")
