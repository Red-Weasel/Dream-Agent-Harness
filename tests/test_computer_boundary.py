"""DREAM-187 (computer): the controlled browser reaches only the public internet. Every connection its
pages ask for goes through a network boundary of the context's own (computer._Boundary: the sandbox
proxy's code, core/sandbox_net), which judges the destination by the shared rule before connecting --
loopback, private, link-local and this machine's own addresses refused, `localhost` refused by name, a
name judged by every address it resolves to -- and dials a public destination by the addresses judged.
No grant of any kind: the owner's Browser tab is for local pages. Dream's own Studio is refused by name
before any lookup. A URL the model opens a target at is judged the same way before the browser is
touched. The real browser is driven in test_computer_controls (the boundary's prevention of a redirect
hop, a child frame, a WebSocket handshake and Studio's origin, with a loopback service counting what
reached it); here the boundary is asked directly, as the browser asks it, over a faked wire."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import contextlib

import pytest

import dream.computer as computer_module
from dream.computer import Computer, ComputerError
from dream.core import sandbox_net

PUBLIC = "93.184.216.34"


@pytest.fixture
def names(monkeypatch):
    """Name resolution from a table (an IP literal resolves to itself), never through DNS."""
    table: dict[str, list[str]] = {}

    async def resolve(host):
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            pass
        if host not in table:
            raise OSError(-2, "Name or service not known")
        return table[host]

    monkeypatch.setattr(sandbox_net, "_resolve", resolve)
    return table


@pytest.fixture
def wire(monkeypatch):
    """The fake network: every (address, port) the boundary dials, in order; a dial reaches a stand-in
    origin answering any request with an empty 200. A forbidden destination proves its prevention by its
    absence from this list."""
    dialled: list[tuple[str, int]] = []

    async def dial(address, port):
        dialled.append((address, port))
        ours, theirs = socket.socketpair()

        async def origin():
            reader, writer = await asyncio.open_connection(sock=theirs)
            await reader.read(65536)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            with contextlib.suppress(OSError):
                await writer.drain()
            writer.close()

        asyncio.get_running_loop().create_task(origin())
        return await asyncio.open_connection(sock=ours)

    monkeypatch.setattr(sandbox_net, "_dial", dial)
    return dialled


@pytest.fixture
async def boundary():
    started = await computer_module._Boundary().start()
    try:
        yield started
    finally:
        started.close()


async def _ask(boundary, request: bytes) -> tuple[int, bytes]:
    """One proxy request to the boundary, as the browser makes it -> (status, the rest of the answer)."""
    reader, writer = await asyncio.open_connection(*boundary.address)
    writer.write(request)
    status = int((await reader.readline()).split()[1])
    rest = b""
    with contextlib.suppress(TimeoutError):   # a refusal closes at once; an opened tunnel stays open
        rest = await asyncio.wait_for(reader.read(), 1)
    writer.close()
    return status, rest


def _get(url: str) -> bytes:
    host = url.split("/")[2]
    return f"GET {url} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()


def _connect(hostport: str) -> bytes:
    return f"CONNECT {hostport} HTTP/1.1\r\nHost: {hostport}\r\n\r\n".encode()


@pytest.mark.parametrize("request_", [
    _get("http://127.0.0.1:9/"),              # a loopback service by address: a local engine, Studio's port
    _get("http://localhost:3001/"),           # this machine by name
    _get("http://10.0.0.5:8000/"),            # a private network: a router
    _get("http://169.254.169.254/latest/"),   # link-local
    _get("http://private.example/"),          # a name resolving to a private address
    _connect("127.0.0.1:9"),                  # a TLS or WebSocket tunnel to loopback
    _connect("[::1]:8080"),
    _connect("private.example:443"),
])
async def test_the_boundary_refuses_a_private_destination_before_connecting(names, wire, boundary, request_):
    names["private.example"] = ["192.168.1.1"]
    status, body = await _ask(boundary, request_)
    assert status == 403 and b"reaches only the public internet" in body
    assert wire == [] and len(boundary.refused) == 1


async def test_the_boundary_refuses_studios_origin_by_name_before_any_lookup(names, wire, boundary, monkeypatch):
    monkeypatch.setitem(sandbox_net.OWN_SERVICES, 3999, "Studio")
    for request_ in (_get("http://localhost:3999/"), _get("http://127.0.0.1:3999/#token=abc"), _connect("[::1]:3999")):
        status, body = await _ask(boundary, request_)
        assert status == 403 and b"Dream's own Studio" in body
    assert wire == [] and boundary.refused == ["localhost:3999", "127.0.0.1:3999", "[::1]:3999"]
    # Another port on loopback is refused too, as local -- but not as Studio.
    status, body = await _ask(boundary, _get("http://127.0.0.1:4000/"))
    assert status == 403 and b"is Dream's own Studio" not in body and b"local or private address" in body


async def test_a_public_destination_is_dialled_by_the_address_judged(names, wire, boundary):
    names["public.example"] = [PUBLIC]
    status, _ = await _ask(boundary, _get("http://public.example/"))
    assert status == 200
    status, _ = await _ask(boundary, _connect("public.example:443"))
    assert status == 200   # the tunnel opens
    assert wire == [(PUBLIC, 80), (PUBLIC, 443)] and boundary.refused == []


async def test_a_name_that_does_not_resolve_connects_nowhere(names, wire, boundary):
    status, _ = await _ask(boundary, _get("http://nowhere.example/"))
    assert status == 502 and wire == []


async def test_a_closed_boundary_accepts_and_dials_nothing(names, wire):
    names["public.example"] = [PUBLIC]
    boundary = await computer_module._Boundary().start()
    reader, writer = await asyncio.open_connection(*boundary.address)   # accepted while open...
    boundary.close()
    writer.write(_get("http://public.example/"))                          # ...asking after the close
    with contextlib.suppress(OSError):
        await writer.drain()
    try:
        answer = await asyncio.wait_for(reader.read(), 5)
    except OSError:      # closed on it with the request unread: a reset
        answer = b""
    assert answer == b""                                                  # closed on it, nothing answered
    writer.close()
    with pytest.raises(OSError):
        await asyncio.open_connection(*boundary.address)                 # the listener is gone
    assert wire == []


@pytest.mark.parametrize("url", ["http://127.0.0.1:9/", "http://localhost:3001/", "http://10.0.0.5:8000/",
                                 "http://[::1]:8080/", "http://private.example/", "https://169.254.169.254/"])
async def test_open_refuses_a_local_or_private_url_before_the_browser_launches(tmp_path, names, wire, url):
    names["private.example"] = ["192.168.1.1"]
    c = Computer(tmp_path, tmp_path / "captures")
    with pytest.raises(ComputerError, match="local or private|this machine"):
        await c.open("browser", url=url)
    assert c._pw is None and not c.targets and wire == []


async def test_open_refuses_studios_origin_by_name(tmp_path, names, wire, monkeypatch):
    monkeypatch.setitem(sandbox_net.OWN_SERVICES, 3999, "Studio")
    c = Computer(tmp_path, tmp_path / "captures")
    for url in ("http://localhost:3999/#token=abc", "http://127.0.0.1:3999/"):
        with pytest.raises(ComputerError, match="Dream's own Studio"):
            await c.open("browser", url=url)
    assert c._pw is None and wire == []


async def test_open_refuses_a_url_with_userinfo_or_no_resolution(tmp_path, names, wire):
    c = Computer(tmp_path, tmp_path / "captures")
    with pytest.raises(ComputerError, match="plain http or https URL"):
        await c.open("browser", url="http://127.0.0.1:8080@attacker.example/")
    with pytest.raises(ComputerError, match="did not resolve"):
        await c.open("browser", url="http://nowhere.example/")
    assert c._pw is None and wire == []


def test_own_service_is_studio_at_its_port_by_any_loopback_spelling(monkeypatch):
    monkeypatch.setitem(sandbox_net.OWN_SERVICES, 3999, "Studio")
    for host in ("localhost", "LOCALHOST.", "studio.localhost", "127.0.0.1", "127.0.0.2", "::1", "::ffff:127.0.0.1"):
        assert sandbox_net.own_service(host, 3999) == "Studio", host
    for host, port in (("127.0.0.1", 4000), ("10.0.0.5", 3999), ("example.com", 3999), ("93.184.216.34", 3999)):
        assert sandbox_net.own_service(host, port) is None, (host, port)
