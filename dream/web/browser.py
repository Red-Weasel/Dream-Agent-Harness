"""A persistent, in-process Camoufox (stealth Firefox) browser.

Launched lazily on first use and kept warm so there's no cold-start per fetch. Guarded
by a lock against concurrent launches, self-heals once if the browser process dies, and
reclaims itself after a stretch of inactivity to free memory. ``fetch`` never raises —
it returns a dict with an ``error`` key on failure so the tool layer can report cleanly
and the agent can recover in-loop.

On Linux the browser runs inside bubblewrap with a network namespace of its own (netns.py):
its only way out is the listener each fetch binds inside that namespace for its boundary,
so a Firefox feature that ignores the context's proxy reaches nothing. Without the sandbox
the browser does not run there (the fetch's error says why); another platform keeps the
proxy-only boundary.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import os
import re
import time
from typing import Any
from urllib.parse import urlsplit

from .. import config
from ..core import sandbox_net
from . import netns

try:
    import idna  # UTS #46 host mapping, as browsers do it; without it a non-ASCII host is refused, not guessed
except ImportError:  # pragma: no cover
    idna = None


def _load_camoufox() -> Any:
    """Imported on first launch, not at module scope: camoufox costs ~160ms to import
    and it sits on every session's boot path, but most sessions never browse."""
    try:
        from camoufox.async_api import AsyncCamoufox
    except Exception as exc:
        raise RuntimeError(f"Camoufox is not importable: {exc}") from exc
    return AsyncCamoufox


def _slug(url: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:40] or "page"


def _looks_dead(exc: Exception) -> bool:
    msg = f"{type(exc).__name__}: {exc}".lower()
    return any(
        k in msg
        for k in ("closed", "crash", "target", "disconnected", "browser has been")
    )


def extract_text(html: str, url: str) -> str:
    """Best readable-text extraction: trafilatura, then a BeautifulSoup fallback."""
    # Kept out of module scope for the same reason as camoufox — trafilatura is ~200ms
    # of import that only a session which actually reads a page needs to pay.
    import trafilatura

    text = trafilatura.extract(
        html, url=url, include_comments=False, include_tables=True, favor_recall=True
    )
    if text:
        return text.strip()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n")).strip()


# The destination rule the sandbox proxy applies to the model's shell (core/sandbox_net.py) applies to the
# model's browser, through the same proxy: a hijacked model must not probe this machine or its network
# through `browse`, nor reach Dream's own servers (they bind loopback only). The owner opens such a URL by
# typing it (tools/web.py): that destination, (scheme, host, port), is admitted -- only when it names this
# machine or its network directly, as localhost or by a local address (local_host) -- and the page so
# opened is sealed off from the internet: its fetch admits every local destination the owner typed in
# that message (a frontend and its API) and nothing else -- an untyped local address or localhost name is
# refused and fails the fetch; a public address, or any other name, is dropped undialled and unresolved
# (telling a name local would take a lookup, a way out for a local page), the page going on without it;
# nothing connects either way. Any other page's fetch admits only public destinations. The two never mix,
# so no page carries public content next to a local service. DREAM-187.
_LOCAL_RULE = ("Dream's browser reaches only the public internet; a local or private destination opens "
               "only when the user typed its URL on its own in their message -- as localhost or by a local "
               "address, with its scheme and port; not inside a link, a list or another URL (ask them to "
               "type, say, http://localhost:3001 on its own).")
_SEALED_RULE = ("local pages open sealed off from the internet: a page the user typed as a local "
                "destination reaches the local destinations they typed in that message and nothing else "
                "-- an untyped local address or localhost name fails the fetch, any other name is dropped "
                "without being resolved (telling it local would take a lookup, a way out), and nothing "
                "connects either way")

Destination = tuple[str, str, int]  # (scheme, host, port)


