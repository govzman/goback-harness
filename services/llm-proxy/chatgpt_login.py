"""Authorize GoBack to use a local user's ChatGPT plan via loopback OAuth."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import tempfile
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


ISSUER = "https://auth.openai.com"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN_ENDPOINT = ISSUER + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
STATE_DIR = Path(os.environ.get("GOBACK_AUTH_DIR", Path.home() / ".config/goback-harness"))
PROFILE = STATE_DIR / "chatgpt.json"


class LoginError(Exception):
    pass


def request_json(url, form=None, headers=None):
    request = Request(url, data=urlencode(form).encode() if form else None, headers=headers or {})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def write_private(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def decode_part(part):
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def check_identity(token, client_id, nonce):
    header, body, signature = token.split(".")
    metadata = json.loads(decode_part(header))
    if metadata.get("alg") != "RS256":
        raise ValueError("Unexpected ID token signature algorithm")
    keys = request_json(ISSUER + "/.well-known/jwks.json")["keys"]
    key = next((key for key in keys if key.get("kid") == metadata.get("kid") and key["kty"] == "RSA"), None)
    if not key:
        raise ValueError("No matching OpenAI signing key")
    exponent = int.from_bytes(decode_part(key["e"]), "big")
    modulus = int.from_bytes(decode_part(key["n"]), "big")
    public_key = rsa.RSAPublicNumbers(exponent, modulus).public_key()
    public_key.verify(decode_part(signature), f"{header}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    claims = json.loads(decode_part(body))
    audience = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
    if (claims["iss"] != ISSUER or client_id not in audience or claims["nonce"] != nonce
            or claims["exp"] <= time.time() or not claims["sub"]
            or (len(audience) > 1 and claims.get("azp") != client_id)):
        raise ValueError("Invalid ID token claims")
    return claims["sub"]


def login():
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    host_path = STATE_DIR / "host-id.json"
    if not host_path.exists():
        write_private(host_path, f"urn:uuid:{uuid.uuid4()}")
    host_id = json.loads(host_path.read_text())
    previous = json.loads(PROFILE.read_text()) if PROFILE.exists() else None
    callback = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            # The callback request contains the one-time authorization code.
            pass

        def do_GET(self):
            url = urlsplit(self.path)
            query = {key: values[0] for key, values in parse_qs(url.query).items()}
            if url.path != "/auth/callback" or not hmac.compare_digest(query.get("state", ""), state):
                self.send_error(404)
                return
            callback.update(query)
            body = "GoBack received the browser response. Return to your Codex chat.".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with HTTPServer(("127.0.0.1", 0), CallbackHandler) as listener:
        redirect = f"http://127.0.0.1:{listener.server_port}/auth/callback"
        authorization = {
            "client_id": previous["client_id"] if previous else "dynamic_agent_client",
            "ext_agent_host_id": host_id,
            "response_type": "code",
            "redirect_uri": redirect,
            "scope": f"openid profile email offline_access resource.invoke {PLAN_SCOPE}",
            "resource": RESOURCE,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if not previous:
            authorization["agent_name_hint"] = "GoBack Harness"
        print(AUTHORIZE + "?" + urlencode(authorization), flush=True)
        listener.timeout = 1
        deadline = time.monotonic() + 900
        while not callback and time.monotonic() < deadline:
            listener.handle_request()

    if not callback:
        raise LoginError("No browser response before the sign-in link expired.")
    if not callback.get("code") or callback.get("error"):
        raise LoginError("ChatGPT did not grant the requested authorization.")
    client_id = callback.get("client_id") or (previous and previous["client_id"])
    if not client_id or client_id == "dynamic_agent_client" or (previous and client_id != previous["client_id"]):
        raise LoginError("OpenAI did not return the expected registration.")
    tokens = request_json(TOKEN_ENDPOINT, {
        "grant_type": "authorization_code", "client_id": client_id, "code": callback["code"],
        "code_verifier": verifier, "redirect_uri": redirect, "resource": RESOURCE,
    })
    subject = check_identity(tokens["id_token"], client_id, nonce)
    if previous and subject != previous["subject"]:
        raise LoginError("The signed-in account differs from the saved account.")
    if PLAN_SCOPE not in tokens.get("scope", "").split():
        raise LoginError("The account did not grant permission to use its ChatGPT plan.")
    write_private(PROFILE, {
        **tokens, "client_id": client_id, "subject": subject,
        "expires_at": time.time() + int(tokens["expires_in"]),
    })
    print("ChatGPT plan authorized.")
    catalog = request_json(RESOURCE + "/models", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    slugs = [entry["slug"] for entry in catalog.get("models", []) if entry.get("visibility") == "list"]
    print("Available models:", ", ".join(slugs))


if __name__ == "__main__":
    try:
        login()
    except HTTPError as error:
        sys.exit(f"OpenAI returned HTTP {error.code} during authorization or model discovery.")
    except LoginError as error:
        sys.exit(str(error))
    except Exception as error:
        sys.exit(f"ChatGPT authorization failed ({type(error).__name__}).")
