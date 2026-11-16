"""Minimal client for Unreal Editor's Python Remote Execution protocol.

Mirrors Engine/Plugins/Experimental/PythonScriptPlugin/Content/Python/remote_execution.py
(stdlib only, so it runs inside Blender). Unreal side must have
Project Settings > Plugins > Python > Enable Remote Execution turned on.
"""

from __future__ import annotations

import json
import socket
import time
import uuid
from dataclasses import dataclass

_PROTOCOL_VERSION = 1
_PROTOCOL_MAGIC = "ue_py"

_TYPE_PING = "ping"
_TYPE_PONG = "pong"
_TYPE_OPEN_CONNECTION = "open_connection"
_TYPE_CLOSE_CONNECTION = "close_connection"
_TYPE_COMMAND = "command"
_TYPE_COMMAND_RESULT = "command_result"

MODE_EXEC_FILE = "ExecuteFile"

# Must match the Python plugin settings in Unreal (these are the engine defaults).
MULTICAST_GROUP_ENDPOINT = ("239.0.0.1", 6766)
MULTICAST_BIND_ADDRESS = "127.0.0.1"
MULTICAST_TTL = 0
COMMAND_HOST = "127.0.0.1"

_RECEIVE_BUFFER_SIZE = 8192


class RemoteExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class UnrealNode:
    node_id: str
    project_name: str
    engine_version: str
    machine: str
    user: str
    project_root: str

    @property
    def label(self) -> str:
        project = self.project_name or "Unreal Editor"
        version = self.engine_version.split("-")[0] if self.engine_version else "?"
        return f"{project} (UE {version})"


def _encode(type_: str, source: str, dest: str | None = None, data: dict | None = None) -> bytes:
    message = {
        "version": _PROTOCOL_VERSION,
        "magic": _PROTOCOL_MAGIC,
        "type": type_,
        "source": source,
    }
    if dest:
        message["dest"] = dest
    if data:
        message["data"] = data
    return json.dumps(message, ensure_ascii=False).encode("utf-8")


def _decode(raw: bytes) -> dict | None:
    try:
        message = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(message, dict):
        return None
    if message.get("version") != _PROTOCOL_VERSION or message.get("magic") != _PROTOCOL_MAGIC:
        return None
    return message


class RemoteExecution:
    """One client session: discover editors, then run commands on one of them."""

    def __init__(self) -> None:
        self.node_id = str(uuid.uuid4())
        self._udp = self._open_broadcast_socket()
        self._listen: socket.socket | None = None
        self._channel: socket.socket | None = None
        self._remote_node_id: str | None = None

    def __enter__(self) -> "RemoteExecution":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ------------------------------------------------------------------ UDP

    @staticmethod
    def _open_broadcast_socket() -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((MULTICAST_BIND_ADDRESS, MULTICAST_GROUP_ENDPOINT[1]))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, MULTICAST_TTL)
        sock.setsockopt(
            socket.IPPROTO_IP,
            socket.IP_MULTICAST_IF,
            socket.inet_aton(MULTICAST_BIND_ADDRESS),
        )
        sock.setsockopt(
            socket.IPPROTO_IP,
            socket.IP_ADD_MEMBERSHIP,
            socket.inet_aton(MULTICAST_GROUP_ENDPOINT[0]) + socket.inet_aton(MULTICAST_BIND_ADDRESS),
        )
        sock.settimeout(0.1)
        return sock

    def _broadcast(self, type_: str, dest: str | None = None, data: dict | None = None) -> None:
        self._udp.sendto(_encode(type_, self.node_id, dest, data), MULTICAST_GROUP_ENDPOINT)

    def _receive_broadcast(self) -> dict | None:
        try:
            raw = self._udp.recv(65535)
        except (socket.timeout, ConnectionResetError):
            return None
        message = _decode(raw)
        if message is None or message.get("source") == self.node_id:
            return None
        dest = message.get("dest")
        if dest and dest != self.node_id:
            return None
        return message

    def discover(self, timeout: float = 1.5) -> list[UnrealNode]:
        """Ping the multicast group and collect every editor that answers."""
        nodes: dict[str, UnrealNode] = {}
        deadline = time.monotonic() + timeout
        next_ping = 0.0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_ping:
                self._broadcast(_TYPE_PING)
                next_ping = now + 0.5
            message = self._receive_broadcast()
            if message is None or message.get("type") != _TYPE_PONG:
                continue
            data = message.get("data") or {}
            node_id = message["source"]
            nodes[node_id] = UnrealNode(
                node_id=node_id,
                project_name=str(data.get("project_name", "")),
                engine_version=str(data.get("engine_version", "")),
                machine=str(data.get("machine", "")),
                user=str(data.get("user", "")),
                project_root=str(data.get("project_root", "")),
            )
        return sorted(nodes.values(), key=lambda n: (n.project_name, n.node_id))

    # ------------------------------------------------------------------ TCP

    def connect(self, remote_node_id: str, timeout: float = 15.0) -> None:
        """Ask the editor to connect back to us over TCP."""
        self._listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        self._listen.bind((COMMAND_HOST, 0))  # ephemeral port: several Blenders can coexist
        self._listen.listen(1)
        self._listen.settimeout(2.5)
        port = self._listen.getsockname()[1]

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._broadcast(
                _TYPE_OPEN_CONNECTION,
                remote_node_id,
                {"command_ip": COMMAND_HOST, "command_port": port},
            )
            try:
                self._channel = self._listen.accept()[0]
            except socket.timeout:
                continue
            self._channel.setblocking(True)
            self._remote_node_id = remote_node_id
            return
        raise RemoteExecutionError(
            "Unreal did not open the command connection. It may be busy (compiling shaders or "
            "showing a dialog); wait a moment and try again"
        )

    def run(self, command: str, *, timeout: float = 600.0, exec_mode: str = MODE_EXEC_FILE) -> dict:
        """Run Python in the connected editor and return its `command_result` data."""
        if self._channel is None:
            raise RemoteExecutionError("Not connected to Unreal")
        self._channel.sendall(
            _encode(
                _TYPE_COMMAND,
                self.node_id,
                self._remote_node_id,
                {"command": command, "unattended": True, "exec_mode": exec_mode},
            )
        )

        self._channel.settimeout(timeout)
        data = b""
        while True:
            try:
                part = self._channel.recv(_RECEIVE_BUFFER_SIZE)
            except socket.timeout as exc:
                raise RemoteExecutionError(
                    f"Timed out after {timeout:g} s waiting for Unreal. It may still be importing: "
                    "check the Unreal log, or raise the timeout in the add-on preferences"
                ) from exc
            if not part:
                raise RemoteExecutionError("Unreal closed the connection")
            data += part
            # Large results arrive in several chunks: keep reading until it parses.
            message = _decode(data)
            if message is not None:
                break

        if message.get("type") != _TYPE_COMMAND_RESULT:
            raise RemoteExecutionError(f"Unexpected reply from Unreal: {message.get('type')}")
        return message.get("data") or {}

    def close(self) -> None:
        if self._remote_node_id is not None:
            try:
                self._broadcast(_TYPE_CLOSE_CONNECTION, self._remote_node_id)
            except OSError:
                pass
            self._remote_node_id = None
        for sock in (self._channel, self._listen, self._udp):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._channel = None
        self._listen = None