def _normal(host: str) -> str:
    """One spelling of a host: an address literal in canonical form (`::0001` is `::1`); a name as the
    browser puts it on the wire -- lower-cased, without its trailing dot, its non-ASCII labels mapped by
    UTS #46 without transitional processing (as browsers do: `faß` keeps its ß and is xn--fa-hia, `fass` is
    another name; a final sigma stays final) and Punycode-encoded, its ASCII labels as they are (browsers
    take `_` and edge hyphens, and an `xn--` label is on-the-wire form already). ValueError for a name the
    browser would not take either (a joiner out of context, a disallowed character), or for a non-ASCII
    name without the idna package: nothing is guessed."""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if host.isascii():
        return host.lower().rstrip(".")
    if idna is None:
        raise ValueError(f"{host} is not an ASCII host name and the idna package is not installed")
    try:
        mapped = idna.uts46_remap(host, std3_rules=False).rstrip(".")
        return ".".join(label if label.isascii() else idna.alabel(label).decode("ascii")
                        for label in mapped.split("."))
    except idna.IDNAError as exc:
        raise ValueError(f"{host} is not a host name a browser would take: {exc}") from None


def destination(url: str) -> Destination | None:
    """The (scheme, host, port) an http(s) URL names: the host in one spelling (_normal), the port the
    scheme's default only when none is given (`:0` is port 0). None for another scheme, no host, a port out
    of range, a host the browser would not take (_normal), or userinfo: `http://127.0.0.1:8080@attacker.example/`
    names attacker.example to a browser and 127.0.0.1:8080 to a reader, and a URL that reads two ways names
    nothing."""
    try:
        parts = urlsplit(url)
        port = parts.port
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None:
            return None
        host = _normal(parts.hostname)
    except ValueError:
        return None
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    return parts.scheme, host, port


def _why_not(url: str) -> str:
    """Why destination(url) is None, for a message: the host, when the browser would not take it (or it is
    non-ASCII and the idna package is missing); a username, an odd port or no host otherwise."""
    try:
        host = urlsplit(url).hostname
        if host:
            _normal(host)
    except ValueError as exc:
        return str(exc)
    return "a username, an odd port or no host"


def local_host(host: str) -> bool:
    """`host` (in one spelling, _normal) is this machine or its network as the boundary's rule reads it:
    `localhost` / `*.localhost` by name, or an address literal the rule calls local (loopback, private,
    link-local, one of this machine's own). Only such a destination can be the owner's grant. A public
    literal is not, any more than a name: a name is judged by where it resolves at each connection and
    never authorizes a local address (a typed public name that resolves to loopback -- at once, or later,
    by rebinding -- opens nothing), and a typed public address needs no grant and seals no fetch."""
    if sandbox_net._local_name(host):
        return True
    try:
        return not sandbox_net._public(host)
    except ValueError:
        return False


def bare_url(text: str) -> str:
    """`text`, a URL typed without a scheme, with the scheme it is read by -- the same whether the owner
    typed it (tools/web.py) or the model browses it: http when it names this machine or its network
    (local_host: a bare `localhost:8080` or `10.0.0.5` is a plain local service), https for anything else
    (a bare public name or address keeps its transport security)."""
    dest = destination("http://" + text)
    return ("http://" if dest and local_host(dest[1]) else "https://") + text


_LOOPBACK = ("127.0.0.1", "::1")


def _pinned(allowed: frozenset[Destination]) -> dict[Destination, tuple[str, ...]]:
    """The owner's grants, each with the addresses it is dialled by -- fixed here, never resolved again: an
    address literal is its own, `localhost` / `*.localhost` are loopback (RFC 6761: this machine by
    definition, whatever a resolver says). A destination that is not local_host -- a name, a public
    address -- is no grant and is dropped."""
    pinned = {}
    for scheme, host, port in allowed:
        if sandbox_net._local_name(host):
            pinned[(scheme, host, port)] = _LOOPBACK
        elif local_host(host):
            pinned[(scheme, host, port)] = (host,)
    return pinned


