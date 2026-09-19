"""Public-internet access for sandboxed commands, without the host network.

The sandbox keeps its own network namespace (loopback only), so this machine's
services, abstract Unix sockets and the LAN stay unreachable. Inside, a small
forwarder listens on the sandbox's 127.0.0.1 and exports HTTP(S)_PROXY. Each
connection it accepts is handed to Dream over a socketpair: the forwarder asks
on a control channel and receives a fresh socket by SCM_RIGHTS, so nothing in
the sandbox ever creates a Unix socket. Dream's side is an HTTP proxy (CONNECT
and absolute-form GET) that resolves the name itself and connects only to
globally routable addresses that are not this machine's own.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import socket
from urllib.parse import urlsplit

# Runs inside the sandbox with the system Python: argv = [control fd, "--", *command].
FORWARDER_SOURCE = r'''
import os
import socket
import subprocess
import sys
import threading

def pump(src, dst):
    try:
        while data := src.recv(65536):
            dst.sendall(data)
    except OSError:
        pass
    try:
        dst.shutdown(socket.SHUT_WR)
    except OSError:
        pass

def relay(a, b):
    other = threading.Thread(target=pump, args=(a, b), daemon=True)
    other.start()
    pump(b, a)
    other.join()
    a.close()
    b.close()

def main():
    control = socket.socket(fileno=int(sys.argv[1]))
    control.set_inheritable(False)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(64)
    proxy = "http://127.0.0.1:%d" % server.getsockname()[1]
    env = dict(os.environ)
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        env[key] = proxy
    env["no_proxy"] = env["NO_PROXY"] = "localhost,127.0.0.1,::1"
    lock = threading.Lock()

    def serve():
        while True:
            client, _ = server.accept()
            with lock:
                control.sendall(b"c")
                _, fds, _, _ = socket.recv_fds(control, 1, 1)
            if not fds:
                client.close()
                return
            threading.Thread(target=relay, args=(client, socket.socket(fileno=fds[0])),
                             daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    code = subprocess.Popen(sys.argv[3:], env=env).wait()
    sys.stdout.flush()
    os._exit(code if code >= 0 else 128 - code)

main()
'''


class _Refused(Exception):
    pass


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not ip.is_global or ip.is_multicast:
        return False
    # A global address can still be one of this machine's own (IPv6 hosts
    # usually have one), which would reach services bound to every interface.
    with socket.socket(socket.AF_INET6 if ip.version == 6 else socket.AF_INET) as probe:
        try:
            probe.bind((str(ip), 0))
        except OSError:
            return True
    return False


async def _connect(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses or not all(_public(a) for a in addresses):
        raise _Refused(f"{host} is a local or private address; the sandbox reaches only the public internet")
    error: OSError | None = None
    for address in addresses:  # connect by the checked address, never re-resolve
        try:
            return await asyncio.wait_for(asyncio.open_connection(address, port), 30)
        except (OSError, asyncio.TimeoutError) as exc:
            error = OSError(str(exc) or "connection timed out")
    raise error


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except OSError:
        pass


def _target(method: str, target: str, lines: list[str], version: str) -> tuple[str, int, bytes | None]:
    """→ (host, port, request to send upstream; None for a CONNECT tunnel)."""
    if method == "CONNECT":
        if target.startswith("["):
            host, _, port = target[1:].partition("]:")
        else:
            host, _, port = target.rpartition(":")
        return host, int(port), None
    url = urlsplit(target)
    if url.scheme != "http" or not url.hostname:
        raise ValueError("only http:// URLs and CONNECT tunnels are proxied")
    path = (url.path or "/") + (f"?{url.query}" if url.query else "")
    drop = {"proxy-connection", "proxy-authorization", "connection", "keep-alive"}
    headers = [line for line in lines if line.split(":", 1)[0].strip().lower() not in drop]
    head = "\r\n".join([f"{method} {path} {version}", *headers, "Connection: close", "", ""])
    return url.hostname, url.port or 80, head.encode("latin-1")


async def _proxy_connection(sock: socket.socket) -> None:
    reader, writer = await asyncio.open_connection(sock=sock)
    try:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
            first, *lines = head.decode("latin-1").split("\r\n")
            method, target, version = first.split(" ")
            host, port, request = _target(method, target, [l for l in lines if l], version)
            up_reader, up_writer = await _connect(host, port)
        except _Refused as exc:
            status, reason = "403 Forbidden", str(exc)
        except (ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
            status, reason = "400 Bad Request", str(exc) or "malformed proxy request"
        except (OSError, asyncio.TimeoutError) as exc:
            status, reason = "502 Bad Gateway", str(exc) or "connection failed"
        else:
            try:
                if request is None:
                    writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                else:
                    up_writer.write(request)
                await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
            finally:
                up_writer.close()
            return
        body = f"Dream sandbox proxy: {reason}\n".encode()
        writer.write(f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\n"
                     "Connection: close\r\n\r\n".encode() + body)
        with contextlib.suppress(OSError):
            await writer.drain()
    finally:
        writer.close()


async def serve(control: socket.socket) -> None:
    """Answer the forwarder's requests for connections until the command ends."""
    loop = asyncio.get_running_loop()
    tasks: set[asyncio.Task] = set()
    try:
        while await loop.sock_recv(control, 1):
            ours, theirs = socket.socketpair()
            with theirs:
                socket.send_fds(control, [b"c"], [theirs.fileno()])
            task = asyncio.create_task(_proxy_connection(ours))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    finally:
        for task in tasks:
            task.cancel()
