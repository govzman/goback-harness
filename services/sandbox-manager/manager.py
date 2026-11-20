"""Trusted mount and container lifecycle service."""

import asyncio
import hmac
import json
import os
import secrets
import socket
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import asyncssh
import docker
from docker.errors import NotFound
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


INSTANCE = os.environ.get("GOBACK_INSTANCE", "goback")
ROOT = Path("/run/goback-harness") / INSTANCE
SESSIONS = {}
RELAY_KEY = asyncssh.generate_private_key("ssh-ed25519")
ENGINE = docker.from_env(timeout=45)
SANDBOX_IMAGE = os.environ.get("SANDBOX_IMAGE", "goback-harness-sandbox:dev")
OWNER = "io.goback-harness.sandbox"


@dataclass
class Session:
    id: str
    directory: Path
    port: int
    ticket: str
    mode: str
    ssh: object = None
    mount: object = None
    container: object = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    results: dict = field(default_factory=dict)
    cancelled: set = field(default_factory=set)
    absent_since: float = field(default_factory=time.monotonic)

    @property
    def workdir(self):
        return self.directory / "work"


class Relay(asyncssh.SSHServer):
    def connection_made(self, connection):
        self.connection = connection
        self.session = None

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        session = SESSIONS.get(username)
        if session and not session.ssh and hmac.compare_digest(session.ticket, password):
            self.session = session
            session.ssh = self.connection
            session.absent_since = None
            return True
        return False

    def connection_lost(self, _error):
        if self.session and self.session.ssh is self.connection:
            self.session.ssh = None
            self.session.absent_since = time.monotonic()

    def server_requested(self, listen_host, listen_port):
        return bool(self.session and listen_host == "127.0.0.1" and listen_port == self.session.port)


async def remove_container(session):
    if session.container:
        current = session.container
        try:
            await asyncio.to_thread(current.remove, force=True)
        except NotFound:
            pass
        if session.container is current:
            session.container = None


async def clear_mount(directory):
    workdir = directory / "work"
    unmount = await asyncio.create_subprocess_exec("umount", "-l", str(workdir),
                                                   stderr=asyncio.subprocess.DEVNULL)
    await unmount.wait()
    try:
        workdir.rmdir()
    except FileNotFoundError:
        pass
    for entry in ("identity", "known_hosts"):
        (directory / entry).unlink(missing_ok=True)
    directory.rmdir()


async def cleanup(session):
    await remove_container(session)
    if session.ssh:
        session.ssh.close()
    if session.mount is not None and session.mount.returncode is None:
        session.mount.terminate()
        try:
            await asyncio.wait_for(session.mount.wait(), timeout=5)
        except TimeoutError:
            session.mount.kill()
            await session.mount.wait()
    session.mount = None
    await clear_mount(session.directory)


async def reap_disconnected():
    while True:
        await asyncio.sleep(5)
        for session in list(SESSIONS.values()):
            if session.absent_since is None or time.monotonic() - session.absent_since < 45:
                continue
            if SESSIONS.pop(session.id, None) is session:
                await remove_container(session)
                async with session.lock:
                    await cleanup(session)


@asynccontextmanager
async def lifespan(_app):
    # A manager restart ends sessions. Remove containers left behind by a crash.
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    stale = await asyncio.to_thread(ENGINE.containers.list, all=True, filters={"label": f"{OWNER}={INSTANCE}"})
    for container in stale:
        await asyncio.to_thread(container.remove, force=True)
    for entry in ROOT.iterdir():
        if len(entry.name) == 32 and all(c in "0123456789abcdef" for c in entry.name):
            await clear_mount(entry)
    relay = await asyncssh.listen("0.0.0.0", 2222, server_factory=Relay, server_host_keys=[RELAY_KEY],
                                  login_timeout=10)
    reaper = asyncio.create_task(reap_disconnected())
    try:
        yield
    finally:
        reaper.cancel()
        await asyncio.gather(reaper, return_exceptions=True)
        relay.close()
        await relay.wait_closed()
        for session in list(SESSIONS.values()):
            await cleanup(session)