async def local_target(url: str, allowed: Any = frozenset()) -> str | None:
    """Why `url` must not be browsed, or None: it is not a plain http(s) URL (destination), or it names this
    machine or the local network -- sandbox_net's rule: `localhost` and `*.localhost` by name, any other
    host by every address it resolves to -- and its destination, scheme and port included, is not one the
    owner typed (`allowed`: the grants, as destinations)."""
    dest = destination(url)
    if dest is None:
        return f"{url} is not a plain http(s) URL: {_why_not(url)}. {_LOCAL_RULE}"
    if dest in allowed:
        return None
    try:
        await sandbox_net._public_addresses(dest[1])
    except sandbox_net._Refused as exc:
        return f"{exc}. {_LOCAL_RULE}"
    except OSError as exc:
        return f"{dest[1]} did not resolve: {exc.strerror or exc}"
    return None


async def _outside_boundary(response: Any, allowed: Any, boundary: tuple[str, int], by_name: bool = True) -> str | None:
    """Why a document the browser received is off limits: its URL names a local target the owner did not
    type (local_target -- skipped when `by_name` is off: a sealed fetch, where the boundary judged every
    connection by name without resolving and watches the main frame as it goes, resolves nothing here
    either), or the browser did not fetch it through the boundary -- the address it reports having
    connected to is not the proxy's. Every connection is meant to go through the proxy; a document that
    came another way (the proxy setting not honoured, a cached copy with no address) is refused, not
    trusted."""
    if by_name:
        reason = await local_target(response.url, allowed)
        if reason:
            return f"{response.url}: {reason}"
    addr = await response.server_addr()
    if not addr:
        return (f"the browser did not report the address it connected to for {response.url}, so it cannot "
                f"be shown to have gone through Dream's network boundary. {_LOCAL_RULE}")
    if (addr["ipAddress"].strip("[]"), addr["port"]) != boundary:
        return (f"the browser connected to {addr['ipAddress']}:{addr['port']} for {response.url}, not to "
                f"Dream's network boundary. {_LOCAL_RULE}")
    return None


# Firefox prefs for the boundary. The proxy ones: loopback goes through the proxy too, a failing proxy is
# never bypassed for a direct connection, and no bypass list applies. Then what the per-context proxy does
# not cover, closed rather than routed: DNS over HTTPS (5: off), WebRTC (browse needs none of it, and ICE
# would reach the network by itself), and OCSP -- an attacker's certificate can point OCSP at a private
# address, and Firefox fetches it outside the context's proxy; CRLite remains for revocation.
_PROXY_PREFS = {"network.proxy.allow_hijacking_localhost": True, "network.proxy.failover_direct": False,
                "network.proxy.allow_bypass": False,
                "network.trr.mode": 5, "media.peerconnection.enabled": False, "security.OCSP.enabled": 0}

# How long a fetch lets the boundary settle what it was still judging when the page closed (a lookup under
# way, a request on its way) before stopping it all; a check unsettled by then fails the fetch.
_SETTLE_S = 5.0
# How long a fetch waits for the browser to close the page; a page not closed by then fails the fetch.
_CLOSE_S = 5.0


def _collect(task: asyncio.Task) -> None:
    """A done callback that takes a task's outcome off the loop's hands: an exception nobody else retrieves
    would otherwise be reported when the garbage collector finds the task."""
    if not task.cancelled():
        task.exception()


