"""Authenticate the desktop client and coordinate session lifecycle."""

import hmac
import os

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


app = FastAPI(title="GoBack client gateway", version="0")
HARNESS = os.environ.get("HARNESS_URL", "http://harness:8000")
MANAGER = os.environ.get("SANDBOX_URL", "http://sandbox-manager:8000")
SSH_PORT = int(os.environ.get("GOBACK_SSH_PORT", "2222"))
TOKENS = [token.strip() for token in os.environ.get("CLIENT_TOKENS", "").split(",") if token.strip()]
SESSIONS = {}


class MountRequest(BaseModel):
    mode: str
    host_key: str


def authorize(authorization: str = Header(default="")):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(401, "A client bearer token is required")
    for index, accepted in enumerate(TOKENS):
        if hmac.compare_digest(token.encode(), accepted.encode()):
            return index
    raise HTTPException(401, "Invalid client token")


def owned_session(session_id, owner):
    record = SESSIONS.get(session_id)
    if record is None or record["owner"] != owner:
        raise HTTPException(404, "Unknown session")
    return record


async def upstream(method, url, data=None):
    try:
        async with httpx.AsyncClient(timeout=65) as client:
            response = await client.request(method, url, json=data)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as error:
        try:
            detail = error.response.json().get("detail")
        except ValueError:
            detail = None
        raise HTTPException(error.response.status_code, detail or "Service request failed") from None
    except httpx.HTTPError:
        raise HTTPException(502, "Internal service unavailable") from None


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/v0/auth")
async def auth(_owner=Depends(authorize)):
    return {"v": 0, "status": "ok"}


@app.post("/v0/sessions")
async def prepare(body: MountRequest, owner=Depends(authorize)):
    session = await upstream("POST", f"{MANAGER}/v0/sessions", body.model_dump())
    SESSIONS[session["session_id"]] = {"owner": owner, "mode": body.mode}
    return {**session, "ssh_port": SSH_PORT}


@app.post("/v0/sessions/{session_id}/mount")
async def mount(session_id: str, owner=Depends(authorize)):
    session = owned_session(session_id, owner)
    await upstream("POST", f"{MANAGER}/v0/sessions/{session_id}/mount")
    return await upstream("POST", f"{HARNESS}/v0/sessions/{session_id}", {"mode": session["mode"]})


@app.post("/v0/sessions/{session_id}/messages")
async def message(session_id: str, request: Request, owner=Depends(authorize)):
    owned_session(session_id, owner)
    return await upstream("POST", f"{HARNESS}/v0/sessions/{session_id}/messages", await request.json())


@app.post("/v0/sessions/{session_id}/edits/{edit_id}/undo")
async def undo(session_id: str, edit_id: str, request: Request, owner=Depends(authorize)):
    owned_session(session_id, owner)
    return await upstream("POST", f"{HARNESS}/v0/sessions/{session_id}/edits/{edit_id}/undo", await request.json())


@app.post("/v0/sessions/{session_id}/cancel")
async def cancel(session_id: str, owner=Depends(authorize)):
    owned_session(session_id, owner)
    return await upstream("POST", f"{HARNESS}/v0/sessions/{session_id}/cancel")


@app.get("/v0/sessions/{session_id}/events")
async def events(session_id: str, after: int = 0, owner=Depends(authorize)):
    owned_session(session_id, owner)
    client = httpx.AsyncClient(timeout=httpx.Timeout(15, read=None))
    request = client.build_request("GET", f"{HARNESS}/v0/sessions/{session_id}/events", params={"after": after})
    try:
        response = await client.send(request, stream=True)
        response.raise_for_status()
    except (httpx.HTTPError, ValueError):
        await client.aclose()
        raise HTTPException(502, "Unable to open the event stream") from None

    async def stream():
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.delete("/v0/sessions/{session_id}")
async def close(session_id: str, owner=Depends(authorize)):
    owned_session(session_id, owner)
    await upstream("DELETE", f"{MANAGER}/v0/sessions/{session_id}")
    await upstream("DELETE", f"{HARNESS}/v0/sessions/{session_id}")
    SESSIONS.pop(session_id, None)
    return {"v": 0, "status": "ok"}
