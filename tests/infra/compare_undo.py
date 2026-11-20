"""Compare three undo policies on identical bytes from the mounted-workdir demo."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "client"))
from session import Session


INITIAL = {"port": 8080, "workers": 2, "debug": False, "retries": 1}
SCENARIOS = {
    "clean": ({}, "applied"),
    "neighbor": ({"workers": 3}, "applied"),
    "distant": ({"retries": 4}, "applied"),
    "same_field": ({"port": 7070}, "refused"),
    "deleted_field": (None, "refused"),
    "malformed": (b'{"port":9090,', "refused"),
}


def encode(document):
    return (json.dumps(document, indent=2) + "\n").encode()


def merge(path, before, after, current):
    for filename, contents in (("before", before), ("after", after), ("current", current)):
        (path / filename).write_bytes(contents)
    result = subprocess.run(["git", "merge-file", "-p", "current", "after", "before"],
                            cwd=path, capture_output=True, timeout=10, check=False)
    if result.returncode < 0 or result.returncode == 255:
        raise RuntimeError("git merge-file failed to run")
    return ("applied", result.stdout) if result.returncode == 0 else ("refused", current)


def matches(output, expected):
    try:
        return json.loads(output) == json.loads(expected)
    except ValueError:
        return output == expected


async def scenario(args, name, change, wanted):
    with tempfile.TemporaryDirectory(prefix="goback-undo-") as folder:
        root = Path(folder)
        workdir = root / "workdir"
        workdir.mkdir()
        config = workdir / "config.json"
        before = encode(INITIAL)
        config.write_bytes(before)
        session = Session(args.server, args.token)
        try:
            await session.mount(workdir, "rw")
            await session.send("Change port in config.json to 9090")
            edit = None
            async with asyncio.timeout(40):
                async for event in session.events():
                    if event["type"] == "edit_applied":
                        edit = event
                    if event["type"] == "run_finished":
                        break
            if edit is None:
                raise RuntimeError("Fixture did not produce a tracked edit")
            after = config.read_bytes()
            if isinstance(change, bytes):
                current = change
            elif change is None:
                current = encode({key: value for key, value in json.loads(after).items() if key != "port"})
            elif change:
                current = encode({**json.loads(after), **change})
            else:
                current = after
            config.write_bytes(current)
            expected = encode({**json.loads(current), "port": INITIAL["port"]}) if wanted == "applied" else current
            assertion = await session.undo(edit["edit_id"])
            status = {"ok": "applied", "conflict": "refused"}.get(assertion["status"], assertion["status"])
            result = {
                "guarded_file_restore": ("applied", before) if current == after else ("refused", current),
                "text_three_way": merge(root, before, after, current),
                "goback_json_field": (status, config.read_bytes()),
            }
            return [{"scenario": name, "policy": policy, "outcome": outcome, "expected": wanted,
                     "correct": outcome == wanted and matches(output, expected)}
                    for policy, (outcome, output) in result.items()]
        finally:
            await session.close()


async def main(args):
    rows = []
    for name, (change, outcome) in SCENARIOS.items():
        rows.extend(await scenario(args, name, change, outcome))
    output = "".join(json.dumps(row) + "\n" for row in rows)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output, end="")
    return int(any(not row["correct"] for row in rows if row["policy"] == "goback_json_field"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:8080")
    parser.add_argument("--token", default=os.environ.get("GOBACK_CLIENT_TOKEN", "goback-local-demo"))
    parser.add_argument("--out", type=Path)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