class Browser:
    def __init__(self) -> None:
        self._cm: Any = None
        self._browser: Any = None
        self._sandbox: Any = None  # the netns.Sandbox the browser runs in, on Linux
        self._lock = asyncio.Lock()
        self.last_used = 0.0
        self._inflight = 0
        self._reaper: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._browser is not None

    async def _ensure(self) -> None:
        if self._browser is not None:
            return
        async_camoufox = _load_camoufox()
        async with self._lock:
            if self._browser is not None:
                return
            # On Linux the browser runs inside its sandbox (netns.py): launched through the sandbox's
            # wrapper, the control channel attached once it is up. No sandbox there, no browser:
            # netns.Unavailable is the fetch's error, and nothing is launched.
            sandbox = await netns.Sandbox.create(headless=config.BROWSER_HEADLESS) if netns.supported() else None
            try:
                # Every connection must go through the boundary each fetch gives its context: Firefox
                # keeps loopback off any proxy unless told otherwise, falls back to a direct connection
                # when the proxy fails, and honours a bypass list -- none of that here.
                cm = async_camoufox(headless=config.BROWSER_HEADLESS, humanize=True, firefox_user_prefs=_PROXY_PREFS,
                                    **(sandbox.launch_kwargs() if sandbox is not None else {}))
                browser = await cm.__aenter__()
                if sandbox is not None:
                    try:
                        await sandbox.attach()
                    except BaseException:
                        with contextlib.suppress(Exception):
                            await cm.__aexit__(None, None, None)
                        raise
            except BaseException:
                if sandbox is not None:
                    await sandbox.close()
                raise
            self._cm, self._browser, self._sandbox = cm, browser, sandbox
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_loop())

    async def _reset(self) -> None:
        async with self._lock:
            cm, sandbox = self._cm, self._sandbox
            self._cm = None
            self._browser = None
            self._sandbox = None
        if cm is not None:
            try:
                await cm.__aexit__(None, None, None)
            except Exception:
                pass
        if sandbox is not None:
            await sandbox.close()

    async def _listen_on(self) -> dict[str, Any]:
        """Where a fetch's boundary listens: on a socket bound inside the browser's network namespace when
        it runs in one (the only port the sandboxed browser can reach), on this host's loopback otherwise."""
        if self._sandbox is None:
            return {"host": "127.0.0.1", "port": 0}
        return {"sock": await self._sandbox.listener()}

    async def _reap_loop(self) -> None:
        """Close the warm browser after it's been idle long enough to be worth the
        ~200MB it holds. Exits when the browser is down; restarted on next launch."""
        idle = config.BROWSER_IDLE_SHUTDOWN_S
        if idle <= 0:
            return
        try:
            while self._browser is not None:
                await asyncio.sleep(min(idle, 30))
                if (
                    self._browser is not None
                    and self._inflight == 0
                    and (time.monotonic() - self.last_used) >= idle
                ):
                    await self._reset()
                    return
        except asyncio.CancelledError:
            pass

    async def fetch(
        self,
        url: str,
        screenshot: bool = False,
        wait_ms: int = 0,
        wait_selector: str | None = None,
        allowed: frozenset[Destination] = frozenset(),
    ) -> dict[str, Any]:
        """Load a page and return {url, final_url, status, title, text, screenshot}. A local or private
        target is refused before the browser launches; every connection the load asks for goes through
        Dream's network boundary, which refuses such a destination before connecting; and every document
        received is checked again by name and by the address the browser connected to. `allowed`: the
        destinations (scheme, host, port) the owner typed. When this page is one of them naming this
        machine or its network directly (local_host), the fetch is sealed (_SEALED_RULE): every such
        destination is admitted, by addresses pinned now (_pinned), and nothing public is; otherwise none
        is, and only the public internet is."""
        if not re.match(r"^https?://", url, re.I):
            url = bare_url(url)
        dest = destination(url)
        pinned = _pinned(allowed)
        grants = pinned if dest in pinned else {}  # non-empty: the sealed fetch of a local page
        reason = await local_target(url, grants)
        if reason:
            return {"url": url, "error": reason}
        try:
            await self._ensure()
            return await self._do_fetch(url, screenshot, wait_ms, wait_selector, grants)
        except netns.Unavailable as exc:
            return {"url": url, "error": str(exc)}
        except Exception as exc:
            if _looks_dead(exc):
                await self._reset()
                try:
                    await self._ensure()
                    return await self._do_fetch(url, screenshot, wait_ms, wait_selector, grants)
                except Exception as exc2:
                    return {"url": url, "error": f"{type(exc2).__name__}: {exc2}"}
            return {"url": url, "error": f"{type(exc).__name__}: {exc}"}

    async def _do_fetch(
        self, url: str, screenshot: bool, wait_ms: int, wait_selector: str | None,
        grants: dict[Destination, tuple[str, ...]],
    ) -> dict[str, Any]:
        self._inflight += 1
        self.last_used = time.monotonic()
        loop = asyncio.get_running_loop()
        refused: list[str] = []  # local or private destinations the boundary refused: the fetch fails
        dropped: list[str] = []  # public destinations a sealed fetch went without: the page goes on
        moved: str | None = None  # where a sealed page's main frame went, outside the typed destinations
        # Every connection the boundary accepted, from acceptance to its end, each with a future that
        # settles once the boundary has judged what it asked for (admitted or refused) or it ended without
        # asking. The fetch is decided only once each has settled or been stopped (the finally below).
        handlers: dict[asyncio.Task, asyncio.Future] = {}
        streams: list[asyncio.StreamWriter] = []  # every connection the boundary accepted or dialled
        closing = False  # the boundary takes no new connection (the page is closing)
        ended = False  # the fetch is over: nothing is judged or dialled any more (tear_down)

        def settle(task: asyncio.Task | None) -> None:
            future = handlers.get(task)
            if future is not None and not future.done():
                future.set_result(None)

        async def judge(scheme: str | None, host: str, port: int) -> list[str] | None:
            """The boundary's rule for one connection the page asks for -> the addresses to dial by. A
            grant is dialled by its pinned addresses -- exactly, scheme included: a tunnel (scheme None:
            a CONNECT names the listener, not what it carries) to a granted listener is judged by its
            first bytes, so None here has the proxy open it, look, and ask again with the scheme they show
            (under an http grant that admits the page's WebSocket to itself and refuses TLS; under an
            https grant the reverse). In a sealed fetch nothing else is resolved: an untyped local address
            or localhost name is refused, and the fetch fails on it; any other destination -- a public
            address, any other name -- is dropped: refused undialled and unresolved, the page going on
            without it. (Telling a name local would take a lookup, and a lookup is a way out for a local
            page; so a private name the owner did not type is dropped, not refused, and nothing connects
            either way.) In a public fetch any other destination is resolved and judged by the public rule
            (sandbox_net): a local or private one is refused and fails the fetch, a public one is dialled
            by the addresses judged, resolved this once. Once the fetch is over, nothing is judged."""
            if ended:
                raise sandbox_net._Refused("the fetch is over")
            key = (scheme, _normal(host), port)
            name = f"{scheme + '://' if scheme else ''}{f'[{host}]' if ':' in host else host}:{port}"
            if scheme is None and any(key[1:] == granted[1:] for granted in grants):
                return None
            if key in grants:
                settle(asyncio.current_task())
                return list(grants[key])
            if grants:
                settle(asyncio.current_task())
                if local_host(key[1]):
                    refused.append(name)
                    raise sandbox_net._Refused(f"{name} is a local or private destination the user did not type")
                dropped.append(name)
                raise sandbox_net._Refused(f"{name} is not among the local destinations the user typed and "
                                           f"is dropped unresolved; {_SEALED_RULE}")
            try:
                addresses = await sandbox_net._public_addresses(key[1])
            except sandbox_net._Refused:
                refused.append(name)
                settle(asyncio.current_task())
                raise
            settle(asyncio.current_task())
            return addresses

        async def connect(scheme: str | None, host: str, port: int) -> Any:
            addresses = await judge(scheme, host, port)
            if addresses is None:
                return None
            if ended:  # the fetch ended as this was judged: nothing is dialled after it
                raise sandbox_net._Refused("the fetch is over")
            upstream = await sandbox_net._connect(host, port, addresses, abandon=lambda: ended)
            if ended:  # the fetch ended during the dial: what it reached is not kept
                upstream[1].close()
                raise sandbox_net._Refused("the fetch is over")
            streams.append(upstream[1])
            return upstream

        def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            """One connection the boundary accepted -- or, once the fetch is closing, one it drops unread
            (asyncio hands a connection over a moment after accepting it; none is served after the end)."""
            if closing:
                writer.close()
                return
            streams.append(writer)
            task = asyncio.create_task(sandbox_net._proxy_connection(reader, writer, connect))
            handlers[task] = loop.create_future()
            task.add_done_callback(settle)
            task.add_done_callback(_collect)

        def departed(request: Any) -> None:
            """A sealed page's main frame asking for a document outside the typed destinations -- a
            redirect hop, a script moving the page -- at any point up to the context's closing: latched as
            the request is made, before any answer, and decided on after the close-down."""
            nonlocal moved
            if (grants and moved is None and request.is_navigation_request()
                    and request.frame == page.main_frame and destination(request.url) not in grants):
                moved = request.url

        def tear_down() -> None:
            """The boundary's end, at once and without waiting: nothing new is accepted (accept), judged or
            dialled (judge, connect), every handler is stopped, every connection and the listener closed --
            each on its own, so one that will not close keeps none of the others open. Whatever way the
            fetch ends, this has run by the time it returns or raises (the outer finally below): the
            guarantee rests on no await finishing."""
            nonlocal closing, ended
            closing = ended = True
            for task in handlers:
                task.cancel()
            for stream in streams:
                try:
                    stream.close()
                except Exception:
                    pass
            if boundary is not None:
                try:
                    boundary.close()
                except Exception:
                    pass

        boundary = context = page = None
        shot_path = error = None
        unsettled, unclosed = 0, False
        try:
            try:
                # Dream's network boundary for this load: an HTTP proxy on loopback (the sandbox's, when
                # the browser runs in one) that this context alone is given, so every connection the page
                # asks for -- main frame, redirect hops, child frames, resources, WebSocket handshakes --
                # reaches it by name (Firefox resolves nothing itself behind a proxy) and is refused or
                # connected there. Closed with the context.
                boundary = await asyncio.start_server(accept, **await self._listen_on(), start_serving=False)
                address = boundary.sockets[0].getsockname()[:2]
                await boundary.start_serving()  # a cancellation landing here finds the listener ours to close
                # A fresh context per fetch keeps pages isolated; no_viewport avoids a
                # Camoufox/Playwright viewport-protocol mismatch.
                context = await self._browser.new_context(no_viewport=True, proxy={"server": "http://%s:%d" % address})
                page = await context.new_page()
                # Every document any frame received: the server's redirect hops (a route handler never
                # sees them) and the page a script moved to, each with the address the browser connected
                # to; and every document the main frame asks for, as it asks (departed).
                documents: list[Any] = []
                page.on("response", lambda r: documents.append(r) if r.request.is_navigation_request() else None)
                page.on("request", departed)
                try:
                    resp = await page.goto(
                        url, wait_until="domcontentloaded", timeout=config.BROWSER_TIMEOUT_MS
                    )
                except Exception:
                    if refused or moved:
                        pass  # a refused or dropped main-frame hop shows as a browser failure too
                    elif dropped:  # sealed: the load failed after the page went for the internet
                        moved = ", ".join(dict.fromkeys(dropped))
                    else:
                        raise  # the browser's own failure
                else:
                    if wait_selector:
                        try:
                            await page.wait_for_selector(
                                wait_selector, timeout=config.BROWSER_TIMEOUT_MS
                            )
                        except Exception:
                            pass
                    if wait_ms:
                        await page.wait_for_timeout(min(wait_ms, 15000))
                    title = await page.title()
                    html = await page.content()
                    final_url = page.url
                    status = resp.status if resp else None
                    if grants and moved is None and destination(final_url) not in grants:
                        moved = final_url  # sealed: the page sits outside the typed destinations, however it got there
                if not refused and not moved:
                    for response in documents:
                        error = await _outside_boundary(response, grants, address, by_name=not grants)
                        if error:
                            break
                    else:
                        # where the page sits now, document or not (a sealed page's is among the grants:
                        # no lookup)
                        reason = await local_target(final_url, grants)
                        if reason:
                            error = f"{final_url}: {reason}"
                    if not error and screenshot:
                        config.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
                        shot_path = str(
                            config.SCREENSHOT_DIR / f"{_slug(url)}-{int(time.time())}.png"
                        )
                        await page.screenshot(path=shot_path, full_page=True)
            finally:
                # The graceful end, bounded and best effort, on the browser's own failure too: close the
                # page (its last requests come in as it goes, and are judged), then take no new connection
                # and let the boundary settle what it was still judging -- a refusal still counts. A
                # cancellation arriving here skips straight to the teardown below.
                if context is not None:
                    closing_page = asyncio.ensure_future(context.close())
                    closing_page.add_done_callback(_collect)  # its outcome taken whenever it comes
                    try:
                        done, _ = await asyncio.wait({closing_page}, timeout=_CLOSE_S)
                        unclosed = not done  # the browser did not close the page in time: fail closed
                    finally:
                        if not closing_page.done():
                            # past the deadline, or the fetch cancelled meanwhile: the close is told to
                            # stop and waited for no longer, deaf to it or not
                            closing_page.cancel()
                closing = True
                if boundary is not None:
                    boundary.close()
                if handlers:
                    await asyncio.wait(handlers.values(), timeout=_SETTLE_S)
                unsettled = sum(not future.done() for future in handlers.values())
        finally:
            try:
                tear_down()
            finally:
                self.last_used = time.monotonic()
                self._inflight -= 1
        if handlers:  # the stopped handlers finish closing: bounded, best effort
            await asyncio.wait(set(handlers), timeout=1)
        if refused or unsettled or moved or unclosed:
            # The page reached for this machine or its network (a redirect, a frame, a resource, a socket)
            # at some point up to its closing and the boundary refused before connecting; or, sealed, its
            # main frame went for the internet; or the browser did not close the page in time; or the
            # boundary was still judging a request when the page closed. Either way nothing of the page
            # comes back, a screenshot taken before included.
            if shot_path:
                with contextlib.suppress(OSError):
                    os.remove(shot_path)
            if refused:
                reached = f"the page reached for {', '.join(dict.fromkeys(refused))}, a local or private address"
                if grants:
                    return {"url": url, "error": f"{reached} the user did not type, which the connection "
                                                 f"boundary refused; {_SEALED_RULE}. {_LOCAL_RULE}"}
                return {"url": url, "error": f"{reached}, which the connection boundary refused. {_LOCAL_RULE}"}
            if moved:
                return {"url": url, "error": f"the page went to {moved}, outside the local destinations the "
                                             f"user typed, so nothing of it comes back; {_SEALED_RULE}. "
                                             f"{_LOCAL_RULE}"}
            if unclosed:
                return {"url": url, "error": f"the browser did not close the page within {_CLOSE_S:g}s, so "
                                             f"the fetch cannot be shown to be over; nothing of the page "
                                             f"comes back. {_LOCAL_RULE}"}
            return {"url": url, "error": f"the connection boundary was still judging {unsettled} request(s) "
                                         f"the page made when the page closed (a name that did not resolve "
                                         f"within {_SETTLE_S:g}s), so the page cannot be shown to have "
                                         f"reached only the public internet. {_LOCAL_RULE}"}
        if error:
            return {"url": url, "error": error}
        result = {
            "url": url,
            "final_url": final_url,
            "status": status,
            "title": title,
            "text": extract_text(html, final_url),
            "screenshot": shot_path,
        }
        if dropped:  # sealed: what the page asked the internet for and went without
            result["sealed_off"] = list(dict.fromkeys(dropped))
        return result

    async def aclose(self) -> None:
        reaper = self._reaper
        self._reaper = None
        if reaper is not None and not reaper.done():
            reaper.cancel()
            try:
                await reaper
            except Exception:
                pass
        await self._reset()


# Process-wide singleton so all tools share one warm browser.
_BROWSER: Browser | None = None


def get_browser() -> Browser:
    global _BROWSER
    if _BROWSER is None:
        _BROWSER = Browser()
    return _BROWSER
