"""Exercise the deployed fixture stack through the public client contract."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "client"))
from session import ClientError, Session


SERVER = os.environ.get("GOBACK_TEST_SERVER", "http://127.0.0.1:8080")
TOKEN = os.environ.get("GOBACK_CLIENT_TOKEN", "goback-local-demo")
INITIAL = {"port": 8080, "workers": 2}
EDIT = "Change the port in config.json to 9090"


class Runtime(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="goback-test-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "work"
        self.folder.mkdir()
        self.config = self.folder / "config.json"
        self.config.write_text(json.dumps(INITIAL), encoding="utf-8")
        self.session = Session(SERVER, TOKEN)
        self.addAsyncCleanup(self.session.close)

    async def events_until(self, event_type, after=0, run_id=None):
        collected = []
        stream = self.session.events(after=after)
        try:
            async with asyncio.timeout(45):
                async for event in stream:
                    collected.append(event)
                    if event["type"] == event_type and (run_id is None or event.get("run_id") == run_id):
                        return collected
        finally:
            await stream.aclose()
        self.fail(f"Stream ended before {event_type}")

    async def edit(self):
        run = await self.session.send(EDIT)
        events = await self.events_until("run_finished", run_id=run["run_id"])
        self.assertEqual(events[-1]["status"], "ok")
        return next(event for event in events if event["type"] == "edit_applied"), events[-1]["seq"]

    async def test_two_way_edit_undo_and_event_replay(self):
        await self.session.mount(self.folder, "rw")
        edit, cursor = await self.edit()
        self.assertEqual(json.loads(self.config.read_text()), {"port": 9090, "workers": 2})
        self.config.write_text('{"port":9090,"workers":3}', encoding="utf-8")
        first = await self.session.undo(edit["edit_id"], request_id="retry")
        second = await self.session.undo(edit["edit_id"], request_id="retry")
        self.assertEqual(first, second)
        events = await self.events_until("undo_applied", after=cursor)
        self.assertEqual([e["type"] for e in events], ["tool_result", "file_changed", "undo_applied"])
        self.assertEqual([e["seq"] for e in events], list(range(cursor + 1, cursor + 4)))
        self.assertEqual(json.loads(self.config.read_text()), {"port": 8080, "workers": 3})

    async def test_same_field_conflict(self):
        await self.session.mount(self.folder, "rw")
        edit, cursor = await self.edit()
        self.config.write_text('{"port":7070,"workers":2}', encoding="utf-8")
        response = await self.session.undo(edit["edit_id"], request_id="conflict")
        facts = {"status": "conflict", "reason": "field_changed", "expected": 9090, "current": 7070, "restore": 8080}
        self.assertEqual({key: response[key] for key in facts}, facts)
        events = await self.events_until("run_finished", after=cursor, run_id=response["run_id"])
        self.assertIn("file_changed", [e["type"] for e in events])
        conflict = next(e for e in events if e["type"] == "undo_conflict")
        self.assertEqual({key: conflict[key] for key in facts}, facts)
        self.assertEqual(events[-1]["status"], "ok")
        explanation = "".join(e["text"] for e in events if e["type"] == "agent_delta")
        for value in ("config.json", "7070", "9090", "8080"):
            self.assertIn(value, explanation)
        self.assertNotIn("tool_started", [e["type"] for e in events])
        self.assertEqual(await self.session.undo(edit["edit_id"], request_id="conflict"), response)
        self.assertEqual(json.loads(self.config.read_text())["port"], 7070)

    async def test_read_only(self):
        await self.session.mount(self.folder, "ro")
        await self.session.send(EDIT)
        events = await self.events_until("run_finished")
        self.assertIn("read_only", [e.get("status") for e in events])
        self.assertNotIn("edit_applied", [e["type"] for e in events])
        self.assertEqual(json.loads(self.config.read_text()), INITIAL)

    async def test_container_boundaries(self):
        outside = self.folder.parent / "canary.txt"
        outside.write_text("outside")
        if os.name != "nt":
            (self.folder / "escape").symlink_to(outside)
        dot_git = self.folder / ".git"
        dot_git.mkdir()
        (dot_git / "canary").write_text("private")
        await self.session.mount(self.folder, "rw")
        await self.session.send("[fixture:isolation]")
        events = await self.events_until("run_finished")
        self.assertEqual([e["status"] for e in events if e["type"] == "tool_result"], ["ok"])
        self.assertEqual((self.folder / "isolation.txt").read_text(), "ok")
        await self.session.send("[fixture:limits]")
        events = await self.events_until("run_finished", after=events[-1]["seq"])
        self.assertEqual([e["status"] for e in events if e["type"] == "tool_result"], ["ok"])
        self.assertEqual((self.folder / "limits.txt").read_text(), "ok")

    async def test_cancel_kills_background_command(self):
        await self.session.mount(self.folder, "rw")
        await self.session.send("[fixture:slow]")
        events = await self.events_until("tool_started")
        await asyncio.sleep(0.8)
        await self.session.cancel()
        tail = await self.events_until("run_finished", after=events[-1]["seq"])
        self.assertEqual(tail[-1]["status"], "cancelled")
        await asyncio.sleep(5.2)
        self.assertFalse((self.folder / "escaped-process.txt").exists())
        await self.edit()

    async def test_time_limit(self):
        await self.session.mount(self.folder, "rw")
        await self.session.send("[fixture:timeout]")
        events = await self.events_until("run_finished")
        self.assertEqual([e["status"] for e in events if e["type"] == "tool_result"], ["timeout"])

    async def test_workdir_disconnect(self):
        await self.session.mount(self.folder, "rw")
        await self.session.workdir.close()
        await asyncio.sleep(0.3)
        await self.session.send(EDIT)
        events = await self.events_until("run_finished")
        self.assertIn("workdir_disconnected", [e["type"] for e in events])
        self.assertEqual(json.loads(self.config.read_text()), INITIAL)

    async def test_bad_client_token(self):
        unknown = Session(SERVER, "invalid-token")
        try:
            with self.assertRaisesRegex(ClientError, "Invalid client token"):
                await unknown.authenticate()
        finally:
            await unknown.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
