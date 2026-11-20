"""Gateway client shared by scripts and the desktop bridge."""

import asyncio
import json
import uuid
from urllib.parse import urlsplit

import httpx

from workdir import Workdir


class ClientError(RuntimeError):
    pass


class Session:
    def __init__(self, server, token):
        parsed = urlsplit(server)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ClientError("Specify an HTTP or HTTPS server address")
        self.host = parsed.hostname
        self.http = httpx.AsyncClient(base_url=server.rstrip("/"), timeout=65, trust_env=False,
                                     headers={"Authorization": f"Bearer {token}"})
        self.id = None
        self.workdir = None

    async def request(self, method, path, data=None):
        try:
            response = await self.http.request(method, path, json=data)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            try:
                detail = error.response.json().get("detail")
            except (TypeError, ValueError):
                detail = None
            raise ClientError(str(detail or f"Server returned HTTP {error.response.status_code}")) from None
        except httpx.HTTPError:
            raise ClientError("Cannot reach the GoBack server") from None

    async def authenticate(self):
        await self.request("GET", "/v0/auth")

    async def mount(self, folder, mode):
        if self.id:
            raise ClientError("Close the active session before sharing another folder")
        self.workdir = Workdir(folder, mode)
        info = await self.request("POST", "/v0/sessions", {"mode": mode, "host_key": self.workdir.exported_key()})
        self.id = info["session_id"]
        try:
            await self.workdir.connect(self.host, info["ssh_port"], info)
            await self.request("POST", self.path("mount"))
        except Exception:
            await self.close_mount()
            raise
        return self.id

    def path(self, suffix=""):
        if self.id is None:
            raise ClientError("Choose a folder to start a session")
        return f"/v0/sessions/{self.id}" + (f"/{suffix}" if suffix else "")

    async def send(self, text, request_id=None):
        return await self.request("POST", self.path("messages"), {"id": request_id or uuid.uuid4().hex, "text": text})

    async def undo(self, edit_id, request_id=None):
        return await self.request("POST", self.path(f"edits/{edit_id}/undo"), {"id": request_id or uuid.uuid4().hex})

    async def cancel(self):
        return await self.request("POST", self.path("cancel"))

    async def events(self, after=0, on_open=None):
        async with self.http.stream("GET", self.path("events"), params={"after": after},
                                    timeout=httpx.Timeout(20, read=20)) as response:
            response.raise_for_status()
            if on_open:
                on_open()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    yield json.loads(line[6:])

    async def close_mount(self):
        if self.id:
            try:
                await self.request("DELETE", self.path())
            except ClientError:
                pass
            self.id = None
        if self.workdir:
            await self.workdir.close()
            self.workdir = None

    async def close(self):
        await self.close_mount()
        await self.http.aclose()


async def pump_events(session, emit):
    after = 0
    reconnecting = False

    def opened():
        nonlocal reconnecting
        if reconnecting:
            emit({"type": "stream_up"})
            reconnecting = False

    while session.id:
        try:
            async for event in session.events(after, on_open=opened):
                after = event["seq"]
                emit(event)
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {401, 404}:
                emit({"type": "stream_ended"})
                await session.close_mount()
                return
        except httpx.HTTPError:
            pass
        if not reconnecting:
            emit({"type": "stream_down"})
            reconnecting = True
        await asyncio.sleep(1)
