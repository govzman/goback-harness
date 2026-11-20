"""Execute one bounded operation inside the sandbox."""

import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


WORK = Path("/work")
FILE_LIMIT = 64 * 1024
OUTPUT_LIMIT = 8 * 1024


class FileTooLarge(ValueError):
    pass


class FieldMissing(ValueError):
    pass


def work_path(name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or not relative.parts:
        raise ValueError("Expected a path relative to /work")
    path = WORK
    for part in relative.parts:
        if part.casefold() in {".git", ".."} or "\\" in part or ":" in part:
            raise ValueError("Path is outside the permitted workdir")
        path /= part
        if path.is_symlink():
            raise ValueError("Symbolic links are unsupported")
    return path


def read_bytes(path):
    with path.open("rb") as stream:
        data = stream.read(FILE_LIMIT + 1)
    if len(data) > FILE_LIMIT:
        raise FileTooLarge("File exceeds the per-operation size limit")
    return data


def version(content):
    return hashlib.sha256(content).hexdigest()


def replace(path, data, expected):
    try:
        current = version(read_bytes(path))
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        current = None
        mode = 0o644
    if current != expected:
        return False
    fd, name = tempfile.mkstemp(prefix=".goback-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
        try:
            actual = version(read_bytes(path))
        except FileNotFoundError:
            actual = None
        if actual != expected:
            return False
        os.replace(name, path)
        return True
    finally:
        Path(name).unlink(missing_ok=True)


def unique_keys(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON keys are unsupported")
    return result


def select_field(document, address):
    if not isinstance(address, list) or not address or not all(isinstance(k, str) for k in address):
        raise ValueError("Provide the field address as a list of object keys")
    parent = document
    for key in address[:-1]:
        if not isinstance(parent, dict) or key not in parent:
            raise FieldMissing("The field address no longer exists")
        parent = parent[key]
    key = address[-1]
    if not isinstance(parent, dict) or key not in parent:
        raise FieldMissing("The field address no longer exists")
    return parent, key


def set_field(path, operation):
    undo = operation["op"] == "undo"
    current_version = "missing"
    try:
        raw = read_bytes(path)
        current_version = version(raw)
        document = json.loads(raw, object_pairs_hook=unique_keys)
        parent, key = select_field(document, operation["address"])
    except (FileNotFoundError, UnicodeError, ValueError) as error:
        if undo:
            reason = {
                FileNotFoundError: "file_missing",
                FileTooLarge: "file_too_large",
                FieldMissing: "field_missing",
            }.get(type(error), "invalid_json")
            return {"status": "conflict", "reason": reason, "detail": str(error), "before": current_version}
        raise

    old, new = parent[key], operation["value"]
    if type(old) not in (str, int, float, bool, type(None)) or type(new) not in (str, int, float, bool, type(None)):
        if undo:
            return {"status": "conflict", "reason": "field_not_scalar",
                    "detail": "The field is no longer scalar", "before": current_version}
        raise ValueError("Only scalar JSON fields are supported")
    if undo:
        expected = operation["expected"]
        if type(old) is not type(expected) or old != expected:
            return {"status": "conflict", "reason": "field_changed",
                    "detail": "The field holds a different value or type", "current": old, "before": current_version}
    elif operation.get("version") and operation["version"] != current_version:
        return {"status": "conflict", "detail": "The file changed since it was read", "before": current_version}

    if type(old) is type(new) and old == new:
        return {"status": "unchanged", "before": current_version, "after": current_version}

    parent[key] = new
    indent = 2 if b"\n" in raw.rstrip() else None
    result = json.dumps(document, ensure_ascii=False, indent=indent, allow_nan=False)
    newline = "\r\n" if b"\r\n" in raw else "\n"
    result = result.replace("\n", newline)
    if raw.endswith(b"\n"):
        result += newline
    data = result.encode()
    if len(data) > FILE_LIMIT:
        raise ValueError("Result exceeds the per-operation size limit")
    if not replace(path, data, current_version):
        return {"status": "conflict", "reason": "file_changed_during_operation",
                "detail": "The file changed during the operation", "before": current_version}
    return {"status": "ok", "old": old, "new": new, "before": current_version, "after": version(data)}


def run_command(operation):
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            ["/bin/sh", "-c", operation["command"]], cwd=WORK, stdout=stdout, stderr=stderr,
            start_new_session=True, stdin=subprocess.DEVNULL,
        )
        try:
            code = process.wait(timeout=operation["timeout"])
            status = "ok" if code == 0 else "command_error"
        except subprocess.TimeoutExpired:
            code, status = None, "timeout"
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        stdout.seek(0)
        stderr.seek(0)
        return {"status": status, "exit_code": code, "stdout": stdout.read(OUTPUT_LIMIT).decode(errors="replace"),
                "stderr": stderr.read(OUTPUT_LIMIT).decode(errors="replace")}


def execute(operation):
    kind = operation["op"]
    if kind == "run":
        return run_command(operation)
    path = work_path(operation["path"])
    if kind == "read":
        data = read_bytes(path)
        return {"status": "ok", "content": data.decode(), "version": version(data)}
    if kind == "write":
        data = operation["content"].encode()
        if len(data) > FILE_LIMIT:
            raise ValueError("File exceeds the per-operation size limit")
        if not replace(path, data, operation.get("version")):
            return {"status": "conflict", "detail": "Read the file before overwriting it"}
        return {"status": "ok", "version": version(data)}
    if kind in {"set_json", "undo"}:
        return set_field(path, operation)
    raise ValueError("Unsupported operation")


if __name__ == "__main__":
    try:
        result = execute(json.loads(sys.argv[1]))
    except (OSError, TypeError, ValueError, KeyError) as error:
        result = {"status": "file_error", "detail": str(error)}
    print(json.dumps(result, ensure_ascii=False))
