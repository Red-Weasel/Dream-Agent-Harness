"""Public-internet access for sandboxed commands, without the host network.

The sandbox keeps its own network namespace (loopback only), so this machine's
services, abstract Unix sockets and the LAN stay unreachable. Inside, a small
forwarder listens on the sandbox's 127.0.0.1 and exports HTTP(S)_PROXY. Each
connection it accepts is handed to Dream over a socketpair: the forwarder asks
on a control channel and receives a fresh socket by SCM_RIGHTS, so nothing in
the sandbox ever creates a Unix socket. Dream's side is an HTTP proxy (CONNECT
and absolute-form GET) that resolves the name itself and connects only to
globally routable addresses that are not this machine's own. The model's
browser goes through the same proxy code (web/browser.py), which also admits
the destinations the owner typed.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import socket
from collections.abc import Awaitable, Callable
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


# Dream's own loopback listeners as they bind, port -> name (Studio, gui/server.py): every one of them is
# loopback-only, and so refused by the rule below already; the register lets a model-driven path name what
# it refused -- the controlled browser (computer.py) refuses Studio's origin by name, before any lookup.
OWN_SERVICES: dict[int, str] = {}


def own_service(host: str, port: int) -> str | None:
    """The name of Dream's own listener that `host`:`port` reaches -- `localhost`, `*.localhost` or a
    loopback literal at a registered port -- or None."""
    name = OWN_SERVICES.get(port)
    if name is None:
        return None
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return name if _local_name(host) else None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return name if ip.is_loopback else None


_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _public(address: str) -> bool:
    """The destination rule of every model-driven network path (this proxy for the sandbox, the model's
    browser): a globally routable, non-multicast address that is not one of this machine's own. An IPv6
    address that embeds an IPv4 one -- IPv4-mapped, or the NAT64 well-known prefix, which `ipaddress`
    counts as global -- is judged by that IPv4 address."""
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    elif ip.version == 6 and ip in _NAT64:
        ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
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


def _local_name(host: str) -> bool:
    """`localhost` and `*.localhost` are this machine by name (RFC 6761), whatever they resolve to."""
    host = host.lower().rstrip(".")
    return host == "localhost" or host.endswith(".localhost")


async def _resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


async def _public_addresses(host: str) -> list[str]:
    """The addresses `host` resolves to, when it is not this machine by name and every one passes _public;
    _Refused otherwise (one local answer refuses the name whole, so a name cannot be half public).
    OSError when it does not resolve."""
    if _local_name(host):
        raise _Refused(f"{host} is this machine")
    addresses = await _resolve(host)
    local = [a for a in addresses if not _public(a)]
    if local:
        where = "is" if local == [host] else f"resolves to {', '.join(local)},"
        raise _Refused(f"{host} {where} a local or private address")
    if not addresses:
        raise _Refused(f"{host} has no address")
    return addresses


async def _dial(address: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.wait_for(asyncio.open_connection(address, port), 30)


async def _connect(
    host: str, port: int, addresses: list[str] | None = None, abandon: Callable[[], bool] = lambda: False,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect to `host`:`port` by an address _public_addresses admitted -- or by `addresses`, when the
    caller judged the name itself -- never re-resolving. `abandon()` is asked before every address is
    dialled (after every wait, that is): true means the caller no longer wants the connection, and no
    further address is tried."""
    if addresses is None:
        addresses = await _public_addresses(host)
    error: OSError | None = None
    for address in addresses:  # connect by the checked address, never re-resolve
        if abandon():
            raise _Refused("the connection is no longer wanted")
        try:
            return await _dial(address, port)
        except (OSError, asyncio.TimeoutError) as exc:
            error = OSError(str(exc) or "connection timed out")
    raise error or OSError(f"{host} has no address")


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except OSError:
        pass


