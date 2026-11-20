"""Run course fixtures in fresh mounted directories and grade client-side artifacts."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import yaml

from full_cases import ALL_CASES
from smoke_cases import CASES
from smoke_cases import Yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
from session import Session


def verify_code(path, code):
    """Use a second local networkless sandbox to test generated code."""
    distro = os.environ.get("GOBACK_CHECKER_WSL")
    prefix = ["wsl.exe", "-d", distro, "--"] if distro and os.name == "nt" else []
    docker = [*prefix, "docker"]

    def engine_path(location):
        if prefix:
            return subprocess.check_output([*prefix, "wslpath", "-a", location.as_posix()],
                                           text=True, timeout=12).strip()
        return str(location)

    with tempfile.TemporaryDirectory(prefix="goback-checker-") as folder:
        checker = Path(folder) / "check.py"
        checker.write_text("import sys\nsys.path.insert(0, '/work')\n" + code, encoding="utf-8")
        path.chmod(0o755)
        name = "goback-check-" + uuid.uuid4().hex
        try:
            command = [
                *docker, "run", "--rm", "--name", name, "--network", "none", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--user", "65532:65532", "--memory", "96m",
                "--pids-limit", "16", "--cpus", "0.5", "--env", "PYTHONDONTWRITEBYTECODE=1", "--workdir", "/work",
                "--tmpfs", "/tmp:rw,nosuid,noexec,size=8m", "--mount", f"type=bind,src={engine_path(path)},dst=/work,readonly",
                "--mount", f"type=bind,src={engine_path(checker)},dst=/checker.py,readonly",
                "goback-harness-sandbox:dev", "python", "/checker.py",
            ]
            run = subprocess.run(command, capture_output=True, timeout=35, check=False)
            if run.returncode in (125, 126, 127):
                return False, f"Checker Docker error {run.returncode}"
            return (True, "ok") if run.returncode == 0 else (False, "Generated code failed isolated hidden cases")
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False, "Isolated code checker unavailable"
        except subprocess.TimeoutExpired:
            subprocess.run([*docker, "rm", "-f", name], capture_output=True, timeout=12, check=False)
            return False, "Isolated code checker timed out"


def check(path, case, smoke):
    for relative, expected in case.outputs.items():
        output = path / relative
        if not output.is_file() or output.is_symlink():
            return False, f"Missing artifact {relative}"
        if expected is None:
            continue
        text = output.read_bytes()
        if isinstance(expected, (dict, list)):
            try:
                matches = json.dumps(json.loads(text), sort_keys=True) == json.dumps(expected, sort_keys=True)
            except ValueError:
                matches = False
        elif isinstance(expected, bytes):
            matches = text == expected
        elif isinstance(expected, Yaml):
            try:
                matches = yaml.safe_load(text) == expected.value
            except yaml.YAMLError:
                matches = False
        elif isinstance(expected, set):
            lines = text.decode().splitlines()
            matches = len(lines) == len(expected) and set(lines) == expected
        elif expected.endswith("\n"):
            matches = text == expected.encode()
        else:
            matches = text.decode().strip() == expected
        if not matches:
            return False, f"Incorrect artifact {relative}"
    if case.code_check and not smoke:
        return verify_code(path, case.code_check)
    return True, "ok"


async def trial(case, args, repeat):
    started = time.monotonic()
    tokens = {"input_tokens": 0, "output_tokens": 0}
    models = set()
    with tempfile.TemporaryDirectory(prefix="goback-baf-") as folder:
        path = Path(folder)
        for relative, content in case.files.items():
            target = path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        client = Session(args.server, args.token)
        detail = None
        try:
            async with asyncio.timeout(200):
                await client.mount(path, "rw")
                prompt = f"[fixture:{case.id}]\n{case.prompt}" if args.smoke else case.prompt
                request = await client.send(prompt)
                async for event in client.events():
                    if event["type"] == "model_usage":
                        models.add(event["model"])
                        for key in tokens:
                            tokens[key] += event["usage"].get(key, 0)
                    if event["type"] == "run_error":
                        detail = event["detail"]
                    if event["type"] == "run_finished" and event["run_id"] == request["run_id"]:
                        status = event["status"]
                        break
                else:
                    status = "disconnected"
            if status == "ok":
                if not args.smoke and (not models or "fixture" in models):
                    passed, reason = False, "Live provider not active"
                else:
                    passed, reason = check(path, case, args.smoke)
            else:
                passed, reason = False, detail or status
        except Exception as error:
            passed, reason = False, str(error) or type(error).__name__
        finally:
            await client.close()
    return {"task": case.id, "repeat": repeat, "pair": args.pair, "models": sorted(models), "pass": passed,
            "reason": reason, "duration_ms": round((time.monotonic() - started) * 1000), **tokens}


async def run(args):
    available = CASES if args.smoke else ALL_CASES
    selected = [case for case in available if not args.case or case.id in args.case]
    if args.case and set(args.case) != {case.id for case in selected}:
        raise ValueError("Unknown task")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("", encoding="utf-8")
    failed = False
    for repeat in range(1, args.repeats + 1):
        for case in selected:
            row = await trial(case, args, repeat)
            line = json.dumps(row, ensure_ascii=False)
            print(line, flush=True)
            if args.out:
                with args.out.open("a", encoding="utf-8") as result:
                    result.write(line + "\n")
            failed |= not row["pass"]
            if "subscription_sharing_usage" in row["reason"] or "Model returned HTTP 429" in row["reason"]:
                return 1
    return int(failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:8080")
    parser.add_argument("--token", default=os.environ.get("GOBACK_CLIENT_TOKEN", "goback-local-demo"))
    parser.add_argument("--case", action="append")
    parser.add_argument("--repeats", default=1, type=int)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--pair", default="goback")
    raise SystemExit(asyncio.run(run(parser.parse_args())))
