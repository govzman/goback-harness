"""A streaming model adapter. Only this service handles provider credentials."""

import asyncio
import hmac
import json
import os
import re
import ssl
import time
from urllib.error import HTTPError

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from chatgpt_login import PLAN_SCOPE, PROFILE, RESOURCE, TOKEN_ENDPOINT, request_json, write_private
from fixtures import SCRIPTS


app = FastAPI(title="GoBack model proxy", version="0")
MODE = os.environ.get("LLM_MODE", "fixture")
MODEL = os.environ.get("LLM_MODEL", "gpt-6.1-terra")
PROFILE_LOCK = asyncio.Lock()
INTERNAL_TOKEN = os.environ.get("GOBACK_PROXY_TOKEN", "")


class Turn(BaseModel):
    instructions: str
    input: list[dict]
    tools: list[dict]


@app.get("/health")
async def health():
    return {"ok": True, "mode": MODE}


def line(event_type, **fields):
    return json.dumps({"type": event_type, **fields}, ensure_ascii=False) + "\n"


def fixture_items(turn):
    """A deterministic demonstration provider. This does not test model quality."""
    user_index = max(i for i, item in enumerate(turn.input) if item.get("role") == "user")
    prompt = turn.input[user_index]["content"]
    marker = re.match(r"^\[fixture:([\w.]+)\]", prompt)
    if marker and marker[1] in SCRIPTS:
        outputs = [item for item in turn.input[user_index + 1:] if item["type"] == "function_call_output"]
        status = json.loads(outputs[-1]["output"])["status"] if outputs else "ok"
        script = SCRIPTS[marker[1]]
        if len(outputs) < len(script) and status == "ok":
            name, arguments = script[len(outputs)]
            return [fixture_call(f"script-{len(outputs)}", name, arguments)]
        return [fixture_message(f"Сценарий завершён · {status}")]
    last = turn.input[-1]
    if last["type"] == "function_call_output":
        response = json.loads(last["output"])
        if last["call_id"] == "demo-read" and response.get("status") == "ok":
            target = re.search(r"\b\d{4,5}\b", prompt)
            return [fixture_call("demo-edit", "set_json_field", {
                "path": "config.json", "address": ["port"], "value": int(target[0]) if target else 9090,
                "version": response["version"],
            })]
        status = response.get("status")
        message = "Операция выполнена." if status == "ok" else f"Операция завершилась со статусом {status}."
        return [fixture_message(message)]
    text = last.get("content", "")
    if "port" in text.casefold() or "порт" in text.casefold():
        return [fixture_call("demo-read", "read_file", {"path": "config.json"})]
    return [fixture_message("Демо принимает запрос на смену порта в config.json.")]


def fixture_call(call_id, name, arguments):
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": json.dumps(arguments)}


def fixture_message(text):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


def subscription_token():
    profile = json.loads(PROFILE.read_text())
    if time.time() + 90 >= profile["expires_at"]:
        response = request_json(TOKEN_ENDPOINT, {
            "grant_type": "refresh_token", "client_id": profile["client_id"],
            "refresh_token": profile["refresh_token"], "resource": RESOURCE,
        })
        if PLAN_SCOPE not in response.get("scope", profile.get("scope", "")).split():
            raise ValueError("ChatGPT plan permission has been revoked")
        profile.update(response)
        profile["expires_at"] = time.time() + int(response["expires_in"])
        write_private(PROFILE, profile)
    return profile["access_token"]


async def provider_events(turn):
    if MODE == "fixture":
        items = fixture_items(turn)
        for item in items:
            if item["type"] == "message":
                yield line("delta", text=item["content"][0]["text"])
        yield line("final", output=items, usage={}, model="fixture")
        return

    try:
        if MODE == "chatgpt":
            async with PROFILE_LOCK:
                token = await asyncio.to_thread(subscription_token)
        elif MODE == "api":
            token = os.environ["LLM_API_KEY"]
            if not token:
                raise ValueError("No API key configured")
        else:
            raise ValueError("Unsupported model mode")
    except (OSError, KeyError, ValueError, HTTPError):
        yield line("error", detail="Model credentials unavailable")
        return

    tools = turn.tools
    if MODE == "chatgpt":
        tools = [{"type": "namespace", "name": "workdir",
                  "description": "Tools for the GoBack sandbox workdir", "tools": tools}]
    request = {
        "model": MODEL, "instructions": turn.instructions, "input": turn.input, "tools": tools,
        "store": False, "stream": True, "reasoning": {"effort": os.environ.get("LLM_REASONING_EFFORT", "low")},
    }
    if MODE == "api":
        request["max_output_tokens"] = 2048
    base_url = RESOURCE if MODE == "chatgpt" else os.environ.get("LLM_PROVIDER_URL", RESOURCE)
    for attempt in range(2):
        delivered = False
        try:
            async for event in model_response(base_url, token, request):
                delivered = True
                yield event
            return
        except (httpx.HTTPError, ssl.SSLError, ValueError) as error:
            if delivered or attempt:
                yield line("error", detail=f"Model response was interrupted ({type(error).__name__})")
                return


async def model_response(base_url, token, request):
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream("POST", base_url.rstrip("/") + "/responses", json=request,
                                 headers={"Authorization": f"Bearer {token}"}) as stream:
            if stream.status_code != 200:
                raw = await stream.aread()
                try:
                    provider_error = json.loads(raw)["error"]
                    metadata = [provider_error.get(key) for key in ("type", "code", "param")]
                    diagnostic = " ".join(item for item in metadata if item and re.fullmatch(r"[\w.\[\]-]+", item))
                except (KeyError, TypeError, ValueError):
                    diagnostic = ""
                yield line("error", detail=f"Model returned HTTP {stream.status_code} {diagnostic}".strip())
                return
            async for entry in stream.aiter_lines():
                if not entry.startswith("data: "):
                    continue
                if entry[6:] == "[DONE]":
                    break
                event = json.loads(entry[6:])
                kind = event.get("type")
                if kind == "response.output_text.delta":
                    yield line("delta", text=event["delta"])
                elif kind == "response.completed":
                    response = event["response"]
                    yield line("final", output=response["output"], usage=response.get("usage") or {},
                               model=response.get("model", MODEL))
                    return
                elif kind in {"error", "response.failed", "response.incomplete"}:
                    result = event.get("response", {})
                    error = result.get("error") or result.get("incomplete_details") or event
                    code = error.get("code") or error.get("reason") or kind
                    yield line("error", detail=f"Model stopped with {code}")
                    return
    yield line("error", detail="Model stream ended without a final response")


@app.post("/v0/response")
async def respond(turn: Turn, x_goback_proxy_token: str = Header(default="")):
    if INTERNAL_TOKEN and not hmac.compare_digest(x_goback_proxy_token.encode(), INTERNAL_TOKEN.encode()):
        raise HTTPException(401, "Invalid proxy token")
    return StreamingResponse(provider_events(turn), media_type="application/x-ndjson")
