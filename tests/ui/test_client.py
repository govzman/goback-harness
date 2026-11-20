"""Headless client interaction and stream replay against a mock gateway."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "client"))
from session import ClientError, Session


class ClientFlow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.client = Session("http://mock.invalid", "test-only")
        await self.client.http.aclose()
        self.client.http = httpx.AsyncClient(
            base_url="http://mock.invalid", transport=httpx.MockTransport(self.gateway),
            headers={"Authorization": "Bearer test-only"},
        )
        self.addAsyncCleanup(self.client.close)

    def gateway(self, request):
        self.calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.headers.get("Authorization") != "Bearer test-only":
            return httpx.Response(401, json={"detail": "Invalid client token"})
        if request.url.path == "/v0/sessions":
            body = json.loads(request.content)
            assert body["mode"] == "rw"
            return httpx.Response(200, json={"session_id": "ui-test", "ssh_port": 2222})
        if request.url.path.endswith("/events"):
            after = int(request.url.params["after"])
            event = {"v": 0, "seq": after + 1, "type": "agent_delta", "text": "Проверено"}
            return httpx.Response(200, text=f"id: {after + 1}\ndata: {json.dumps(event)}\n\n")
        return httpx.Response(200, json={"v": 0, "status": "ok"})

    @patch("session.Workdir")
    async def test_connect_select_mount_send_cancel_and_resume(self, workdir):
        workdir.return_value.connect = AsyncMock()
        workdir.return_value.close = AsyncMock()
        workdir.return_value.exported_key.return_value = "test-host-key"
        await self.client.authenticate()
        await self.client.mount("test-folder", "rw")
        workdir.assert_called_once_with("test-folder", "rw")
        await self.client.send("Read a file", request_id="message-1")
        first = [event async for event in self.client.events()]
        await self.client.cancel()
        second = [event async for event in self.client.events(after=first[0]["seq"])]
        await self.client.close_mount()
        self.assertEqual((first[0]["text"], second[0]["seq"]), ("Проверено", 2))
        self.assertEqual(self.calls, [
            ("GET", "/v0/auth", {}), ("POST", "/v0/sessions", {}), ("POST", "/v0/sessions/ui-test/mount", {}),
            ("POST", "/v0/sessions/ui-test/messages", {}), ("GET", "/v0/sessions/ui-test/events", {"after": "0"}),
            ("POST", "/v0/sessions/ui-test/cancel", {}), ("GET", "/v0/sessions/ui-test/events", {"after": "1"}),
            ("DELETE", "/v0/sessions/ui-test", {}),
        ])
        workdir.return_value.close.assert_awaited_once()

    async def test_failed_auth_is_visible(self):
        self.client.http.headers["Authorization"] = "Bearer wrong"
        with self.assertRaisesRegex(ClientError, "Invalid client token"):
            await self.client.authenticate()


if __name__ == "__main__":
    unittest.main(verbosity=2)
