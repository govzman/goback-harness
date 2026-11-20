"""Expose only the selected directory through a reverse SSH tunnel."""

import os
from pathlib import Path, PurePosixPath

import asyncssh


class WorkdirServer(asyncssh.SFTPServer):
    def __init__(self, channel, folder, read_only):
        super().__init__(channel, chroot=os.fsencode(folder))
        self.folder = folder
        self.read_only = read_only

    def map_path(self, path):
        remote = PurePosixPath(os.fsdecode(path))
        parts = remote.parts[1:] if remote.is_absolute() else remote.parts
        local = self.folder
        for part in parts:
            if (part.rstrip(" .").casefold() in {"", ".git", ".."} or "\\" in part
                    or ":" in part or (os.name == "nt" and Path(part).is_reserved())):
                raise asyncssh.SFTPPermissionDenied("Path is outside the permitted workdir")
            local /= part
            if local.is_symlink() or getattr(local, "is_junction", lambda: False)():
                raise asyncssh.SFTPPermissionDenied("Links and junctions are unsupported")
        if not local.resolve().is_relative_to(self.folder):
            raise asyncssh.SFTPPermissionDenied("Path is outside the permitted workdir")
        return os.fsencode(local)

    async def scandir(self, path):
        async for entry in super().scandir(path):
            name = os.fsdecode(entry.filename)
            if name in {".", ".."}:
                yield entry
                continue
            try:
                self.map_path(path.rstrip(b"/") + b"/" + entry.filename)
            except asyncssh.SFTPPermissionDenied:
                continue
            yield entry

    def symlink(self, _oldpath, _newpath):
        raise asyncssh.SFTPPermissionDenied("Symbolic links are unsupported")

    def link(self, _oldpath, _newpath):
        raise asyncssh.SFTPPermissionDenied("Hard links are unsupported")

    def open(self, path, flags, attrs):
        if flags & (asyncssh.FXF_WRITE | asyncssh.FXF_APPEND | asyncssh.FXF_CREAT | asyncssh.FXF_TRUNC):
            self.check_write()
        return super().open(path, flags, attrs)

    def check_write(self):
        if self.read_only:
            raise asyncssh.SFTPPermissionDenied("Workdir is read-only")

    def mkdir(self, path, attrs):
        self.check_write()
        return super().mkdir(path, attrs)

    def rmdir(self, path):
        self.check_write()
        return super().rmdir(path)

    def remove(self, path):
        self.check_write()
        return super().remove(path)

    def rename(self, oldpath, newpath):
        self.check_write()
        return super().rename(oldpath, newpath)

    def posix_rename(self, oldpath, newpath):
        self.check_write()
        return super().posix_rename(oldpath, newpath)

    def setstat(self, path, attrs):
        self.check_write()
        return super().setstat(path, attrs)

    def lsetstat(self, path, attrs):
        self.check_write()
        return super().lsetstat(path, attrs)

    def fsetstat(self, file_obj, attrs):
        self.check_write()
        return super().fsetstat(file_obj, attrs)


class SFTPAuth(asyncssh.SSHServer):
    def __init__(self, key):
        self.key = key

    def public_key_auth_supported(self):
        return True

    def validate_public_key(self, username, key):
        return username == "workdir" and key == self.key


class Workdir:
    def __init__(self, folder, mode):
        self.folder = Path(folder).resolve(strict=True)
        if not self.folder.is_dir():
            raise ValueError("Select a directory")
        self.mode = mode
        self.host_key = asyncssh.generate_private_key("ssh-ed25519")
        self.sftp = None
        self.ssh = None

    def exported_key(self):
        return self.host_key.export_public_key().decode().strip()

    async def connect(self, host, ssh_port, session):
        client_key = asyncssh.import_public_key(session["sftp_authorized_key"])
        relay_key = asyncssh.import_public_key(session["relay_host_key"])
        self.sftp = await asyncssh.listen(
            "127.0.0.1", 0, server_host_keys=[self.host_key], server_factory=lambda: SFTPAuth(client_key),
            sftp_factory=lambda chan: WorkdirServer(chan, self.folder, self.mode == "ro"),
        )
        try:
            self.ssh = await asyncssh.connect(
                host, ssh_port, username=session["session_id"], password=session["ticket"],
                known_hosts=([relay_key], [], []), client_keys=[], agent_path=None, config=None,
                keepalive_interval=5, keepalive_count_max=2, connect_timeout=8, login_timeout=8,
            )
            await self.ssh.forward_remote_port("127.0.0.1", session["relay_port"], "127.0.0.1",
                                               self.sftp.get_port())
        except BaseException:
            await self.close()
            raise

    async def close(self):
        if self.ssh:
            self.ssh.close()
            await self.ssh.wait_closed()
            self.ssh = None
        if self.sftp:
            self.sftp.close()
            await self.sftp.wait_closed()
            self.sftp = None