def _target(method: str, target: str, lines: list[str], version: str) -> tuple[str | None, str, int, bytes | None]:
    """→ (scheme, host, port, request to send upstream; None for a CONNECT tunnel). The scheme is `http` for
    a plain request and None for a tunnel: CONNECT names a listener, not what the tunnel will carry (a
    browser carries https and wss as TLS, ws as plaintext) -- _proxy_connection learns that from its first
    bytes when the connect callback asks. A port is the port given (`:0` is port 0 for a plain request; a
    tunnel's must be 1..65535)."""
    if method == "CONNECT":
        if target.startswith("["):
            host, _, port = target[1:].partition("]:")
        else:
            host, _, port = target.rpartition(":")
        try:
            port_number = int(port)
        except ValueError:
            port_number = 0
        if not host or not 1 <= port_number <= 65535:
            raise ValueError("CONNECT needs host:port with a port in 1..65535")
        return None, host, port_number, None
    url = urlsplit(target)
    if url.scheme != "http" or not url.hostname:
        raise ValueError("only http:// URLs and CONNECT tunnels are proxied")
    path = (url.path or "/") + (f"?{url.query}" if url.query else "")
    drop = {"proxy-connection", "proxy-authorization", "connection", "keep-alive"}
    headers = [line for line in lines if line.split(":", 1)[0].strip().lower() not in drop]
    head = "\r\n".join([f"{method} {path} {version}", *headers, "Connection: close", "", ""])
    return "http", url.hostname, 80 if url.port is None else url.port, head.encode("latin-1")


Upstream = tuple[asyncio.StreamReader, asyncio.StreamWriter]


async def _connect_public(scheme: str | None, host: str, port: int) -> Upstream:
    """The sandbox's rule for a proxied connection: the scheme plays no part."""
    return await _connect(host, port)


async def _proxy_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    connect: Callable[[str | None, str, int], Awaitable[Upstream | None]] = _connect_public,
) -> None:
    """Serve one proxy client. `connect(scheme, host, port)` opens the upstream by a checked address: the
    sandbox's rule, or the browser's, which also admits the destination the owner typed (web/browser.py).
    For a CONNECT tunnel the scheme is None -- the request names a listener, not what the tunnel will carry
    -- and the callback may answer None instead of a connection when that decides its judgement: the proxy
    then opens the tunnel, reads the client's first bytes (a TLS ClientHello, first byte 0x16, is https or
    wss; anything else is plaintext, http or ws) and asks again with the scheme they show. A tunnel refused
    at that point closes without a status (its 200 is out), nothing having been dialled; every other
    refusal is a 403 before any tunnel opens."""
    try:
        opened = False  # a tunnel's 200 already sent
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
            first, *lines = head.decode("latin-1").split("\r\n")
            method, target, version = first.split(" ")
            scheme, host, port, request = _target(method, target, [l for l in lines if l], version)
            upstream = await connect(scheme, host, port)
            carried = b""
            if upstream is None:  # what the tunnel carries decides: open it and look
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                opened = True
                carried = await asyncio.wait_for(reader.read(65536), 30)
                if not carried:
                    return  # the client left without a word
                upstream = await connect("https" if carried[:1] == b"\x16" else "http", host, port)
        except _Refused as exc:
            status, reason = "403 Forbidden", str(exc)
        except (ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
            status, reason = "400 Bad Request", str(exc) or "malformed proxy request"
        except (OSError, asyncio.TimeoutError) as exc:
            status, reason = "502 Bad Gateway", str(exc) or "connection failed"
        else:
            up_reader, up_writer = upstream
            try:
                if request is None and not opened:
                    writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                up_writer.write(carried if request is None else request)
                await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
            finally:
                up_writer.close()
            return
        if opened:
            return  # judged by its bytes after the 200: the tunnel just closes
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
            task = asyncio.create_task(_proxy_connection(*await asyncio.open_connection(sock=ours)))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    finally:
        for task in tasks:
            task.cancel()
