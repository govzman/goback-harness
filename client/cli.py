"""Headless demo and the newline-delimited JSON bridge for the desktop client."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from session import Session, pump_events


def emit(event):
    print(json.dumps(event, ensure_ascii=True), flush=True)


async def bridge():
    session = None
    event_task = None
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            command = json.loads(line)
            reply = {"type": "reply", "id": command["id"]}
            try:
                action = command["action"]
                if action == "connect":
                    if session:
                        await session.close()
                    session = Session(command["server"], command["token"])
                    await session.authenticate()
                elif session is None:
                    raise ValueError("Connect to the server first")
                elif action == "mount":
                    await session.mount(command["folder"], command["mode"])
                    event_task = asyncio.create_task(pump_events(session, emit))
                elif action == "send":
                    reply.update(await session.send(command["text"]))
                elif action == "undo":
                    reply.update(await session.undo(command["edit_id"]))
                elif action == "cancel":
                    await session.cancel()
                elif action == "disconnect":
                    if event_task:
                        event_task.cancel()
                        await asyncio.gather(event_task, return_exceptions=True)
                        event_task = None
                    await session.close()
                    session = None
                else:
                    raise ValueError("Unknown request")
                emit({**reply, "ok": True})
            except Exception as error:
                emit({**reply, "ok": False, "error": str(error)})
    finally:
        if event_task:
            event_task.cancel()
            await asyncio.gather(event_task, return_exceptions=True)
        if session:
            await session.close()


async def run(args):
    session = Session(args.server, args.token)
    try:
        await session.authenticate()
        await session.mount(args.folder, args.mode)
        request = await session.send(args.message)
        edit = None
        cursor = 0
        async with asyncio.timeout(195):
            async for event in session.events():
                emit(event)
                cursor = event["seq"]
                if event["type"] == "edit_applied":
                    edit = event
                if event["type"] == "run_finished" and event["run_id"] == request["run_id"]:
                    if event["status"] != "ok":
                        return 1
                    break
        if args.demo:
            if edit is None:
                raise RuntimeError("The demo requires a tracked JSON edit")
            config = Path(args.folder) / "config.json"
            document = json.loads(config.read_text(encoding="utf-8"))
            if document["port"] != 9090:
                raise RuntimeError("The sandbox edit was not visible on the client")
            document["workers" if args.demo == "neighbor" else "port"] = 3 if args.demo == "neighbor" else 7070
            config.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
            resolution = await session.undo(edit["edit_id"])
            wanted = "undo_applied" if args.demo == "neighbor" else "undo_conflict"
            explanation = resolution.get("run_id")
            async with asyncio.timeout(25):
                async for event in session.events(after=cursor):
                    emit(event)
                    if event["type"] in {"undo_applied", "undo_conflict", "undo_failed"}:
                        if event["type"] != wanted:
                            raise RuntimeError(f"Unexpected undo result: {resolution['status']}")
                        if not explanation:
                            break
                    if explanation and event["type"] == "run_finished" and event["run_id"] == explanation:
                        break
            actual = json.loads(config.read_text(encoding="utf-8"))
            expected = {"port": 8080, "workers": 3} if args.demo == "neighbor" else {"port": 7070, "workers": 2}
            if actual != expected:
                raise RuntimeError(f"Unexpected client file: {actual}")
            emit({"type": "demo_verified", "scenario": args.demo, "file": actual})
        return 0
    finally:
        await session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("bridge", help="control a client over stdin/stdout")
    invoke = actions.add_parser("run", help="mount a folder, run an agent turn and print events")
    invoke.add_argument("--server", default="http://127.0.0.1:8080")
    invoke.add_argument("--token", default=os.environ.get("GOBACK_CLIENT_TOKEN", "goback-local-demo"))
    invoke.add_argument("--folder", required=True)
    invoke.add_argument("--mode", choices=("ro", "rw"), default="ro")
    invoke.add_argument("--message", required=True)
    invoke.add_argument("--demo", choices=("neighbor", "conflict"))
    args = parser.parse_args()
    if args.action == "bridge":
        asyncio.run(bridge())
        return 0
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