app = FastAPI(title="GoBack sandbox manager", version="0", lifespan=lifespan)


def find_session(session_id):
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(404, "Unknown sandbox session")
    return session


def require_mount(session):
    if session.mount is None or session.mount.returncode is not None or not session.ssh:
        raise HTTPException(409, "Workdir connection is unavailable")


def free_port():
    while True:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        if all(session.port != port for session in SESSIONS.values()):
            return port


async def start_container(session):
    if session.container:
        return session.container
    mount = docker.types.Mount("/work", str(session.workdir), type="bind", read_only=session.mode == "ro")
    session.container = await asyncio.to_thread(
        ENGINE.containers.run, SANDBOX_IMAGE, detach=True, init=True,
        name=f"goback-{session.id}", labels={OWNER: INSTANCE},
        user="65532:65532", network_mode="none", read_only=True, cap_drop=["ALL"],
        security_opt=["no-new-privileges"], pids_limit=int(os.environ.get("SANDBOX_PIDS", "64")),
        nano_cpus=int(float(os.environ.get("SANDBOX_CPU", "1")) * 1_000_000_000),
        mem_limit=os.environ.get("SANDBOX_MEMORY", "256m"),
        tmpfs={"/tmp": "rw,nosuid,nodev,noexec,mode=1777,size=" + os.environ.get("SANDBOX_DISK", "64m")},
        mounts=[mount], environment={"HOME": "/tmp", "TMPDIR": "/tmp", "PYTHONDONTWRITEBYTECODE": "1"},
        log_config=docker.types.LogConfig(type="none"),
    )
    return session.container


class Prepare(BaseModel):
    mode: str
    host_key: str


class Execution(BaseModel):
    id: str
    run_id: str
    operation: dict


class Cancel(BaseModel):
    run_id: str


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/v0/sessions")
async def prepare(body: Prepare):
    if body.mode not in {"ro", "rw"}:
        raise HTTPException(422, "Expected ro or rw")
    try:
        client_key = asyncssh.import_public_key(body.host_key).export_public_key().decode().strip()
    except (ValueError, asyncssh.KeyImportError):
        raise HTTPException(422, "Invalid SFTP host key") from None
    if len(SESSIONS) >= 8:
        raise HTTPException(503, "Too many open sessions")
    session_id = uuid.uuid4().hex
    session = Session(session_id, ROOT / session_id, free_port(), secrets.token_urlsafe(32), body.mode)
    session.directory.mkdir(mode=0o700)
    session.workdir.mkdir()
    mount_key = asyncssh.generate_private_key("ssh-ed25519")
    identity = session.directory / "identity"
    identity.touch(mode=0o600)
    identity.write_bytes(mount_key.export_private_key())
    (session.directory / "known_hosts").write_text(f"[127.0.0.1]:{session.port} {client_key}\n")
    SESSIONS[session_id] = session
    return {"v": 0, "session_id": session_id, "relay_port": session.port, "ticket": session.ticket,
            "relay_host_key": RELAY_KEY.export_public_key().decode().strip(),
            "sftp_authorized_key": mount_key.export_public_key().decode().strip()}


@app.post("/v0/sessions/{session_id}/mount")
async def mount(session_id: str):
    session = find_session(session_id)
    async with session.lock:
        if session.mount is not None:
            require_mount(session)
            return {"v": 0, "status": "ok"}
        if not session.ssh:
            raise HTTPException(409, "Connect the SSH tunnel before mounting")
        options = [
            "allow_other", "default_permissions", "uid=65532", "gid=65532", "umask=0077",
            "dir_cache=no", "direct_io", "attr_timeout=0", "entry_timeout=0", "negative_timeout=0",
            "StrictHostKeyChecking=yes", f"UserKnownHostsFile={session.directory / 'known_hosts'}",
            f"IdentityFile={session.directory / 'identity'}", "IdentitiesOnly=yes", "BatchMode=yes",
            "ConnectTimeout=5", "ServerAliveInterval=2", "ServerAliveCountMax=2",
        ]
        if session.mode == "ro":
            options.append("ro")
        session.mount = await asyncio.create_subprocess_exec(
            "sshfs", "-f", "-p", str(session.port), "-o", ",".join(options),
            "workdir@127.0.0.1:/", str(session.workdir),
            stdin=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        try:
            for _ in range(80):
                if session.mount.returncode is not None:
                    message = await session.mount.stderr.read()
                    raise HTTPException(502, message.decode(errors="replace").strip() or "SSHFS exited")
                if await asyncio.to_thread(os.path.ismount, session.workdir):
                    await start_container(session)
                    return {"v": 0, "status": "ok"}
                await asyncio.sleep(0.1)
            raise HTTPException(504, "Timed out creating SSHFS mount")
        except BaseException:
            SESSIONS.pop(session_id, None)
            await cleanup(session)
            raise


async def operation_result(session, body):
    async with session.lock:
        if body.id in session.results:
            return session.results[body.id]
        if body.run_id in session.cancelled:
            return {"v": 0, "type": "result", "id": body.id, "status": "cancelled"}
        require_mount(session)
        operation = body.operation
        if operation.get("op") in {"write", "set_json", "undo"} and session.mode != "rw":
            raise HTTPException(403, "Workdir is read-only")
        if len(session.results) >= 512:
            raise HTTPException(429, "Session operation limit reached")
        maximum = int(os.environ.get("SANDBOX_EXEC_TIMEOUT_S", "20"))
        operation["timeout"] = maximum
        request = json.dumps(operation, ensure_ascii=False)
        if len(request.encode()) > 90_000:
            raise HTTPException(413, "Operation exceeds the protocol payload limit")
        sandbox = await start_container(session)
        try:
            code, data = await asyncio.wait_for(
                asyncio.to_thread(sandbox.exec_run, ["python", "/opt/goback/worker.py", request], workdir="/work"),
                timeout=maximum + 5,
            )
            result = json.loads(data) if code == 0 else {"status": "sandbox_error"}
            if result.get("status") == "timeout":
                await remove_container(session)
        except TimeoutError:
            await remove_container(session)
            result = {"status": "timeout"}
        except Exception:
            result = {"status": "cancelled" if session.container is not sandbox else "sandbox_error"}
        envelope = {"v": 0, "type": "result", "id": body.id, **result}
        session.results[body.id] = envelope
        return envelope


@app.post("/v0/sessions/{session_id}/exec")
async def execute(session_id: str, body: Execution):
    session = find_session(session_id)

    async def events():
        task = asyncio.create_task(operation_result(session, body))
        yield json.dumps({"v": 0, "type": "accepted", "id": body.id}) + "\n"
        try:
            result = await asyncio.shield(task)
        except HTTPException as error:
            status = {403: "read_only", 409: "workdir_disconnected", 413: "invalid_operation",
                      429: "limit_reached"}.get(error.status_code, "sandbox_error")
            result = {"v": 0, "type": "result", "id": body.id, "status": status}
        except Exception:
            result = {"v": 0, "type": "result", "id": body.id, "status": "sandbox_error"}
        yield json.dumps(result, ensure_ascii=False) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.post("/v0/sessions/{session_id}/cancel")
async def cancel(session_id: str, body: Cancel):
    session = find_session(session_id)
    session.cancelled.add(body.run_id)
    await remove_container(session)
    return {"v": 0, "status": "ok"}


@app.get("/v0/sessions/{session_id}/collect")
async def collect(session_id: str):
    session = find_session(session_id)
    return {"v": 0, "operations": len(session.results)}


@app.delete("/v0/sessions/{session_id}")
async def release(session_id: str):
    session = SESSIONS.pop(session_id, None)
    if session:
        await remove_container(session)
        async with session.lock:
            await cleanup(session)
    return {"v": 0, "status": "ok"}
