"""Real Chromium actions on disposable pages; desktop control uses fixed fake I/O."""
import asyncio
import contextlib
import hashlib
import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import dream.computer as computer_module
from dream.computer import Computer, ComputerError
from dream.core import policy, sandbox_net
from dream.tools import computer_tools
from dream.tools.context import bind_context

PAGE = b'''<!doctype html><title>Fixture</title><h1>Two forms</h1>
<label>First<input id="first"></label><button onclick="document.querySelector('#a').textContent='saved first'">Save</button><p id="a"></p>
<label>Second<input id="second"></label><button onclick="document.querySelector('#b').textContent='saved '+document.querySelector('#second').value">Save</button><p id="b"></p>
<div style="height:1500px">Long content</div>'''

# The fixture origin as the controlled browser sees it (DREAM-187): a public name that resolves only through
# the boundary's own lookup, to a public address, and a wire on which every dial the boundary makes lands on
# the fixture server. Nothing here reaches a real network -- and a page the browser fetched outside the
# boundary could not load at all, the name resolving nowhere else.
PUBLIC = '93.184.216.34'
FIXTURE_HOST = 'fixture.example'
WIRE = []          # every (address, port) the boundary dialled since the fixture started
EXTRA_PAGES = {}   # path -> a raw HTTP response the fixture server gives instead of PAGE


@pytest.fixture(autouse=True)
def forbid_unmocked_native_input(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('Real native I/O is forbidden in computer-control tests')
    def forbidden_capture(*args, **kwargs):
        raise AssertionError('Real native capture is forbidden in computer-control tests')
    monkeypatch.setattr(computer_module.ImageGrab, 'grab', forbidden_capture)
    monkeypatch.setattr(computer_module, '_command', forbidden)


@pytest.fixture
async def target(tmp_path, monkeypatch):
    WIRE.clear()
    EXTRA_PAGES.clear()
    async def serve(reader, writer):
        try:
            head = await reader.readuntil(b'\r\n\r\n')
            path = head.split(b' ', 2)[1].decode(errors='replace')
            writer.write(EXTRA_PAGES.get(path) or
                         b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: '+str(len(PAGE)).encode()+b'\r\nConnection: close\r\n\r\n'+PAGE)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_server(serve, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    async def resolve(host):
        if host in (FIXTURE_HOST, 'other.example'):   # two public names, one fixture origin behind both
            return [PUBLIC]
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            raise OSError(-2, 'Name or service not known') from None
    async def dial(address, dialled_port):
        WIRE.append((address, dialled_port))
        if (address, dialled_port) != (PUBLIC, port):
            raise OSError('nothing else is on this wire')
        return await asyncio.open_connection('127.0.0.1', port)
    monkeypatch.setattr(sandbox_net, '_resolve', resolve)
    monkeypatch.setattr(sandbox_net, '_dial', dial)
    controller = Computer(tmp_path, tmp_path/'captures')
    url = f'http://{FIXTURE_HOST}:{port}'
    try:
        observed = await controller.open('browser', url=url)
        yield controller, observed
    finally:
        await controller.aclose()
        server.close()
        await server.wait_closed()


@pytest.fixture
async def private_service():
    """A loopback service standing for a private target (a router, the local engine, Studio): counts the
    connections it receives. One would prove the controlled browser reached past its boundary."""
    hits = []
    async def count(reader, writer):
        hits.append(1)
        writer.close()
    server = await asyncio.start_server(count, '127.0.0.1', 0)
    try:
        yield server.sockets[0].getsockname()[1], hits
    finally:
        server.close()
        await server.wait_closed()


# --- DREAM-187: the controlled browser reaches only the public internet, through its boundary -------------

async def test_the_real_browser_loads_through_the_boundary_by_the_address_judged(target):
    c, state = target
    owned = c.targets[state['target_id']]
    assert owned['boundary'].address[0] == '127.0.0.1' and owned['boundary'].proxy.startswith('http://127.0.0.1:')
    assert WIRE and set(WIRE) == {(PUBLIC, int(state['state']['url'].rsplit(':', 1)[1].split('/')[0]))}
    assert 'breach' not in owned and owned['boundary'].refused == []
    assert 'refused' not in state['state']


@pytest.mark.parametrize('way', ['redirect', 'frame', 'websocket', 'studio'])
async def test_the_real_browser_never_reaches_a_private_destination(target, private_service, monkeypatch, way):
    """A private destination asked for by a redirect hop, a child frame or a WebSocket handshake -- and
    Studio's own origin -- is refused at the boundary before any connection: the private service counts
    none, nothing is dialled but the fixture origin, the refusal is part of the next observation, and the
    page still works."""
    c, state = target
    ident = state['target_id']
    page, boundary = c.targets[ident]['page'], c.targets[ident]['boundary']
    port, hits = private_service
    private = f'http://127.0.0.1:{port}/'
    if way == 'studio':
        monkeypatch.setitem(sandbox_net.OWN_SERVICES, port, 'Studio')
    if way in ('redirect', 'studio'):
        EXTRA_PAGES['/hop'] = b'HTTP/1.1 302 Found\r\nLocation: '+private.encode()+b'\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'
        await page.goto(state['state']['url'].rstrip('/')+'/hop', wait_until='domcontentloaded')
    elif way == 'frame':
        await page.set_content(f'<iframe src="{private}"></iframe>')
    else:
        await page.evaluate(f'() => {{ new WebSocket("ws://127.0.0.1:{port}/"); }}')
    for _ in range(50):
        if boundary.refused:
            break
        await asyncio.sleep(.1)
    assert boundary.refused == [f'127.0.0.1:{port}']
    assert hits == []
    assert {address for address, _ in WIRE} == {PUBLIC}
    observed = await c.observe(ident)
    assert observed['state']['refused'] == [f'127.0.0.1:{port}']
    if way in ('redirect', 'studio'):   # the main frame shows the boundary's refusal, which names Studio when it is
        text = observed['state']['text']
        assert 'reaches only the public internet' in text
        assert ("is Dream's own Studio" in text) == (way == 'studio')
        assert ('local or private address' in text) == (way == 'redirect')


RTC = """async (stun) => {
  if (typeof RTCPeerConnection === 'undefined') return {rtc: false};
  const pc = new RTCPeerConnection({iceServers: [{urls: stun}]});
  pc.createDataChannel('x');
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise(r => setTimeout(r, 1500));
  const sdp = pc.localDescription ? pc.localDescription.sdp : '';
  const out = {rtc: true, state: pc.iceGatheringState, candidates: (sdp.match(/a=candidate/g) || []).length};
  pc.close();
  return out;
}"""


def _lan_address():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('10.255.255.255', 1))
            return probe.getsockname()[0]
    except OSError:
        return None


async def _stun_datagrams(page, host):
    """Run RTC on `page` against a UDP socket the test owns at `host` (a STUN server standing for a router or
    the local engine) -> (datagrams the browser sent it, datagrams a direct send from this process was
    seen as, the page's report). The direct send proves the socket sees what reaches it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, 0))
    sock.setblocking(False)
    port = sock.getsockname()[1]
    seen = []
    def on_read():
        try:
            while True:
                seen.append(sock.recvfrom(65535)[0])
        except BlockingIOError:
            pass
    loop = asyncio.get_running_loop()
    loop.add_reader(sock.fileno(), on_read)
    try:
        report = await asyncio.wait_for(page.evaluate(RTC, f'stun:{host}:{port}'), 20)
        await asyncio.sleep(.5)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.sendto(b'direct', (host, port))
        await asyncio.sleep(.3)
    finally:
        loop.remove_reader(sock.fileno())
        sock.close()
    direct = sum(1 for d in seen if d == b'direct')
    return len(seen) - direct, direct, report


@pytest.mark.parametrize('where', ['loopback', 'lan'])
async def test_the_controlled_browser_sends_no_webrtc_datagrams(target, where):
    """Codex round 1, item 5: WebRTC's ICE would reach this machine or its network over UDP by itself, past
    the HTTP proxy. The controlled browser is launched so that WebRTC uses no UDP outside a proxy: a page
    pointing an RTCPeerConnection at a STUN server on loopback, or on this machine's LAN address, sends it
    nothing -- while a direct send shows the socket sees datagrams, and the same page in a browser launched
    without the transport flags shows the browser would have sent them."""
    host = '127.0.0.1' if where == 'loopback' else (_lan_address() or pytest.skip('no LAN address on this machine'))
    c, state = target
    page = c.targets[state['target_id']]['page']
    sent, direct, report = await _stun_datagrams(page, host)
    assert direct == 1, 'the counting socket did not see a direct send'
    assert sent == 0, f'the controlled browser sent {sent} datagram(s) to {host}: {report}'
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:   # positive control: the same page, a browser without the flags
        controls = []
        for launch in ({}, {'channel': 'chromium'}):   # the headless shell, then full Chromium's new headless
            try:
                control = await pw.chromium.launch(headless=True, **launch)
            except Exception as exc:
                controls.append((launch, f'launch failed: {type(exc).__name__}'))
                continue
            try:
                control_page = await control.new_page()
                await control_page.set_content('<!doctype html><title>control</title>')
                control_sent, _, control_report = await _stun_datagrams(control_page, host)
                controls.append((launch, control_sent, control_report))
                if control_sent > 0:
                    break
            finally:
                await control.close()
    assert any(len(entry) == 3 and entry[1] > 0 for entry in controls), f'no positive control sent STUN datagrams: {controls}'


async def test_a_document_fetched_outside_the_boundary_closes_the_target(target, monkeypatch):
    """Fail closed: a navigation document whose reported connection address is not the boundary's (a
    proxy setting not honoured) breaches the target -- every observation and action refuses, the context
    is closed."""
    c, state = target
    ident = state['target_id']
    page = c.targets[ident]['page']
    class Elsewhere:
        def __init__(self, response):
            self.url, self.request = response.url, response.request
        async def server_addr(self):
            return {'ipAddress': '127.0.0.1', 'port': 1}
    original = c._verify_through_boundary
    monkeypatch.setattr(c, '_verify_through_boundary', lambda target, response: original(target, Elsewhere(response)))
    await page.reload()
    with pytest.raises(ComputerError, match='breached its network boundary'):
        await c.observe(ident)
    with pytest.raises(ComputerError):   # the failed observation consumed the token; a fresh one is refused too
        await c.act(ident, state['observation_id'], 'click', element_id='1')
    with pytest.raises(ComputerError, match='breached its network boundary'):
        await c.observe(ident)
    assert page.is_closed()


@pytest.mark.parametrize('how', ['raises', 'hangs'])
async def test_a_document_check_that_fails_or_hangs_closes_the_target(target, monkeypatch, how):
    """A boundary check the browser cannot answer -- server_addr() raising, or never completing within the
    deadline -- is a breach, not a pass: the target is closed and refuses before anything is read from or
    done to its document."""
    c, state = target
    ident = state['target_id']
    page = c.targets[ident]['page']
    monkeypatch.setattr(computer_module, '_CHECK_S', 0.3, raising=False)
    class Unanswerable:
        def __init__(self, response):
            self.url, self.request = response.url, response.request
        async def server_addr(self):
            if how == 'raises':
                raise RuntimeError('protocol error')
            await asyncio.Event().wait()
    original = c._verify_through_boundary
    monkeypatch.setattr(c, '_verify_through_boundary', lambda target, response: original(target, Unanswerable(response)))
    await page.reload()
    with pytest.raises(ComputerError, match='breached its network boundary.*(protocol error|did not complete within)'):
        await c.observe(ident)
    assert page.is_closed()
    with pytest.raises(ComputerError, match='breached its network boundary'):
        await c.act(ident, state['observation_id'], 'key', key='Tab')


def _document(url, server_addr):
    """A navigation response as the check sees it: its URL and the address the browser reports."""
    return SimpleNamespace(url=url, request=SimpleNamespace(is_navigation_request=lambda: True), server_addr=server_addr)


async def _hangs():
    await asyncio.Event().wait()


async def test_a_check_arriving_during_verification_is_awaited_before_the_page_is_touched(target, monkeypatch):
    """Codex round 2: a document (a child frame, a hop) arriving while earlier checks are awaited adds its
    own check. The live set is drained under one deadline: the second, hanging check holds the observation,
    which refuses and closes the target without asking the page anything."""
    c, state = target
    ident = state['target_id']
    owned = c.targets[ident]
    page = owned['page']
    monkeypatch.setattr(computer_module, '_CHECK_S', 0.3)
    boundary = owned['boundary'].address
    async def passes_and_adds_a_hanging_one():
        c._verify_through_boundary(owned, _document(page.url + 'second', _hangs))
        return {'ipAddress': boundary[0], 'port': boundary[1]}
    touched = []
    real_evaluate = page.evaluate
    async def evaluate(expression, *a, **kw):
        touched.append(expression[:40])
        return await real_evaluate(expression, *a, **kw)
    monkeypatch.setattr(page, 'evaluate', evaluate)
    c._verify_through_boundary(owned, _document(page.url + 'first', passes_and_adds_a_hanging_one))
    with pytest.raises(ComputerError, match='breached its network boundary.*did not complete within'):
        await c.observe(ident)
    assert touched == []
    assert page.is_closed()


async def test_checks_chaining_during_verification_hold_an_action_until_they_finish(target, monkeypatch):
    """Codex round 2: the first check adds a second, the second a hanging third. The action is refused
    before any input goes out: no keydown, no keyup, no dispatched result -- the target closed."""
    c, state = target
    ident = state['target_id']
    owned = c.targets[ident]
    page = owned['page']
    monkeypatch.setattr(computer_module, '_CHECK_S', 0.3)
    boundary = owned['boundary'].address
    ok = {'ipAddress': boundary[0], 'port': boundary[1]}
    pressed = []
    real_down, real_up = page.keyboard.down, page.keyboard.up
    async def down(key):
        pressed.append(('down', key))
        await real_down(key)
    async def up(key):
        pressed.append(('up', key))
        await real_up(key)
    monkeypatch.setattr(page.keyboard, 'down', down)
    monkeypatch.setattr(page.keyboard, 'up', up)
    async def second():
        await asyncio.sleep(.15)   # still pending when the first is done; the third arrives only as this completes
        c._verify_through_boundary(owned, _document(page.url + 'third', _hangs))
        return ok
    async def first():
        c._verify_through_boundary(owned, _document(page.url + 'second', second))
        await asyncio.sleep(.05)   # done while the second is still pending
        return ok
    c._verify_through_boundary(owned, _document(page.url + 'first', first))
    with pytest.raises(ComputerError, match='breached its network boundary.*did not complete within'):
        await c.act(ident, state['observation_id'], 'key', key='Tab')
    assert pressed == []
    assert page.is_closed()


async def test_a_check_cancelled_before_it_starts_is_a_breach(target):
    """Codex round 2, the suspected edge: a check task cancelled before its body runs recorded nothing. The
    verdict is structural now -- a check that ends cancelled or failed is a breach however it ended."""
    c, state = target
    ident = state['target_id']
    owned = c.targets[ident]
    page = owned['page']
    c._verify_through_boundary(owned, _document(page.url + 'doc', _hangs))
    for check in list(owned['checks']):
        check.cancel()   # before the task ever ran
    with pytest.raises(ComputerError, match='breached its network boundary.*cancelled before it completed'):
        await c.observe(ident)
    assert page.is_closed()


def _arriving_during_the_dom_read(c, owned, page, monkeypatch):
    """A document (a child frame: main document and navigation count unchanged) arriving while the DOM is
    read, registering a hanging check meanwhile: the DOM evaluation is wrapped, everything else untouched."""
    real = page.evaluate
    async def evaluate(expression, *a, **kw):
        if expression is computer_module._DOM:
            c._verify_through_boundary(owned, _document(page.url + 'frame', _hangs))
        return await real(expression, *a, **kw)
    monkeypatch.setattr(page, 'evaluate', evaluate)


async def test_state_is_not_handed_out_with_a_check_that_arrived_during_the_dom_read(target, monkeypatch):
    """Codex round 3: the state read drains again after the DOM evaluation, so a check registered during
    it is awaited before the state is returned."""
    c, state = target
    owned = c.targets[state['target_id']]
    page = owned['page']
    monkeypatch.setattr(computer_module, '_CHECK_S', 0.3)
    _arriving_during_the_dom_read(c, owned, page, monkeypatch)
    with pytest.raises(ComputerError, match='breached its network boundary.*did not complete within'):
        await c._state(owned)
    assert page.is_closed()


async def test_a_check_arriving_during_the_dom_read_holds_the_action(target, monkeypatch):
    """Codex round 3's reproduction: act(key Tab) with a document arriving during the DOM read. No keydown,
    no keyup, no dispatched result: refused, the target closed."""
    c, state = target
    ident = state['target_id']
    owned = c.targets[ident]
    page = owned['page']
    monkeypatch.setattr(computer_module, '_CHECK_S', 0.3)
    _arriving_during_the_dom_read(c, owned, page, monkeypatch)
    pressed = []
    real_down, real_up = page.keyboard.down, page.keyboard.up
    async def down(key):
        pressed.append(('down', key))
        await real_down(key)
    async def up(key):
        pressed.append(('up', key))
        await real_up(key)
    monkeypatch.setattr(page.keyboard, 'down', down)
    monkeypatch.setattr(page.keyboard, 'up', up)
    with pytest.raises(ComputerError, match='breached its network boundary.*did not complete within'):
        await c.act(ident, state['observation_id'], 'key', key='Tab')
    assert pressed == []
    assert page.is_closed()


async def test_a_check_arriving_mid_chord_stops_the_chord_and_releases_what_was_pressed(target, monkeypatch):
    """A document arrives as the first key of a chord goes down: the drain before the next key refuses, the
    pressed key is released on the live page, the refusal says what went out, and the target is closed."""
    c, state = target
    ident = state['target_id']
    owned = c.targets[ident]
    page = owned['page']
    monkeypatch.setattr(computer_module, '_CHECK_S', 0.3)
    pressed, released = [], []
    real_down, real_up = page.keyboard.down, page.keyboard.up
    async def down(key):
        pressed.append(key)
        await real_down(key)
        if key == 'Control':
            c._verify_through_boundary(owned, _document(page.url + 'frame', _hangs))
    async def up(key):
        released.append(key)
        await real_up(key)
    monkeypatch.setattr(page.keyboard, 'down', down)
    monkeypatch.setattr(page.keyboard, 'up', up)
    with pytest.raises(ComputerError, match='breached its network boundary.*Control pressed; nothing further dispatched'):
        await c.act(ident, state['observation_id'], 'key', key='Control+z')
    assert pressed == ['Control'] and released == ['Control']
    assert page.is_closed()


async def test_a_check_failing_with_an_unprintable_exception_is_still_a_breach(target):
    """Codex round 3, finding 5: an exception whose text cannot be formatted must not defeat the verdict --
    the breach is recorded before any detail is formatted."""
    c, state = target
    ident = state['target_id']
    owned = c.targets[ident]
    page = owned['page']
    class Unprintable(Exception):
        def __str__(self):
            raise RuntimeError('broken exception formatter')
    async def raises():
        raise Unprintable()
    c._verify_through_boundary(owned, _document(page.url + 'doc', raises))
    with pytest.raises(ComputerError, match=r'breached its network boundary.*failed: Unprintable \(its text could not be formatted\)'):
        await c.observe(ident)
    assert page.is_closed()


async def test_a_document_arriving_during_handle_disposal_stops_the_press(target, monkeypatch):
    """Codex round 4, item 1: hit.dispose() awaits a protocol reply, and a document arriving during it
    registers a check; the last drain follows the disposal, so the press never goes out -- no mouse.down,
    no unmatched mouse.up -- and the refusal says only that the pointer was moved."""
    c, state = target
    ident = state['target_id']
    owned = c.targets[ident]
    page = owned['page']
    monkeypatch.setattr(computer_module, '_CHECK_S', 0.3)
    buttons = []
    real_down, real_up = page.mouse.down, page.mouse.up
    async def down(**kw):
        buttons.append('down')
        await real_down(**kw)
    async def up(**kw):
        buttons.append('up')
        await real_up(**kw)
    monkeypatch.setattr(page.mouse, 'down', down)
    monkeypatch.setattr(page.mouse, 'up', up)
    real_handle = page.evaluate_handle
    async def evaluate_handle(expression, *a, **kw):
        handle = await real_handle(expression, *a, **kw)
        if 'elementFromPoint' in expression:
            real_dispose = handle.dispose
            async def dispose():
                c._verify_through_boundary(owned, _document(page.url + 'frame', _hangs))   # arrives during the disposal's wait
                await real_dispose()
            handle.dispose = dispose
        return handle
    monkeypatch.setattr(page, 'evaluate_handle', evaluate_handle)
    with pytest.raises(ComputerError, match='breached its network boundary.*The pointer was moved; nothing further dispatched') as refusal:
        await c.act(ident, state['observation_id'], 'click', x=10, y=10)
    assert buttons == []
    assert 'No input dispatched' not in str(refusal.value)
    assert page.is_closed()


async def test_a_document_arriving_during_the_final_key_press_is_reported_truthfully(target, monkeypatch):
    """Codex round 4, item 2: a check registered during the action's last press: the key is pressed and
    released, the observation that follows refuses, and its error says the keys were pressed -- never
    'No input dispatched'."""
    c, state = target
    ident = state['target_id']
    owned = c.targets[ident]
    page = owned['page']
    monkeypatch.setattr(computer_module, '_CHECK_S', 0.3)
    real_down = page.keyboard.down
    async def down(key):
        await real_down(key)
        c._verify_through_boundary(owned, _document(page.url + 'frame', _hangs))   # arrives as the press completes
    monkeypatch.setattr(page.keyboard, 'down', down)
    result = await c.act(ident, state['observation_id'], 'key', key='Tab')
    assert result['action_result']['action_dispatched'] is True
    error = result['observation_error']['message']
    assert 'breached its network boundary' in error
    assert 'The keys were pressed and released; nothing further dispatched' in error
    assert 'No input dispatched' not in error
    assert page.is_closed()


# --- Codex round 4, item 3, then round 5, items 3 and 4: every suspension point of every browser input path ------

_REAL = SimpleNamespace(sleep=asyncio.sleep, wait=asyncio.wait, shield=asyncio.shield)   # taken before any test patches them


class _Suspensions:
    """Every suspension point of an act() run goes through one hook: the fake page's Playwright calls, by name, and
    asyncio's own -- asyncio.sleep (a paced stroke's pauses, the settling loop's), the drain's asyncio.wait, the
    release's asyncio.shield -- patched to note themselves; each entry carries the task it was noted from. A fresh
    input made while a document check is pending is a violation; and a document (a hanging check) can be made to
    arrive at any one point, chosen by its index in the trace."""
    FRESH = {'mouse.move', 'mouse.down', 'mouse.wheel', 'keyboard.down', 'element.click', 'element.fill'}   # releases are cleanup

    def __init__(self):
        self.trace, self.violations, self.went_out = [], [], []
        self.arrive_at, self.arrive, self.arrived = None, lambda: None, False
        self.checks = set()

    def note(self, name):
        index = len(self.trace)
        self.trace.append((name, asyncio.current_task()))
        if name in self.FRESH:
            if self.checks:
                self.violations.append((index, name))
            self.went_out.append(name)
        if index == self.arrive_at:
            self.arrived = True
            self.arrive()   # the document arrives during this suspension

    async def suspend(self, name):
        self.note(name)
        await _REAL.sleep(0)

    def points(self):
        return [name for name, _ in self.trace]

    def install(self, monkeypatch):
        """asyncio's suspension points as computer.py reaches them: a sleep yields once in place of its pause (its
        length is not under test); a wait and a shield are noted and go on to the real ones."""
        async def sleep(delay, result=None):
            self.note('asyncio.sleep')
            await _REAL.sleep(0)
            return result

        async def wait(fs, *, timeout=None, return_when=asyncio.ALL_COMPLETED):
            self.note('asyncio.wait')
            return await _REAL.wait(fs, timeout=timeout, return_when=return_when)

        def shield(arg):
            self.note('asyncio.shield')
            return _REAL.shield(arg)
        monkeypatch.setattr(asyncio, 'sleep', sleep)
        monkeypatch.setattr(asyncio, 'wait', wait)
        monkeypatch.setattr(asyncio, 'shield', shield)


class _FakeHandle:
    def __init__(self, browser, name):
        self._browser, self._name = browser, name

    def as_element(self):
        return self

    async def evaluate(self, expression, *args):
        return await self._browser._await(f'{self._name}.evaluate', expression)

    async def get_properties(self):
        return await self._browser._await(f'{self._name}.get_properties')

    async def dispose(self):
        return await self._browser._await(f'{self._name}.dispose')

    async def click(self, timeout=None):
        return await self._browser._await('element.click')

    async def fill(self, text, timeout=None):
        return await self._browser._await('element.fill', text)


class _FakeBrowser:
    """A page and its handles whose every awaited Playwright call is a point of the hook's, answered with fixed
    state and pixels."""
    DOM = {'url': 'http://fixture.example/', 'title': 'Fixture', 'viewport': [1280, 800], 'scroll': [0, 0], 'focus': -1,
           'text': 'Fixture', 'note': '',
           'elements': [{'id': '1', 'tag': 'button', 'role': '', 'name': 'Go', 'type': '', 'value': '', 'href': '', 'formAction': '',
                         'formMethod': '', 'disabled': False, 'checked': False, 'rect': [0, 0, 10, 10], 'visible': True}]}

    def __init__(self, hook):
        self.hook = hook
        self.url = 'http://fixture.example/'
        self.mouse = SimpleNamespace(move=self._call('mouse.move'), down=self._call('mouse.down'),
                                     up=self._call('mouse.up'), wheel=self._call('mouse.wheel'))
        self.keyboard = SimpleNamespace(down=self._call('keyboard.down'), up=self._call('keyboard.up'))

    def _call(self, name):
        async def call(*args, **kwargs):
            return await self._await(name, *args)
        return call

    async def evaluate(self, expression, *args):
        return await self._await('page.evaluate', expression)

    async def evaluate_handle(self, expression, *args):
        return await self._await('page.evaluate_handle', expression)

    async def screenshot(self, **kwargs):
        return await self._await('page.screenshot')

    async def wait_for_timeout(self, ms):
        return await self._await('page.wait_for_timeout')

    def is_closed(self):
        return False

    async def _await(self, name, *args):
        await self.hook.suspend(name)
        if name == 'page.evaluate' and args and args[0] is computer_module._DOM:
            return json.loads(json.dumps(self.DOM))
        if name == 'page.evaluate_handle':
            return _FakeHandle(self, 'handle')
        if name == 'handle.get_properties':
            return {'1': _FakeHandle(self, 'element')}
        if name == 'page.screenshot':
            return b'png'
        if name.endswith('.evaluate'):
            return True
        return None


class _Stepping(asyncio.tasks._PyTask):
    """asyncio's pure-Python Task with its steps counted: one for the start, then one each time the coroutine resumes
    after a suspension -- whatever it suspended on -- so steps - 1 is how many times act() suspended."""

    def __init__(self, coro, *, loop):
        self.steps = 0
        super().__init__(coro, loop=loop)

    def _Task__step(self, exc=None):
        self.steps += 1
        return super()._Task__step(exc)


def _fake_browser_target(tmp_path, monkeypatch):
    """A Computer owning one browser target on a _FakeBrowser, observed and ready to act on, every suspension point
    hooked -> (hook, computer, target). computer.py's clock is a counter (0.2 s a reading), so the real settling
    loop after an action takes its readings and finishes the same way every run, with no waiting."""
    hook = _Suspensions()
    hook.install(monkeypatch)
    clock = [time.monotonic()]

    def monotonic():
        clock[0] += 0.2
        return clock[0]
    monkeypatch.setattr(computer_module, 'time', SimpleNamespace(monotonic=monotonic))
    browser = _FakeBrowser(hook)
    c = Computer(tmp_path, tmp_path / 'caps')
    state = {**json.loads(json.dumps(browser.DOM)), 'navigation': 0}

    async def close():
        await hook.suspend('context.close')
    target = {'kind': 'browser', 'context': SimpleNamespace(close=close), 'page': browser, 'navigation': 0,
              'boundary': SimpleNamespace(address=('127.0.0.1', 1), refused=[], close=lambda: None), 'checks': hook.checks,
              'observation': 'o', 'observed': time.monotonic(), 'fingerprint': computer_module._fingerprint(state),
              'pixels_sha256': hashlib.sha256(b'png').hexdigest(), 'elements': {'1': _FakeHandle(browser, 'element')},
              'visible_ids': {'1'}}
    c.targets['t'] = target
    hook.arrive = lambda: c._verify_through_boundary(target, _document('http://fixture.example/frame', _hangs))
    return hook, c, target


async def _act(c, action, args):
    """act() run as a stepping task -> (what it returned, or the ComputerError it raised; the task)."""
    task = _Stepping(c.act('t', 'o', action, **args), loop=asyncio.get_running_loop())
    try:
        return await task, task
    except ComputerError as exc:
        return exc, task


def _instrumented(hook, task, where):
    """Every suspension of act() was a point of the hook's: the task resumed exactly as many times as the hook noted
    a point from it (a point noted from the release's own cleanup task is that task's suspension, not act()'s)."""
    noted = sum(1 for _, noted_from in hook.trace if noted_from is task)
    assert task.steps - 1 == noted, f'{where}: {task.steps - 1 - noted} suspension(s) of act() not instrumented; points: {hook.points()}'


_INPUT_PATHS = {
    'coordinate click': ('click', {'x': 5, 'y': 5}),
    'drag': ('drag', {'points': [{'x': 1, 'y': 1}, {'x': 3, 'y': 3}]}),
    'stroke': ('stroke', {'points': [{'x': 1, 'y': 1}, {'x': 3, 'y': 3}, {'x': 5, 'y': 5}]}),
    'paced stroke': ('stroke', {'points': [{'x': 1, 'y': 1}, {'x': 3, 'y': 3}], 'duration_ms': 30}),
    'element click': ('click', {'element_id': '1'}),
    'element fill': ('type', {'element_id': '1', 'text': 'hi'}),
    'key': ('key', {'key': 'Tab'}),
    'chord': ('key', {'key': 'Control+z'}),
    'wheel': ('scroll', {'dy': 100}),
}
_COORDINATE = {'coordinate click', 'drag', 'stroke', 'paced stroke'}
# What a refusal says once the whole action went out (the observation's refusal, in the action's own words) -- spelled
# out here, not read from computer.py, so a wrong description there fails the test.
_DONE = {'click': 'The click was dispatched', 'type': 'The text was typed', 'key': 'The keys were pressed and released',
         'scroll': 'The wheel was scrolled', 'drag': 'The drag was dispatched and the button released',
         'stroke': 'The stroke was dispatched and the button released'}
_SAYINGS = set(_DONE.values()) | {'No input dispatched', 'The pointer was moved', 'The button was pressed and is released', 'Control pressed'}


def _truthful(path, action, result, went_out, planned):
    """What a refusal must say of the input recorded as gone out before it. Raised during the action (a ComputerError):
    nothing, or the part that went, in that part's words -- for a stroke 'pressed' even after its last move, the
    button being held until the release that follows. Raised by the observation (an error in the result): the whole
    action, in its own words, and the record must show the whole of it out."""
    if not isinstance(result, ComputerError):
        assert went_out == planned and result['action_result']['action_dispatched'] is True, (went_out, planned, result)
        return _DONE[action]
    if not went_out:
        return 'No input dispatched'
    if path in _COORDINATE:
        return 'The button was pressed and is released' if 'mouse.down' in went_out else 'The pointer was moved'
    if path == 'chord' and went_out == ['keyboard.down']:
        return 'Control pressed'
    pytest.fail(f'{path}: a refusal during the action after {went_out} of {planned}, which no wording covers')


def _said(message, expected, where):
    """The refusal says `expected` of what went out, says nothing else of the kind, and closes with 'nothing further
    dispatched' when something did."""
    said = {saying for saying in _SAYINGS if saying in message}
    assert said == {expected}, f'{where}: the refusal says {said or "nothing of what went out"}, expected {expected!r}: {message}'
    if expected != 'No input dispatched':
        assert f'{expected}; nothing further dispatched.' in message, (where, message)


@pytest.mark.parametrize('path', list(_INPUT_PATHS))
async def test_no_fresh_input_goes_out_with_a_check_pending_whatever_suspension_the_document_arrives_at(tmp_path, monkeypatch, path):
    """Codex round 4, item 3, then round 5, items 3 and 4: for every browser input path, a document is made to arrive
    at each suspension point of the whole act() in turn -- the points counted from an undisturbed run: the Playwright
    calls, and asyncio.sleep (a paced stroke's pauses, the settling loop's), the drain's wait and the release's
    shield; and the run's task counts its own resumptions, so a suspension the hook did not see fails the test rather
    than going uncovered. For each: no fresh input goes out while its check is pending; the boundary refusal is
    raised (every point is followed by a drain); and the refusal says what the recorded dispatches show went out --
    nothing, the part that went, or the whole action in its own words -- and nothing else."""
    action, args = _INPUT_PATHS[path]
    monkeypatch.setattr(computer_module, '_CHECK_S', 0.05)
    hook, c, target = _fake_browser_target(tmp_path, monkeypatch)
    result, task = await _act(c, action, args)
    assert not isinstance(result, ComputerError) and 'observation_error' not in result, result
    _instrumented(hook, task, path)
    points, planned = hook.points(), list(hook.went_out)
    assert planned and not hook.violations, (planned, hook.violations)
    last = max(i for i, p in enumerate(points) if p in _Suspensions.FRESH)
    assert 'asyncio.sleep' in points[last:], points                                     # the settling loop's pauses are points
    if path == 'paced stroke':
        assert 'asyncio.sleep' in points[points.index('mouse.down'):last], points       # and so are the stroke's
    for index, name in enumerate(points):
        hook, c, target = _fake_browser_target(tmp_path, monkeypatch)
        hook.arrive_at = index
        result, task = await _act(c, action, args)
        pending = list(target['checks'])
        for check in pending:
            check.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        where = f'{path}, arrival at point {index} ({name})'
        assert hook.arrived, where
        assert not hook.violations, f'{where}: {hook.violations} went out with a check pending; points: {hook.points()}'
        _instrumented(hook, task, where)
        message = str(result) if isinstance(result, ComputerError) else (result.get('observation_error') or {}).get('message')
        assert message is not None and 'breached its network boundary' in message, (where, result)
        _said(message, _truthful(path, action, result, hook.went_out, planned), where)
    print(f'[exhaustive] {path}: {len(points)} points, one arrival each = {len(points)} cases; points: {points}')


async def test_actual_form_actions_preserve_other_form(target):
    c, state = target
    ident = state['target_id']
    field = next(e for e in state['state']['elements'] if e['name']=='Second')
    filled = await c.act(ident, state['observation_id'], 'type', element_id=field['id'], text='launch v2')
    buttons = [e for e in filled['state']['elements'] if e['name']=='Save']
    saved = await c.act(ident, filled['observation_id'], 'click', element_id=buttons[1]['id'])
    page = c.targets[ident]['page']
    assert await page.locator('#b').inner_text() == 'saved launch v2'
    assert await page.locator('#a').inner_text() == ''
    assert await page.locator('#first').input_value() == ''
    assert Path(saved['screenshot']).read_bytes().startswith(b'\x89PNG')
    assert saved['action_result']['task_success']=='unverified'
    assert saved['observation_id'] != filled['observation_id']


@pytest.mark.parametrize('mutation', ['resize', 'dom', 'focus', 'navigation'])
async def test_changed_state_refuses_old_action(target, mutation):
    c, old = target
    page = c.targets[old['target_id']]['page']
    if mutation == 'resize':
        await page.set_viewport_size({'width':600,'height':500})
    elif mutation == 'dom':
        await page.locator('#second').fill('other user edit')
    elif mutation == 'focus':
        await page.locator('#second').focus()
    else:
        await page.reload()
    with pytest.raises(ComputerError, match='state changed'):
        await c.act(old['target_id'], old['observation_id'], 'click', element_id='1')
    assert await page.locator('#a').inner_text()==''


async def test_timeout_consumes_token_and_does_not_replay_completed_save(target, monkeypatch):
    c, old = target
    original=c._browser_action
    calls=[]
    async def timeout_after_action(*args):
        calls.append(True)
        await original(*args)
        raise TimeoutError('response lost after click')
    monkeypatch.setattr(c, '_browser_action', timeout_after_action)
    with pytest.raises(ComputerError, match='outcome is uncertain'):
        await c.act(old['target_id'], old['observation_id'], 'click', element_id='1')
    with pytest.raises(ComputerError, match='already used'):
        await c.act(old['target_id'], old['observation_id'], 'click', element_id='1')
    observed=await c.observe(old['target_id'])
    assert 'saved first' in observed['state']['text']
    assert len(calls)==1


async def test_session_ownership_and_close(target, tmp_path):
    c, state=target
    other=Computer(tmp_path,tmp_path/'other')
    with pytest.raises(ComputerError,match='Unknown target'):
        await other.observe(state['target_id'])
    await c.close(state['target_id'])
    with pytest.raises(ComputerError,match='Unknown target'):
        await c.observe(state['target_id'])


async def test_key_and_scroll_return_new_observations(target):
    c,state=target
    keyed=await c.act(state['target_id'],state['observation_id'],'key',key='Tab')
    assert keyed['state']['focus'] >= 0
    scrolled=await c.act(state['target_id'],keyed['observation_id'],'scroll',dy=400)
    assert scrolled['state']['scroll'][1] > 0


@pytest.mark.parametrize('url',['file:///etc/passwd','javascript:alert(1)','data:text/html,hello','about:blank'])
async def test_browser_open_rejects_other_schemes(tmp_path,url):
    c=Computer(tmp_path,tmp_path/'captures')
    with pytest.raises(ComputerError,match='http or https'):
        await c.open('browser',url=url)
    assert c._pw is None


def test_permissions_keep_real_actions_gated_in_auto_and_plan(tmp_path):
    assert policy.decide('computer_observe',{},'plan',tmp_path)[0]=='allow'
    for name in ['computer_open','computer_action','computer_close']:
        assert policy.decide(name,{},'auto',tmp_path)[0]=='ask'
        assert policy.decide(name,{},'plan',tmp_path)[0]=='deny'


async def test_wayland_reports_unavailable_without_running_desktop_commands(tmp_path,monkeypatch):
    monkeypatch.setenv('XDG_SESSION_TYPE','wayland')
    c=Computer(tmp_path,tmp_path/'captures')
    assert not c.capabilities()['desktop']
    with pytest.raises(ComputerError,match='Wayland'):
        await c.open('desktop',window_id='123')


async def test_desktop_geometry_change_refuses_dispatch(tmp_path,monkeypatch):
    c=Computer(tmp_path,tmp_path/'captures')
    monkeypatch.setattr(c,'capabilities',lambda:{'desktop':True})
    state={'window_id':'123','focus':'123','title':'Fixture','rect':[0,0,800,600]}
    async def read(_):return dict(state)
    async def capture(*_):return None
    monkeypatch.setattr(c,'_desktop_state',read)
    monkeypatch.setattr(c,'_capture',capture)
    observed=await c.open('desktop',window_id='123')
    state['rect']=[0,0,400,300]
    with pytest.raises(ComputerError,match='state changed'):
        await c.act(observed['target_id'],observed['observation_id'],'click',x=20,y=30)


async def test_text_only_endpoint_gets_dom_without_unseen_image_claim(target):
    c,state=target
    context=SimpleNamespace(computer=c,multimodal=False)
    with bind_context(context):
        out=await computer_tools.computer_observe.handler({'target_id':state['target_id']})
    assert all(part['type']=='text' for part in out['content'])
    text=out['content'][0]['text']
    assert text.startswith('<untrusted_web_content id="')  # DREAM-187: page text is marked as data
    body=json.loads(text.split('\n',2)[2].rsplit('\n',1)[0])
    assert 'unavailable' in body['image_note'] and body['state']['elements']


async def test_changed_destination_and_replaced_node_reject_old_click(target):
    c,first=target
    ident=first['target_id'];page=c.targets[ident]['page']
    await page.evaluate("document.body.insertAdjacentHTML('afterbegin','<a href=\"/one\">Next</a>')")
    observed=await c.observe(ident)
    await page.locator('a').evaluate("e=>e.href='/two'")
    with pytest.raises(ComputerError,match='state changed'):
        await c.act(ident,observed['observation_id'],'click',element_id='0')
    observed=await c.observe(ident)
    await page.locator('a').evaluate('e=>e.replaceWith(e.cloneNode(true))')
    with pytest.raises(ComputerError,match='replaced'):
        await c.act(ident,observed['observation_id'],'click',element_id='0')
    assert not page.url.endswith('/two')


async def test_observation_images_are_immutable(target):
    c,first=target
    image=Path(first['screenshot']);data=image.read_bytes()
    await c.targets[first['target_id']]['page'].evaluate("document.body.style.background='red'")
    second=await c.observe(first['target_id'])
    assert second['screenshot'] != first['screenshot']
    assert image.read_bytes()==data
    assert Path(second['screenshot']).read_bytes()!=data


async def test_unused_targeting_parameters_are_rejected_without_consuming_observation(target):
    c,state=target
    with pytest.raises(ComputerError,match='unused parameters'):
        await c.act(state['target_id'],state['observation_id'],'key',key='Tab',element_id='2')
    assert c.targets[state['target_id']]['observation']==state['observation_id']


async def test_desktop_scroll_moves_inside_selected_window_and_revalidates_before_clicking(tmp_path, monkeypatch, plain_process):
    c = Computer(tmp_path, tmp_path/'captures')
    calls, _ = _x11_client(monkeypatch, c, pid=str(plain_process))
    observed = await c.open('desktop', window_id='123')
    calls.clear()
    await c.act(observed['target_id'], observed['observation_id'], 'scroll', dy=400)
    inputs = [call for call in calls if call[0] in ('mousemove', 'click')]
    assert inputs[0] == ('mousemove', '--window', '123', '400', '300')
    assert inputs[1] == ('click', '--repeat', '4', '5')
    between = calls[calls.index(inputs[0])+1:calls.index(inputs[1])]   # the whole state re-read between move and click
    assert {'-id', 'getwindowname', 'getwindowfocus'} <= {call[0] for call in between}


async def test_failed_observation_invalidates_prior_handles(target,monkeypatch):
    c,old=target;ident=old['target_id'];page=c.targets[ident]['page']
    await page.locator('button').first.evaluate('e=>e.replaceWith(e.cloneNode(true))')
    async def failed(*_):raise TimeoutError('capture failed')
    monkeypatch.setattr(c,'_capture',failed)
    with pytest.raises(TimeoutError):
        await c.observe(ident)
    with pytest.raises(ComputerError,match='stale or already used'):
        await c.act(ident,old['observation_id'],'click',element_id='1')
    assert await page.locator('#a').inner_text()==''


async def test_changed_containing_form_destination_refuses_old_submit(target):
    c,old=target;ident=old['target_id'];page=c.targets[ident]['page']
    await page.evaluate("document.body.innerHTML='<form action=\"/one\" method=\"post\"><button>Submit</button></form>'")
    observed=await c.observe(ident)
    assert observed['state']['elements'][0]['formAction'].endswith('/one')
    await page.locator('form').evaluate("e=>e.action='/two'")
    with pytest.raises(ComputerError,match='state changed'):
        await c.act(ident,observed['observation_id'],'click',element_id='0')
    assert not page.url.endswith('/two')


async def test_large_escaped_observations_page_under_actual_transport_cap(target):
    from dream import config
    c,old=target;ident=old['target_id'];page=c.targets[ident]['page']
    await page.evaluate('''() => {document.body.innerHTML='';for(let i=0;i<60;i++){
      const b=document.createElement('button');b.textContent='Control '+i+'\\x00'.repeat(200);
      b.onclick=()=>document.title='chosen '+i;document.body.append(b);}}''')
    state=await c.observe(ident)
    assert len(json.dumps(state,ensure_ascii=False))<config.TOOL_RESULT_CAP
    assert state['state']['next_element_offset'] is not None
    first_ids={e['id'] for e in state['state']['elements']}
    with pytest.raises(ComputerError,match='enabled element_id'):
        await c.act(ident,state['observation_id'],'click',element_id='59')
    state=await c.observe(ident,offset=state['state']['next_element_offset'])
    assert not first_ids.intersection(e['id'] for e in state['state']['elements'])
    assert len(json.dumps(state,ensure_ascii=False))<config.TOOL_RESULT_CAP
    entry=next(e for e in state['state']['elements'] if e['visible'])
    done=await c.act(ident,state['observation_id'],'click',element_id=entry['id'])
    assert done['state']['title']=='chosen '+entry['id']
    assert len(json.dumps(done,ensure_ascii=False))<config.TOOL_RESULT_CAP


async def test_browser_observations_fit_the_transport_cap_with_their_untrusted_content_wrapper(target):
    """DREAM-187: the model receives a browser observation inside untrusted_web_content, whose own tags and
    the escapes of the page's copies (three characters each) count against the same transport cap; a page
    cannot push the closing tag, or the action result after it, past the cut."""
    from dream import config
    c,state=target;ident=state['target_id'];page=c.targets[ident]['page']
    budget=config.TOOL_RESULT_CAP-1800
    base=len(json.dumps(await c.observe(ident),ensure_ascii=False))-len('Fixture')
    tag='<untrusted_web_content>'
    await page.evaluate('t=>{document.title=t}',tag*((budget-base-100)//len(tag)))   # the JSON alone fits
    with bind_context(SimpleNamespace(computer=c,multimodal=False)):
        out=await computer_tools.computer_observe.handler({'target_id':ident})
    text=out['content'][0]['text']
    assert len(text)<=config.TOOL_RESULT_CAP
    opening,_note,rest=text.split('\n',2)
    body,closing=rest.rsplit('\n',1)
    assert closing=='</'+opening[1:]
    assert '&lt;untrusted_web_content>' in json.loads(body)['state']['title']


async def prepare_canvas(c, ident):
    page = c.targets[ident]['page']
    await page.set_content('''<style>body{margin:0}</style><canvas width=400 height=300></canvas><script>
    const c=document.querySelector('canvas'),ctx=c.getContext('2d');let down=false;
    c.onmousedown=e=>{down=true;ctx.beginPath();ctx.moveTo(e.offsetX,e.offsetY)};
    c.onmousemove=e=>{if(down){ctx.lineTo(e.offsetX,e.offsetY);ctx.stroke()}};
    window.onmouseup=()=>down=false;
    window.onkeydown=e=>{if(e.ctrlKey&&e.key==='z')ctx.clearRect(0,0,400,300)};
    </script>''')
    return page, await c.observe(ident)


async def test_real_canvas_stroke_and_undo(target):
    c, old = target; ident=old['target_id']
    page, state = await prepare_canvas(c, ident)
    original=Path(state['screenshot']).read_bytes()
    drawn=await c.act(ident,state['observation_id'],'stroke',points=[{'x':20,'y':20},{'x':100,'y':100},{'x':200,'y':20}])
    assert await page.evaluate("ctx.getImageData(99,99,1,1).data[3]") > 0
    assert Path(drawn['screenshot']).read_bytes()!=original
    assert Path(state['screenshot']).read_bytes()==original
    undone=await c.act(ident,drawn['observation_id'],'key',key='Control+z')
    assert Path(undone['screenshot']).read_bytes()==original
    clicked=await c.act(ident,undone['observation_id'],'click',x=30,y=30)
    assert clicked['observation_id']!=undone['observation_id']


@pytest.mark.parametrize('points',[[],[{'x':1,'y':2}], [{'x':1,'y':2},{'x':1280,'y':2}], [{'x':True,'y':2},{'x':3,'y':4}], [{'x':1,'y':2}]*129])
async def test_bad_stroke_never_dispatches(target, points):
    c,state=target
    with pytest.raises(ComputerError,match='points|integer'):
        await c.act(state['target_id'],state['observation_id'],'stroke',points=points)
    assert c.targets[state['target_id']]['observation']==state['observation_id']


async def test_canvas_pixel_change_refuses_coordinate_action(target):
    c,old=target;ident=old['target_id']
    page,state=await prepare_canvas(c,ident)
    await page.evaluate('ctx.fillRect(1,1,20,20)')
    with pytest.raises(ComputerError,match='pixels changed'):
        await c.act(ident,state['observation_id'],'click',x=50,y=50)
    with pytest.raises(ComputerError,match='already used'):
        await c.act(ident,state['observation_id'],'click',x=50,y=50)


@pytest.mark.parametrize('cancel',[False,True])
async def test_browser_failed_stroke_releases_button(target,monkeypatch,cancel):
    c,old=target;ident=old['target_id']
    page,state=await prepare_canvas(c,ident)
    real_move=page.mouse.move; calls=0; entered=asyncio.Event()
    async def move(*a,**kw):
        nonlocal calls
        calls+=1
        if calls==2:
            entered.set()
            if cancel: await asyncio.Event().wait()
            raise RuntimeError('injected mid-stroke failure')
        await real_move(*a,**kw)
    monkeypatch.setattr(page.mouse,'move',move)
    task=asyncio.create_task(c.act(ident,state['observation_id'],'drag',points=[{'x':20,'y':20},{'x':100,'y':100}]))
    if cancel:
        await asyncio.wait_for(entered.wait(),5);task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else ComputerError):await task
    assert await page.evaluate('down') is False
    with pytest.raises(ComputerError,match='already used'):
        await c.act(ident,state['observation_id'],'click',x=30,y=30)


@pytest.mark.parametrize('failure',['none','focus','geometry','cancel','move'])
async def test_desktop_stroke_scopes_moves_and_releases_on_failure(tmp_path,monkeypatch,failure):
    import dream.computer as module
    c=Computer(tmp_path,tmp_path/'captures');calls=[]
    state={'window_id':'123','focus':'123','title':'Fixture','rect':[0,0,800,600],'view_only':False}
    pressed=False
    async def read(_):
        current=dict(state)
        if pressed and failure=='focus':current['focus']='456'
        if pressed and failure=='geometry':current['rect']=[1,1,800,600]
        return current
    async def command(*args):
        nonlocal pressed
        calls.append(args[1:])
        if args[1]=='mousedown':pressed=True
        if args[1]=='mouseup':pressed=False
        if args[1]=='mousemove' and pressed:
            if failure=='cancel':raise asyncio.CancelledError()
            if failure=='move':raise RuntimeError('injected native failure')
        return ''
    monkeypatch.setattr(c,'_desktop_state',read)
    monkeypatch.setattr(module,'_command',command)
    monkeypatch.setattr(module.shutil,'which',lambda _:'/usr/bin/xdotool')
    action=c._desktop_action({'window_id':'123'},state,'stroke',{'points':[{'x':10,'y':20},{'x':30,'y':40}]})
    if failure=='none':await action
    else:
        with pytest.raises(asyncio.CancelledError if failure=='cancel' else (ComputerError,RuntimeError)):await action
    assert calls[0]==('mousemove','--window','123','10','20')
    assert calls[-1]==('mouseup','1')
    assert not pressed
    if failure in {'focus','geometry'}:assert len(calls)==3


async def test_coordinate_click_rejects_mixed_target_and_bounds(target):
    c,state=target
    for args in ({'element_id':'1','x':20,'y':20},{'x':1280,'y':10},{'x':1}):
        with pytest.raises(ComputerError):
            await c.act(state['target_id'],state['observation_id'],'click',**args)
    assert c.targets[state['target_id']]['observation']==state['observation_id']


async def test_browser_stroke_stops_if_viewport_changes_after_press(target,monkeypatch):
    c,old=target;ident=old['target_id']
    page,state=await prepare_canvas(c,ident)
    real_down=page.mouse.down
    async def down(**kw):
        await real_down(**kw)
        await page.set_viewport_size({'width':600,'height':400})
    monkeypatch.setattr(page.mouse,'down',down)
    with pytest.raises(ComputerError,match='viewport or navigation changed'):
        await c.act(ident,state['observation_id'],'drag',points=[{'x':20,'y':20},{'x':100,'y':100}])
    assert await page.evaluate('down') is False
    assert await page.evaluate('ctx.getImageData(50,50,1,1).data[3]')==0


@pytest.mark.parametrize('duration',[None,500])
async def test_repeated_cancellation_waits_for_release_before_unlock(target,monkeypatch,duration):
    c,old=target;ident=old['target_id']
    page,state=await prepare_canvas(c,ident)
    real_move,real_up=page.mouse.move,page.mouse.up
    pressed=asyncio.Event();releasing=asyncio.Event();finish=asyncio.Event();calls=0
    async def move(*a,**kw):
        nonlocal calls
        calls+=1
        if calls==2:
            pressed.set();await asyncio.Event().wait()
        await real_move(*a,**kw)
    async def up(**kw):
        releasing.set();await finish.wait();await real_up(**kw)
    monkeypatch.setattr(page.mouse,'move',move)
    monkeypatch.setattr(page.mouse,'up',up)
    task=asyncio.create_task(c.act(ident,state['observation_id'],'drag',points=[{'x':10,'y':10},{'x':50,'y':50}],**({} if duration is None else {'duration_ms':duration})))
    await asyncio.wait_for(pressed.wait(),5);task.cancel()
    await asyncio.wait_for(releasing.wait(),5);task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and c._lock.locked()
    finish.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert await page.evaluate('down') is False
    assert not c._lock.locked()


@pytest.mark.parametrize('change',['overlay','replace','destination'])
async def test_coordinate_hover_change_refuses_unobserved_press(target,change):
    c,old=target;ident=old['target_id'];page=c.targets[ident]['page']
    await page.set_content('''<style>body{margin:0}a{display:block;width:400px;height:300px}</style>
    <a id="area" href="/original">Target</a><script>window.clicked=0;document.addEventListener('click',e=>{e.preventDefault();window.clicked++});</script>''')
    await page.mouse.move(600,600)
    await page.evaluate('''change=>{area.onmouseenter=()=>{
      if(change==='overlay')area.innerHTML='<button style="position:absolute;inset:0;width:400px;height:300px">Unexpected</button>';
      if(change==='replace')area.replaceWith(area.cloneNode(true));
      if(change==='destination')area.href='/unexpected';
    }}''',change)
    state=await c.observe(ident)
    with pytest.raises(ComputerError,match='changed|replaced'):
        await c.act(ident,state['observation_id'],'click',x=100,y=100)
    assert await page.evaluate('window.clicked')==0


async def test_coordinate_hover_status_text_is_allowed(target):
    c,old=target;ident=old['target_id'];page=c.targets[ident]['page']
    await page.set_content('''<style>body{margin:0}#area{width:400px;height:300px}</style>
    <div id="area"></div><p id="status">Ready</p><script>window.clicked=0;
    area.onmousemove=e=>document.querySelector('#status').textContent='Coordinates '+e.clientX+','+e.clientY;
    area.onclick=()=>window.clicked++;</script>''')
    await page.mouse.move(600,600)
    state=await c.observe(ident)
    await c.act(ident,state['observation_id'],'click',x=100,y=100)
    assert await page.evaluate('window.clicked')==1
    assert await page.locator('#status').inner_text()=='Coordinates 100,100'


@pytest.mark.asyncio
@pytest.mark.parametrize('key',['CTRL+z','Ctrl+z','ctrl+z','Control+z'])
async def test_common_aliases_real_key_events_and_release(target,key):
 c,state=target;ident=state['target_id'];page=c.targets[ident]['page']
 await page.evaluate('() => {window.events=[];document.onkeydown=e=>events.push([e.key,e.ctrlKey]);document.onclick=e=>events.push(["click",e.ctrlKey])}')
 await c.act(ident,state['observation_id'],'key',key=key)
 await page.locator('button').first.click()
 assert await page.evaluate('events')==[['Control',True],['z',True],['click',False]]


@pytest.mark.asyncio
@pytest.mark.parametrize('key',['Control+BOGUSKEY','Control+','CTRL+BOGUSKEY','a+b','Control+Clear','Control+Control+z','CTRL+Control+z',None])
async def test_bad_chord_preserves_token_and_sends_no_events(target,key):
 c,state=target;ident=state['target_id'];page=c.targets[ident]['page']
 await page.evaluate('() => {window.events=[];document.onkeydown=e=>events.push(e.key)}')
 with pytest.raises(ComputerError):await c.act(ident,state['observation_id'],'key',key=key)
 assert await page.evaluate('events')==[]
 assert c.targets[ident]['observation']==state['observation_id']


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel',[False,True])
async def test_chord_failure_cancellation_releases_held_control(target,monkeypatch,cancel):
 c,state=target;ident=state['target_id'];page=c.targets[ident]['page'];real=page.keyboard.down
 async def down(key):
  if key=='z':
   if cancel:raise asyncio.CancelledError()
   raise RuntimeError('injected after modifier')
  await real(key)
 monkeypatch.setattr(page.keyboard,'down',down)
 await page.evaluate('() => {window.ctrl=null;document.onclick=e=>window.ctrl=e.ctrlKey}')
 with pytest.raises(asyncio.CancelledError if cancel else ComputerError):await c.act(ident,state['observation_id'],'key',key='Control+z')
 await page.locator('button').first.click();assert await page.evaluate('ctrl') is False


@pytest.mark.asyncio
async def test_native_delayed_redraw_waits_for_new_pixels(tmp_path,monkeypatch):
 c=computer_module.Computer(tmp_path,tmp_path/'caps');started=time.monotonic();state={'window_id':'123','focus':'123','rect':[0,0,800,600],'title':'Synthetic'}
 async def read(_):return dict(state)
 async def capture(*_):return b'old pixels' if time.monotonic()-started<.35 else b'new pixels'
 monkeypatch.setattr(c,'_state',read);monkeypatch.setattr(c,'_capture',capture)
 got,pixels,quiet=await c._settled_capture({'kind':'desktop'})
 assert quiet and pixels==b'new pixels' and time.monotonic()-started>=.6


@pytest.mark.asyncio
async def test_never_stable_pixels_keep_fresh_unsettled_observation(tmp_path,monkeypatch):
 c=computer_module.Computer(tmp_path,tmp_path/'caps');target={'kind':'desktop','observation':'old'};count=0
 async def read(_):return {'window_id':'123'}
 async def capture(*_):
  nonlocal count
  count+=1;return str(count).encode()
 monkeypatch.setattr(c,'_state',read);monkeypatch.setattr(c,'_capture',capture)
 result=await c._observe('id',target)
 assert result['readiness']['status']=='unsettled'
 assert target['observation']!='old'
 assert Path(result['screenshot']).exists()


@pytest.mark.asyncio
async def test_cancelled_settle_invalidates_old_token(tmp_path,monkeypatch):
 c=computer_module.Computer(tmp_path,tmp_path/'caps');target={'kind':'desktop','observation':'old'};entered=asyncio.Event()
 async def read(_):entered.set();await asyncio.Event().wait()
 monkeypatch.setattr(c,'_state',read)
 task=asyncio.create_task(c._observe('id',target));await entered.wait();task.cancel()
 with pytest.raises(asyncio.CancelledError):await task
 assert 'observation' not in target


@pytest.mark.asyncio
async def test_every_named_key_in_inventory_is_accepted_by_actual_playwright(target):
 c,state=target;page=c.targets[state['target_id']]['page']
 await page.goto('about:blank')
 keys=computer_module._BROWSER_KEYS|computer_module._BROWSER_MODIFIERS|{'Key'+chr(n) for n in range(65,91)}|{'Digit'+str(n) for n in range(10)}|{'Numpad'+str(n) for n in range(10)}|{'F'+str(n) for n in range(1,13)}
 for key in sorted(keys):
  await page.keyboard.down(key)
  await page.keyboard.up(key)


@pytest.mark.asyncio
async def test_dispatch_and_cleanup_failures_both_reported_and_other_keys_released(target,monkeypatch):
 c,state=target;ident=state['target_id'];page=c.targets[ident]['page'];real_down,real_up=page.keyboard.down,page.keyboard.up
 async def down(key):
  if key=='z':raise RuntimeError('dispatch sentinel')
  await real_down(key)
 async def up(key):
  if key=='z':raise RuntimeError('cleanup sentinel')
  await real_up(key)
 monkeypatch.setattr(page.keyboard,'down',down);monkeypatch.setattr(page.keyboard,'up',up)
 with pytest.raises(ComputerError,match='dispatch sentinel; keyboard release also failed: cleanup sentinel'):
  await c.act(ident,state['observation_id'],'key',key='Control+z')
 await page.evaluate('() => {window.ctrl=null;document.onclick=e=>window.ctrl=e.ctrlKey}')
 await page.locator('button').first.click();assert await page.evaluate('ctrl') is False


@pytest.mark.asyncio
async def test_unmocked_native_io_is_fail_closed():
 with pytest.raises(AssertionError,match='Real native I/O is forbidden'):
  await computer_module._command('/usr/bin/xdotool','mousemove','--window','123','1','1')


@pytest.mark.asyncio
async def test_animated_canvas_keeps_read_only_evidence_without_claiming_quiet(target):
 c,old=target;ident=old['target_id'];page=c.targets[ident]['page']
 await page.set_content('<canvas width=400 height=300></canvas><script>let n=0;window.timer=setInterval(()=>{const c=document.querySelector("canvas").getContext("2d");c.fillStyle=`rgb(${n++%255},0,0)`;c.fillRect(0,0,400,300)},30)</script>')
 state=await c.observe(ident)
 assert state['readiness']['status']=='unsettled'
 assert Path(state['screenshot']).read_bytes().startswith(b'\x89PNG')
 await page.evaluate('clearInterval(timer);const c=document.querySelector("canvas").getContext("2d");c.fillStyle="blue";c.fillRect(0,0,400,300)')
 with pytest.raises(ComputerError,match='pixels changed'):
  await c.act(ident,state['observation_id'],'click',x=50,y=50)


@pytest.mark.asyncio
async def test_no_animation_frames_still_returns_fresh_coherent_evidence(target,monkeypatch):
 c,old=target;ident=old['target_id'];page=c.targets[ident]['page'];original=page.evaluate
 async def evaluate(expression,*a,**kw):
  if 'new Promise(resolve => requestAnimationFrame' in expression:
   await asyncio.Event().wait()
  return await original(expression,*a,**kw)
 monkeypatch.setattr(page,'evaluate',evaluate)
 state=await c.observe(ident)
 assert state['readiness']['status']=='unsettled'
 assert state['observation_id']!=old['observation_id']
 assert state['state']['elements']
 assert Path(state['screenshot']).read_bytes().startswith(b'\x89PNG')


def test_paced_plan_retains_corners_and_bounds():
    import math
    points=[(10,10),(110,10),(110,90)]
    plan=Computer._paced_plan(points,1000)
    assert plan[0]==(0,points[0]) and plan[-1]==(1,points[-1])
    assert points[1] in [p for _,p in plan]
    assert len(plan)<=256
    for (ta,a),(tb,b) in zip(plan,plan[1:]):
        assert 0<=tb-ta<=.02000001
        assert math.dist(a,b)<=4
    hold=Computer._paced_plan([(10,10)]*2,1000)
    assert hold[-1]==(1,(10,10)) and len(hold)>=51
    with pytest.raises(ComputerError,match='subdivisions'):
        Computer._paced_plan([(0,0),(1279,799)],2000)


@pytest.mark.parametrize('duration',[None,True,0,-1,2001,'100',1.5])
async def test_invalid_duration_preserves_observation_and_no_input(target,monkeypatch,duration):
    c,state=target;ident=state['target_id'];page=c.targets[ident]['page']
    async def forbidden(*a,**kw):raise AssertionError('input dispatched')
    monkeypatch.setattr(page.mouse,'move',forbidden)
    with pytest.raises(ComputerError,match='duration_ms'):
        await c.act(ident,state['observation_id'],'stroke',points=[{'x':10,'y':10},{'x':20,'y':20}],duration_ms=duration)
    assert c.targets[ident]['observation']==state['observation_id']


async def test_paced_excessive_plan_and_wrong_action_preserve_token(target,monkeypatch):
    c,state=target;ident=state['target_id'];page=c.targets[ident]['page']
    async def forbidden(*a,**kw):raise AssertionError('input dispatched')
    monkeypatch.setattr(page.mouse,'move',forbidden)
    for action,args in [('drag',{'points':[{'x':0,'y':0},{'x':1279,'y':799}]}),('click',{'x':10,'y':10})]:
        with pytest.raises(ComputerError,match='subdivisions|unused parameters'):
            await c.act(ident,state['observation_id'],action,duration_ms=1000,**args)
        assert c.targets[ident]['observation']==state['observation_id']


async def test_real_paced_drag_interpolates_and_reports_hold(target):
    c,old=target;ident=old['target_id'];page,state=await prepare_canvas(c,ident)
    await page.evaluate('()=>{window.moves=[];document.addEventListener("mousemove",e=>{if(e.buttons) moves.push([e.clientX,e.clientY])})}')
    result=await c.act(ident,state['observation_id'],'drag',points=[{'x':20,'y':20},{'x':220,'y':20}],duration_ms=400)
    moves=await page.evaluate('moves')
    assert any(80<x<160 and y==20 for x,y in moves)
    assert result['action_result']['requested_duration_ms']==400
    assert result['action_result']['actual_hold_ms']>=390
    assert await page.evaluate('down') is False


@pytest.mark.parametrize('failure',['cancel','viewport'])
async def test_paced_wait_failure_releases_before_next_move(target,monkeypatch,failure):
    c,old=target;ident=old['target_id'];page,state=await prepare_canvas(c,ident)
    original=page.mouse.down;pressed=asyncio.Event()
    async def down(**kw):await original(**kw);pressed.set()
    monkeypatch.setattr(page.mouse,'down',down)
    task=asyncio.create_task(c.act(ident,state['observation_id'],'stroke',points=[{'x':20,'y':20}]*2,duration_ms=1000))
    await pressed.wait();await asyncio.sleep(.05)
    if failure=='cancel':task.cancel()
    else:await page.set_viewport_size({'width':600,'height':400})
    with pytest.raises(asyncio.CancelledError if failure=='cancel' else ComputerError):await task
    assert await page.evaluate('down') is False
    assert not c._lock.locked()


@pytest.mark.parametrize('failure',['none','focus','cancel','timeout'])
async def test_native_paced_hold_checks_and_releases(tmp_path,monkeypatch,failure):
    c=Computer(tmp_path,tmp_path/'caps');calls=[];pressed=False;checks=0
    state={'window_id':'123','focus':'123','title':'Mock','rect':[0,0,800,600],'view_only':False}
    async def read(_):
        nonlocal checks
        checks+=1
        if pressed and checks>4:
            if failure=='focus':return {**state,'focus':'456'}
            if failure=='cancel':raise asyncio.CancelledError()
            if failure=='timeout':await asyncio.Event().wait()
        return dict(state)
    async def command(*args):
        nonlocal pressed
        calls.append(args[1:])
        assert args[1] in {'mousemove','mousedown','mouseup'}
        if args[1]=='mousedown':pressed=True
        if args[1]=='mouseup':pressed=False
        return ''
    monkeypatch.setattr(c,'_desktop_state',read)
    monkeypatch.setattr(computer_module,'_command',command)
    monkeypatch.setattr(computer_module.shutil,'which',lambda _:'/fake/xdotool')
    task=c._desktop_action({'window_id':'123'},state,'stroke',{'points':[{'x':10,'y':20}]*2,'duration_ms':100})
    if failure=='none':
        result=await task;assert result['actual_hold_ms']>=90
    else:
        expected=asyncio.CancelledError if failure=='cancel' else (TimeoutError if failure=='timeout' else ComputerError)
        with pytest.raises(expected):await asyncio.wait_for(task,.3)
    assert calls[-1]==('mouseup','1') and not pressed
    assert all(call[1:3]==('--window','123') for call in calls if call[0]=='mousemove')


@pytest.mark.parametrize('origin', [(1, 22), (-120, -40)])
async def test_native_client_geometry_uses_absolute_origin(tmp_path, monkeypatch, origin):
    c = Computer(tmp_path, tmp_path/'caps')
    calls = []
    async def command(*args, **kwargs):
        calls.append((args, kwargs))
        if args[0].endswith('xwininfo'):
            assert kwargs['env']['LC_ALL'] == 'C'
            return f'  Absolute upper-left X: {origin[0]}\n  Absolute upper-left Y: {origin[1]}\n  Relative upper-left X: 1\n  Relative upper-left Y: 22\n  Width: 640\n  Height: 220\n'
        if args[0].endswith('xprop'):
            return '_NET_WM_PID(CARDINAL) = 4242\nWM_CLASS(STRING) = "fixture", "Fixture"'
        return {'getwindowname': 'Fixture', 'getwindowfocus': '123',
                'getwindowgeometry': 'X=2\nY=44\nWIDTH=640\nHEIGHT=220'}[args[1]]
    monkeypatch.setattr(computer_module.shutil, 'which', lambda name: '/fake/'+name)
    monkeypatch.setattr(computer_module, '_command', command)
    state = await c._desktop_state({'window_id': '123'})
    assert state['rect'] == [*origin, 640, 220]
    assert not any('getwindowgeometry' in args for args, _ in calls)


@pytest.mark.parametrize('geometry', ['', 'Width: 640\nHeight: 220',
    'Absolute upper-left X: 1\nAbsolute upper-left Y: 22\nWidth: 0\nHeight: 220',
    'Absolute upper-left X: 1\nAbsolute upper-left Y: 22\nWidth: 640\nWidth: 12\nHeight: 220'])
async def test_native_malformed_geometry_refuses_attach(tmp_path, monkeypatch, geometry):
    c = Computer(tmp_path, tmp_path/'caps')
    monkeypatch.setenv('DISPLAY', ':96')
    monkeypatch.setenv('XDG_SESSION_TYPE', 'x11')
    monkeypatch.setattr(computer_module.shutil, 'which', lambda name: '/fake/'+name)
    async def command(*args, **kwargs):
        if args[0].endswith('xwininfo'): return geometry
        if args[1] == 'getwindowgeometry': return 'X=2\nY=44\nWIDTH=640\nHEIGHT=220'
        return '123'
    monkeypatch.setattr(computer_module, '_command', command)
    with pytest.raises(ComputerError, match='geometry|dimensions'):
        await c.open('desktop', window_id='123')
    assert not c.targets


def test_native_missing_xwininfo_reports_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv('DISPLAY', ':96')
    monkeypatch.setenv('XDG_SESSION_TYPE', 'x11')
    monkeypatch.setattr(computer_module.shutil, 'which', lambda name: None if name == 'xwininfo' else '/fake/'+name)
    assert Computer(tmp_path, tmp_path/'caps').capabilities()['desktop'] is False


async def test_native_full_text_chunks_preserve_literal_characters_and_check_target(tmp_path, monkeypatch):
    c = Computer(tmp_path, tmp_path/'caps')
    state = {'window_id': '123', 'focus': '123', 'title': 'Fixture', 'rect': [1,22,640,220], 'view_only': False}
    text = ('22 Aa+;&\n-é' * 400)[:4000]
    typed = []; checks = []
    async def read(_):
        checks.append(len(''.join(typed)))
        return dict(state)
    async def command(*args):
        if args[1] == 'getwindowfocus': return '123'
        assert args[1:6] == ('type', '--clearmodifiers', '--delay', '12', '--')
        assert len(args[6]) <= 128
        typed.append(args[6]); return ''
    monkeypatch.setattr(c, '_desktop_state', read)
    monkeypatch.setattr(computer_module, '_command', command)
    monkeypatch.setattr(computer_module.shutil, 'which', lambda _: '/fake/xdotool')
    await c._desktop_action({'window_id':'123'}, state, 'type', {'text':text})
    assert ''.join(typed) == text
    assert checks[0] == 0 and checks[-1] == 4000
    assert len(checks) >= len(typed)+1


@pytest.mark.parametrize('changed', ['focus', 'title', 'rect', 'pid', 'wm_class'])
async def test_native_type_target_change_stops_remaining_chunks(tmp_path, monkeypatch, changed):
    c = Computer(tmp_path, tmp_path/'caps'); typed=[]
    state = {'window_id':'123','focus':'123','title':'Fixture','rect':[1,22,640,220],'pid':4242,'wm_class':['fixture','Fixture'],'view_only':False}
    async def read(_):
        return {**state, changed: {'focus':'456','title':'Other','rect':[5,22,640,220],'pid':4243,'wm_class':['other','Other']}[changed]} if typed else dict(state)
    async def command(*args):
        if args[1] == 'getwindowfocus': return '123'
        typed.append(args[-1]); return ''
    monkeypatch.setattr(c, '_desktop_state', read)
    monkeypatch.setattr(computer_module, '_command', command)
    monkeypatch.setattr(computer_module.shutil, 'which', lambda _: '/fake/xdotool')
    with pytest.raises(ComputerError, match='changed.*typ'):
        await c._desktop_action({'window_id':'123'}, state, 'type', {'text':'2'*300})
    assert len(''.join(typed)) == 128


async def test_native_type_cancel_finishes_current_chunk_restoration_only(tmp_path, monkeypatch):
    c = Computer(tmp_path, tmp_path/'caps'); entered=asyncio.Event(); finish=asyncio.Event(); calls=[]; restored=False
    state={'window_id':'123','focus':'123','title':'Fixture','rect':[1,22,640,220],'view_only':False}
    async def read(_): return dict(state)
    async def command(*args):
        nonlocal restored
        if args[1]=='getwindowfocus': return '123'
        calls.append(args[-1]); entered.set()
        await finish.wait(); restored=True; return ''
    monkeypatch.setattr(c, '_desktop_state', read)
    monkeypatch.setattr(computer_module, '_command', command)
    monkeypatch.setattr(computer_module.shutil, 'which', lambda _: '/fake/xdotool')
    task=asyncio.create_task(c._desktop_action({'window_id':'123'},state,'type',{'text':'A'*300}))
    await entered.wait(); task.cancel(); await asyncio.sleep(0)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert restored and len(calls)==1 and len(calls[0])==128


async def test_native_long_type_deadline_allows_full_input_and_consumes_token(tmp_path, monkeypatch):
    c=Computer(tmp_path,tmp_path/'caps'); state={'window_id':'123','focus':'123','title':'Fixture','rect':[0,0,640,220],'view_only':False}
    owned={'kind':'desktop','window_id':'123','observation':'o','observed':time.monotonic(),'fingerprint':computer_module._fingerprint(state)}
    c.targets['id']=owned; dispatched=[]; deadlines=[]
    async def read(_):return dict(state)
    async def action(*args):dispatched.append(args[-1]['text'])
    async def observe(*_):return {'observation_id':'next'}
    async def wait_for(coro, timeout):
        deadlines.append(timeout)
        if timeout < 48:
            coro.close();raise TimeoutError('4000 conservative native keystroke intervals need 48s')
        return await coro
    monkeypatch.setattr(c,'_state',read);monkeypatch.setattr(c,'_desktop_action',action);monkeypatch.setattr(c,'_observe',observe)
    monkeypatch.setattr(computer_module.asyncio,'wait_for',wait_for)
    result=await c.act('id','o','type',text='2'*4000)
    assert dispatched==['2'*4000] and 48<=deadlines[0]<=120
    assert result['action_result']['task_success']=='unverified' and 'observation' not in owned


async def test_native_typing_command_failure_reports_partial_input_without_retry(tmp_path, monkeypatch):
    c=Computer(tmp_path,tmp_path/'caps');state={'window_id':'123','focus':'123','title':'Fixture','rect':[0,0,640,220],'view_only':False};calls=[]
    async def read(_):return dict(state)
    async def command(*args):
        calls.append(args)
        raise TimeoutError('fixture subprocess expired')
    monkeypatch.setattr(c,'_desktop_state',read);monkeypatch.setattr(computer_module,'_command',command)
    monkeypatch.setattr(computer_module.shutil,'which',lambda _: '/fake/xdotool')
    with pytest.raises(ComputerError,match='partial text or held keys/modifiers.*cleanup could not be confirmed'):
        await c._desktop_action({'window_id':'123'},state,'type',{'text':'A'*300})
    assert len(calls)==1 and len(calls[0][-1])==128


async def test_native_capture_uses_true_client_bounds(tmp_path, monkeypatch):
    from PIL import Image
    c=Computer(tmp_path,tmp_path/'caps');calls=[]
    monkeypatch.setenv('DISPLAY',':96')
    def capture(*,bbox,xdisplay):
        calls.append((bbox,xdisplay));return Image.new('RGB',(640,220),'white')
    monkeypatch.setattr(computer_module.ImageGrab,'grab',capture)
    result=await c._capture({'kind':'desktop'}, {'window_id':'123','focus':'123','rect':[1,22,640,220]})
    assert calls==[((1,22,641,242),':96')] and result.startswith(b'\x89PNG')


async def test_native_missing_geometry_binary_blocks_state_read(tmp_path, monkeypatch):
    monkeypatch.setattr(computer_module.shutil,'which',lambda name: None if name=='xwininfo' else '/fake/'+name)
    with pytest.raises(ComputerError,match='xwininfo.*unavailable'):
        await Computer(tmp_path,tmp_path/'caps')._desktop_state({'window_id':'123'})


# --- DREAM-187: Dream's own window is never a target; terminals are view-only ------------------

NEVER_A_PID = '4194305'   # above pid_max: the view-only check reads /proc/<pid>/fd, so a live pid would be a real process


def _x11_client(monkeypatch, c, *, pid=NEVER_A_PID, wm_class='"fixture", "Fixture"', title='Fixture'):
    """A fake X11 client behind the REAL _desktop_state: geometry, name, focus and the ownership
    properties. Records every native call after the binary; input commands answer ''. Returns the
    calls and the client's mutable properties (a title change is a tab switch or a window-ID reuse)."""
    calls, client = [], dict(pid=pid, wm_class=wm_class, title=title)
    async def command(*args, **kwargs):
        calls.append(args[1:])
        if args[0].endswith('xwininfo'):
            return 'Absolute upper-left X: 0\nAbsolute upper-left Y: 0\nWidth: 800\nHeight: 600'
        if args[0].endswith('xprop'):
            return ((f"_NET_WM_PID(CARDINAL) = {client['pid']}\n" if client['pid'] else '_NET_WM_PID:  not found.\n')
                    + f"WM_CLASS(STRING) = {client['wm_class']}")
        return {'getwindowname': client['title'], 'getwindowfocus': '123'}.get(args[1], '')
    async def capture(*_):
        return b'pixels'
    monkeypatch.setenv('DISPLAY', ':96')
    monkeypatch.setenv('XDG_SESSION_TYPE', 'x11')
    monkeypatch.setattr(computer_module.shutil, 'which', lambda name: '/fake/'+name)
    monkeypatch.setattr(computer_module, '_command', command)
    monkeypatch.setattr(c, '_capture', capture)
    return calls, client


@pytest.mark.parametrize('signal', ['own_pid', 'parent_pid', 'wm_class', 'title'])
async def test_desktop_refuses_dreams_own_window(tmp_path, monkeypatch, signal):
    c = Computer(tmp_path, tmp_path/'caps')
    own = {'own_pid': dict(pid=str(os.getpid())), 'parent_pid': dict(pid=str(os.getppid())),
           'wm_class': dict(wm_class='"dream-desktop", "Dream"'), 'title': dict(title='Dream — Workspace')}[signal]
    calls, _ = _x11_client(monkeypatch, c, **own)
    with pytest.raises(ComputerError, match="Dream's own window"):
        await c.open('desktop', window_id='123')
    assert not c.targets
    assert {call[0] for call in calls} <= {'-id', 'getwindowname', 'getwindowfocus'}   # reads only, no input


@pytest.mark.parametrize('title', ['Dream Studio — Mozilla Firefox', 'Dream Studio - Google Chrome', 'Dream Studio - Brave',
                                   'Dream Studio'])
async def test_desktop_refuses_the_studio_tab_in_the_owners_browser(tmp_path, monkeypatch, title):
    """`dream --gui` opens Studio, approval cards included, as a tab in the owner's browser: not Dream's PID
    tree, not its WM_CLASS. The page's fixed <title> is the signal, wherever it is shown."""
    c = Computer(tmp_path, tmp_path/'caps')
    calls, _ = _x11_client(monkeypatch, c, wm_class='"Navigator", "firefox"', title=title)
    with pytest.raises(ComputerError, match="Dream's own window"):
        await c.open('desktop', window_id='123')
    assert not c.targets and 'windowactivate' not in {call[0] for call in calls}


@pytest.mark.parametrize('change', [dict(title='Dream Studio — Mozilla Firefox'), dict(wm_class='"dream-desktop", "Dream"')])
async def test_desktop_act_refuses_a_target_that_became_dreams_own_window(tmp_path, monkeypatch, change):
    """The window behind a target can change after attach (the browser's active tab, an X11 window-ID reuse),
    so the own-window check runs on every action, like the view-only check, not only in open()."""
    c = Computer(tmp_path, tmp_path/'caps')
    calls, client = _x11_client(monkeypatch, c, wm_class='"Navigator", "firefox"', title='Wikipedia — Mozilla Firefox')
    observed = await c.open('desktop', window_id='123')
    client.update(change)
    with pytest.raises(ComputerError, match='state changed'):        # the stale check fires first...
        await c.act(observed['target_id'], observed['observation_id'], 'click', x=1, y=1)
    observed = await c.observe(observed['target_id'])                 # ...a fresh observation may read it...
    for action, args in (('click', {'x': 1, 'y': 1}), ('type', {'text': 'y'}), ('key', {'key': 'Return'}), ('focus', {})):
        with pytest.raises(ComputerError, match="Dream's own window"):   # ...and no action drives it, focus included
            await c.act(observed['target_id'], observed['observation_id'], action, **args)
    assert not {'click', 'type', 'key', 'mousedown', 'mousemove', 'windowactivate'} & {call[0] for call in calls}


@pytest.fixture
def plain_process():
    """A real process of ours that holds no pseudo-terminal and has nothing below it: an ordinary
    application's window, its ownership readable from /proc."""
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        yield child.pid
    finally:
        child.terminate()
        child.wait()


async def test_desktop_ordinary_window_attaches_with_owner_in_state(tmp_path, monkeypatch, plain_process):
    c = Computer(tmp_path, tmp_path/'caps')
    _x11_client(monkeypatch, c, pid=str(plain_process))
    observed = await c.open('desktop', window_id='123')
    assert observed['state']['pid'] == plain_process and observed['state']['wm_class'] == ['fixture', 'Fixture']
    assert observed['state']['view_only'] is False


# --- DREAM-187 (2 of 4): a pty held by a helper, or ownership that cannot be read, is view-only -----------

async def test_a_terminal_whose_helper_process_holds_the_pty_is_view_only(tmp_path, monkeypatch):
    """The window's process holds no pty itself: a process it spawned does (an emulator that leaves the pty
    to a helper). Read from the real /proc of a real process tree of ours."""
    parent = subprocess.Popen([sys.executable, '-c', '''
import subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import os,time; os.openpty(); print('ready', flush=True); time.sleep(60)"],
                         stdout=subprocess.PIPE, text=True)
assert child.stdout.readline().strip() == "ready"
print("ready", flush=True)
child.wait()'''], stdout=subprocess.PIPE, text=True)
    try:
        assert parent.stdout.readline().strip() == 'ready'
        assert computer_module._pty_ownership(parent.pid) == 'held'
        c = Computer(tmp_path, tmp_path/'caps')
        calls, _ = _x11_client(monkeypatch, c, pid=str(parent.pid), wm_class='"newterm", "Newterm"', title='owner@host: ~')
        observed = await c.open('desktop', window_id='123')
        assert observed['state']['view_only'] is True and 'Terminal window' in observed['state']['note']
        with pytest.raises(ComputerError, match='view-only'):
            await c.act(observed['target_id'], observed['observation_id'], 'type', text='rm -rf ~')
        assert 'type' not in {call[0] for call in calls}
    finally:
        parent.terminate()
        parent.wait()


@pytest.mark.parametrize('how', ['no_pid', 'process_gone', 'fd_table_unreadable'])
async def test_a_window_whose_ownership_cannot_be_read_is_view_only(tmp_path, monkeypatch, plain_process, how):
    """Ownership unavailable (no _NET_WM_PID), a process that is gone, or a file table closed to us: not
    shown writable -- a terminal that cannot be identified is one."""
    c = Computer(tmp_path, tmp_path/'caps')
    pid = {'no_pid': '', 'process_gone': NEVER_A_PID, 'fd_table_unreadable': str(plain_process)}[how]
    if how == 'fd_table_unreadable':
        real_scandir = os.scandir
        def scandir(path='.'):
            if str(path) == f'/proc/{plain_process}/fd':
                raise PermissionError(13, 'Permission denied', str(path))
            return real_scandir(path)
        monkeypatch.setattr(computer_module.os, 'scandir', scandir)
        assert computer_module._pty_ownership(plain_process) == 'unknown'
    calls, _ = _x11_client(monkeypatch, c, pid=pid, wm_class='"someapp", "Someapp"', title='Untitled')
    observed = await c.open('desktop', window_id='123')
    assert observed['state']['view_only'] is True and 'unknown ownership' in observed['state']['note']
    for action, args in (('click', {'x': 1, 'y': 1}), ('type', {'text': 'ls'}), ('key', {'key': 'Return'})):
        with pytest.raises(ComputerError, match='view-only'):
            await c.act(observed['target_id'], observed['observation_id'], action, **args)
    assert not {'click', 'type', 'key', 'mousedown', 'mousemove'} & {call[0] for call in calls}


def test_pty_ownership_of_a_plain_process_is_free(plain_process):
    assert computer_module._pty_ownership(plain_process) == 'free'
    assert computer_module._pty_ownership(int(NEVER_A_PID)) == 'unknown'
    assert computer_module._view_only({'wm_class': ['someapp', 'Someapp'], 'pid': None}).startswith('Window of unknown ownership')


# --- Codex round 1, gap 1: an incomplete pty inspection is unknown, never free -----------------------------

_HOLDER = "import os,time; os.openpty(); print('ready', flush=True); time.sleep(120)"
_MIDDLE = ("import subprocess, sys\n"
           f"holder = subprocess.Popen([sys.executable, '-c', {_HOLDER!r}], stdout=subprocess.PIPE, text=True)\n"
           "assert holder.stdout.readline().strip() == 'ready'\n"
           "print(holder.pid, flush=True)\n"
           "holder.wait()\n")
_ROOT = ("import subprocess, sys\n"
         f"middle = subprocess.Popen([sys.executable, '-c', {_MIDDLE!r}], stdout=subprocess.PIPE, text=True)\n"
         "print(middle.pid, middle.stdout.readline().strip(), flush=True)\n"
         "middle.wait()\n")


@pytest.fixture
def pty_tree():
    """A real process tree of ours in its own session: root -> middle -> the holder of a pseudo-terminal
    master (an emulator whose GUI process leaves the pty to a helper's helper). Yields the root Popen and
    the middle and holder pids; the whole session is killed afterwards."""
    root = subprocess.Popen([sys.executable, '-c', _ROOT], stdout=subprocess.PIPE, text=True, start_new_session=True)
    middle, holder = map(int, root.stdout.readline().split())
    try:
        yield root, middle, holder
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(root.pid, signal.SIGKILL)
        root.wait()


def test_pty_ownership_sees_a_holder_two_levels_down(pty_tree):
    root, _, _ = pty_tree
    assert computer_module._pty_ownership(root.pid) == 'held'


@pytest.mark.parametrize('refused', ['stat', 'fd_links'])
def test_pty_ownership_is_unknown_when_a_descendants_inspection_is_refused(pty_tree, monkeypatch, refused):
    """The holder is there, but its stat (the walk's parent link) or its descriptor links are refused to us:
    the inspection is incomplete, and the window is of unknown ownership, not writable."""
    root, _, holder = pty_tree
    if refused == 'stat':
        real = Path.read_text
        def read_text(self, *a, **kw):
            if str(self) == f'/proc/{holder}/stat':
                raise PermissionError(13, 'Permission denied', str(self))
            return real(self, *a, **kw)
        monkeypatch.setattr(Path, 'read_text', read_text)
    else:
        real = os.readlink
        def readlink(path, *a, **kw):
            if str(path).startswith(f'/proc/{holder}/fd/'):
                raise PermissionError(13, 'Permission denied', str(path))
            return real(path, *a, **kw)
        monkeypatch.setattr(computer_module.os, 'readlink', readlink)
    assert computer_module._pty_ownership(root.pid) == 'unknown'
    assert computer_module._view_only({'wm_class': ['newterm', 'Newterm'], 'pid': root.pid}).startswith('Window of unknown ownership')


def test_pty_ownership_is_unknown_when_the_tree_is_larger_than_the_walk(pty_tree, monkeypatch):
    root, _, _ = pty_tree
    monkeypatch.setattr(computer_module, '_DESCENDANT_CAP', 1, raising=False)   # the holder sits beyond the walk
    assert computer_module._pty_ownership(root.pid) == 'unknown'


def test_pty_ownership_is_unknown_when_the_tree_exits_after_the_roots_descriptor_check(pty_tree, monkeypatch):
    """The root's own descriptors were checked; then the whole tree exits before its descendants are: the
    process the answer would be about is gone, so the answer is unknown."""
    root, middle, holder = pty_tree
    real = computer_module._descendants
    def descendants_then_gone(pid):
        found = real(pid)
        os.killpg(root.pid, signal.SIGKILL)
        root.wait()
        for _ in range(100):
            if not os.path.exists(f'/proc/{middle}') and not os.path.exists(f'/proc/{holder}'):
                break
            time.sleep(.05)
        return found
    monkeypatch.setattr(computer_module, '_descendants', descendants_then_gone)
    assert computer_module._pty_ownership(root.pid) == 'unknown'


# --- DREAM-187 (3 of 4): every security predicate re-applied immediately before native input goes out ----

@pytest.fixture
def other_process():
    """A second plain process of ours: another application behind a reused window ID."""
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        yield child.pid
    finally:
        child.terminate()
        child.wait()


@pytest.mark.parametrize('action,args', [('click', {'x': 1, 'y': 1}), ('type', {'text': 'ls -la'}), ('key', {'key': 'Return'}),
                                         ('scroll', {'dy': 100}), ('drag', {'points': [{'x': 1, 'y': 1}, {'x': 2, 'y': 2}]}), ('focus', {})])
@pytest.mark.parametrize('change', ['own_class', 'own_pid', 'terminal_class', 'other_class', 'other_pid'])
async def test_native_dispatch_refuses_a_window_that_changed_after_authorization(tmp_path, monkeypatch, plain_process, other_process,
                                                                                   action, args, change):
    """Between act()'s authorization (fresh observation, state unchanged, not Dream's, not view-only) and
    the native dispatch, the window behind the ID becomes Dream's own, a terminal, or another window --
    keeping its title, focus and geometry. The state is read again immediately before input goes out and
    every predicate re-applied: refused, nothing dispatched (focusing a terminal, to read it, is allowed)."""
    c = Computer(tmp_path, tmp_path/'caps')
    calls, client = _x11_client(monkeypatch, c, pid=str(plain_process), wm_class='"Navigator", "firefox"', title='Wikipedia — Mozilla Firefox')
    observed = await c.open('desktop', window_id='123')
    original = c._desktop_action
    async def changed_then(*a, **kw):
        client.update({'own_class': dict(wm_class='"dream-desktop", "Dream"'), 'own_pid': dict(pid=str(os.getpid())),
                       'terminal_class': dict(wm_class='"gnome-terminal-server", "Gnome-terminal"'),
                       'other_class': dict(wm_class='"otherapp", "Otherapp"'), 'other_pid': dict(pid=str(other_process))}[change])
        return await original(*a, **kw)
    monkeypatch.setattr(c, '_desktop_action', changed_then)
    expected = {'own_class': "Dream's own window", 'own_pid': "Dream's own window", 'terminal_class': 'view-only',
                'other_class': 'wm_class changed', 'other_pid': 'pid changed'}[change]
    if action == 'focus' and change == 'terminal_class':
        expected = 'wm_class changed'   # focusing a window observed as a terminal is allowed; one that changed into one is another window
    with pytest.raises(ComputerError, match=expected) as refusal:
        await c.act(observed['target_id'], observed['observation_id'], action, **args)
    assert not {'click', 'type', 'key', 'mousedown', 'mousemove', 'windowactivate'} & {call[0] for call in calls}
    assert 'No input dispatched' in str(refusal.value)   # and it is true: not even the pointer moved


@pytest.mark.parametrize('action,args', [('click', {'x': 1, 'y': 2}), ('scroll', {'dy': 100})])
@pytest.mark.parametrize('failure', [ComputerError('state unreadable'), TimeoutError('state read timed out')])
async def test_nothing_moves_when_the_state_cannot_be_read_before_dispatch(tmp_path, monkeypatch, plain_process, action, args, failure):
    """The pointer is not moved into a window whose state cannot be re-read (an unreadable or timed-out
    read) immediately before dispatch: the re-check comes before the move, so nothing goes out."""
    c = Computer(tmp_path, tmp_path/'caps')
    calls, _ = _x11_client(monkeypatch, c, pid=str(plain_process))
    observed = await c.open('desktop', window_id='123')
    original = c._desktop_action
    async def failing_read(_):
        raise failure
    async def then_unreadable(*a, **kw):
        monkeypatch.setattr(c, '_desktop_state', failing_read)
        return await original(*a, **kw)
    monkeypatch.setattr(c, '_desktop_action', then_unreadable)
    with pytest.raises(ComputerError, match='outcome is uncertain'):
        await c.act(observed['target_id'], observed['observation_id'], action, **args)
    assert not {'mousemove', 'click'} & {call[0] for call in calls}


@pytest.mark.parametrize('action,args', [('click', {'x': 1, 'y': 2}), ('scroll', {'dy': 100})])
async def test_a_window_that_changes_as_the_pointer_moves_is_refused_and_the_refusal_says_so(tmp_path, monkeypatch, plain_process, action, args):
    """The pre-move check passes; the window becomes Dream's own as the pointer moves: the post-move check
    refuses the click or scroll, and the refusal says the pointer was moved, not that nothing was."""
    c = Computer(tmp_path, tmp_path/'caps')
    calls, client = _x11_client(monkeypatch, c, pid=str(plain_process), wm_class='"Navigator", "firefox"', title='Wikipedia — Mozilla Firefox')
    observed = await c.open('desktop', window_id='123')
    fake = computer_module._command
    async def command(*args_, **kwargs):
        result = await fake(*args_, **kwargs)
        if args_[1] == 'mousemove':
            client['wm_class'] = '"dream-desktop", "Dream"'
        return result
    monkeypatch.setattr(computer_module, '_command', command)
    with pytest.raises(ComputerError, match="Dream's own window.*The pointer was moved; nothing further dispatched") as refusal:
        await c.act(observed['target_id'], observed['observation_id'], action, **args)
    assert 'No input dispatched' not in str(refusal.value)
    assert [call[0] for call in calls if call[0] in ('mousemove', 'click')] == ['mousemove']


@pytest.mark.parametrize('action', ['stroke', 'type'])
async def test_native_input_stops_when_the_window_becomes_a_terminal_midway(tmp_path, monkeypatch, plain_process, action):
    """The window turns into a terminal (same title, focus and geometry) as the first input goes out: the
    stroke's next check releases the button with no further move; typing stops after the current chunk."""
    c = Computer(tmp_path, tmp_path/'caps')
    calls, client = _x11_client(monkeypatch, c, pid=str(plain_process))
    observed = await c.open('desktop', window_id='123')
    fake = computer_module._command
    async def command(*args, **kwargs):
        result = await fake(*args, **kwargs)
        if args[1] in ('mousedown', 'type'):
            client['wm_class'] = '"gnome-terminal-server", "Gnome-terminal"'
        return result
    monkeypatch.setattr(computer_module, '_command', command)
    args = {'points': [{'x': 1, 'y': 1}, {'x': 5, 'y': 5}, {'x': 9, 'y': 9}]} if action == 'stroke' else {'text': 'A' * 300}
    with pytest.raises(ComputerError, match='view-only'):
        await c.act(observed['target_id'], observed['observation_id'], action, **args)
    inputs = [call for call in calls if call[0] in ('mousemove', 'mousedown', 'mouseup', 'type')]
    if action == 'stroke':
        assert [call[0] for call in inputs] == ['mousemove', 'mousedown', 'mouseup']
    else:
        assert [call[0] for call in inputs] == ['type'] and len(inputs[0][-1]) == 128


# --- DREAM-187 (4 of 4): a target's identity, for the 'always' approval (tui/app.py) ---------------------

async def test_a_browser_targets_identity_is_its_page_origin(target):
    c, state = target
    ident = state['target_id']
    port = int(state['state']['url'].rsplit(':', 1)[1].split('/')[0])
    assert c.identity(ident) == f'browser\x00http://{FIXTURE_HOST}:{port}'
    await c.targets[ident]['page'].goto(f'http://other.example:{port}/', wait_until='domcontentloaded')
    await c.observe(ident)
    assert c.identity(ident) == f'browser\x00http://other.example:{port}'   # another origin: another identity
    assert c.identity('nope') is None


def _browser_identity(url, navigation=0, scripted=None):
    """The identity of a browser target whose page (the browser's record) is at `url`; `scripted` is what
    the page's own script reports as its URL, when it differs."""
    return Computer._identity({'kind': 'browser', 'page': SimpleNamespace(url=url), 'navigation': navigation},
                              {'url': scripted if scripted is not None else url})


@pytest.mark.parametrize('first,second', [
    ('blob:https://a.example/id', 'blob:https://b.example/id'),   # a blob's origin is its inner URL's
    ('https://user@a.example/', 'https://user@b.example/'),       # userinfo dropped, the host kept
    ('https://a.example/', 'https://a.example./'),                # a trailing dot is another origin
    ('https://a.example/', 'http://a.example/'),
    ('https://a.example/', 'https://a.example:8443/'),
])
def test_a_browser_identity_keeps_distinct_origins_apart(first, second):
    """Codex round 1, gap 2: the approval identity is the document's origin, never the destination-admission
    parser's reading of it (which dropped credentialed and blob: URLs to one 'no-origin' and normalised hosts)."""
    assert _browser_identity(first) != _browser_identity(second)


def test_a_browser_identity_is_the_origin_alone_whatever_the_rest_of_the_url():
    same = ['https://a.example/', 'https://A.EXAMPLE:443/path?q#f', 'https://user:pw@a.example/x', 'blob:https://a.example/123']
    assert len({_browser_identity(url) for url in same}) == 1
    assert _browser_identity('https://a.example/') == 'browser\x00https://a.example:443'
    assert _browser_identity('http://[::1]:8080/') == 'browser\x00http://[::1]:8080'


@pytest.mark.parametrize('url', ['data:text/html,A', 'about:blank', 'file:///etc/hosts', 'javascript:1', ''])
def test_an_opaque_origin_is_this_document_alone(url):
    """An opaque or unknown origin remembers nothing across documents: the grant is for this document,
    until the main frame navigates again (data:text/html,A then data:text/html,B are two navigations)."""
    assert _browser_identity(url, 3) == _browser_identity(url, 3)
    assert _browser_identity(url, 3) != _browser_identity(url, 4)
    assert _browser_identity(url, 3) != _browser_identity('https://a.example/', 3)


def test_a_browser_identity_comes_from_the_browsers_record_not_page_script():
    assert _browser_identity('https://a.example/', scripted='https://b.example/') == 'browser\x00https://a.example:443'


async def test_a_desktop_targets_identity_is_its_window_and_owner(tmp_path, monkeypatch, plain_process, other_process):
    c = Computer(tmp_path, tmp_path/'caps')
    _, client = _x11_client(monkeypatch, c, pid=str(plain_process))
    observed = await c.open('desktop', window_id='123')
    assert c.identity(observed['target_id']) == f'desktop\x00123\x00{plain_process}\x00["fixture", "Fixture"]'
    client['pid'] = str(other_process)   # the window ID reused by another application
    await c.observe(observed['target_id'])
    assert c.identity(observed['target_id']) == f'desktop\x00123\x00{other_process}\x00["fixture", "Fixture"]'


@pytest.mark.parametrize('action,args', [('click', {'x': 1, 'y': 1}), ('type', {'text': 'rm -rf ~'}), ('key', {'key': 'Return'}),
                                         ('scroll', {'dy': 100}), ('drag', {'points': [{'x': 1, 'y': 1}, {'x': 2, 'y': 2}]}),
                                         ('stroke', {'points': [{'x': 1, 'y': 1}, {'x': 2, 'y': 2}]})])
async def test_terminal_windows_are_view_only(tmp_path, monkeypatch, action, args):
    c = Computer(tmp_path, tmp_path/'caps')
    calls, _ = _x11_client(monkeypatch, c, wm_class='"gnome-terminal-server", "Gnome-terminal"', title='owner@host: ~')
    observed = await c.open('desktop', window_id='123')
    assert observed['state']['view_only'] is True
    with pytest.raises(ComputerError, match='view-only'):
        await c.act(observed['target_id'], observed['observation_id'], action, **args)
    assert not {'click', 'type', 'key', 'mousedown', 'mousemove'} & {call[0] for call in calls}
    # Nothing was dispatched, so the observation is still good; focusing the terminal (to read it) is allowed.
    after = await c.act(observed['target_id'], observed['observation_id'], 'focus')
    assert ('windowactivate', '--sync', '123') in calls and after['action_result']['action'] == 'focus'


@pytest.mark.parametrize('wm_class', ['"kgx", "org.gnome.Console"', '"ghostty", "com.mitchellh.ghostty"', '"dev.warp.Warp", "dev.warp.Warp"',
                                      '"rio", "rio"', '"contour", "contour"', '"cosmic-term", "com.system76.CosmicTerm"',
                                      '"io.elementary.terminal", "Io.elementary.terminal"', '"roxterm", "Roxterm"', '"mlterm", "mlterm"'])
def test_terminal_class_list_knows_the_other_common_emulators(wm_class):
    assert computer_module._view_only({'wm_class': re.findall(r'"([^"]*)"', wm_class), 'pid': int(NEVER_A_PID)}) == 'Terminal window'


async def test_an_unlisted_terminal_is_view_only_when_its_process_holds_a_pty(tmp_path, monkeypatch):
    """An emulator the class list does not know. The signal left is the pseudo-terminal master its
    process holds (/proc/<pid>/fd -> /dev/ptmx), read from the real /proc of a real child here."""
    child = subprocess.Popen([sys.executable, '-c', 'import os,time; os.openpty(); print("ready", flush=True); time.sleep(60)'],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'ready'
        c = Computer(tmp_path, tmp_path/'caps')
        calls, _ = _x11_client(monkeypatch, c, pid=str(child.pid), wm_class='"newterm", "Newterm"', title='owner@host: ~')
        observed = await c.open('desktop', window_id='123')
        assert observed['state']['view_only'] is True and 'Terminal window' in observed['state']['note']
        with pytest.raises(ComputerError, match='view-only'):
            await c.act(observed['target_id'], observed['observation_id'], 'type', text='rm -rf ~')
        assert 'type' not in {call[0] for call in calls}
    finally:
        child.terminate()
        child.wait()


def test_native_missing_xprop_reports_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv('DISPLAY', ':96')
    monkeypatch.setenv('XDG_SESSION_TYPE', 'x11')
    monkeypatch.setattr(computer_module.shutil, 'which', lambda name: None if name == 'xprop' else '/fake/'+name)
    assert Computer(tmp_path, tmp_path/'caps').capabilities()['desktop'] is False


# --- live Blender (DREAM-109): Dream's own nested display is drivable without _NET_WM_PID -----------------

LIVE_TITLE = 'Dream live Blender: project [abcd]'
XEPHYR_CLASS = '"Xephyr", "Xephyr"'


def _xephyr_stand_in(tmp_path, title):
    """A stand-in for the Xephyr that Dream's live-Blender launcher starts: a process below this one whose
    command is named Xephyr and carries the window's exact title after -title, as the launcher's does
    (core/execution.py nested_display_argv). It opens no window anywhere."""
    xephyr = tmp_path / 'Xephyr'
    if not xephyr.exists():
        xephyr.symlink_to(sys.executable)
    return subprocess.Popen([str(xephyr), '-c', 'import time; time.sleep(120)', '-title', title],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def launched_xephyr(tmp_path):
    child = _xephyr_stand_in(tmp_path, LIVE_TITLE)
    try:
        yield child
    finally:
        child.terminate()
        child.wait()


def test_the_launched_xephyr_is_found_by_its_exact_title_while_it_lives(tmp_path, launched_xephyr):
    assert computer_module._launched_xephyr(LIVE_TITLE) == launched_xephyr.pid
    assert computer_module._launched_xephyr('Dream live Blender: project [ffff]') is None
    launched_xephyr.terminate()
    launched_xephyr.wait()
    assert computer_module._launched_xephyr(LIVE_TITLE) is None


async def test_dreams_live_blender_window_is_drivable_without_net_wm_pid(tmp_path, monkeypatch, launched_xephyr):
    """The regression: Xephyr sets no _NET_WM_PID, so since d21caa8 the live-Blender window was of unknown
    ownership and view-only. Its owner is now the Xephyr Dream's launcher started with this exact title,
    found in Dream's own process tree: the window is drivable (click and type dispatch), keyed to that
    process, and not Dream's own."""
    c = Computer(tmp_path, tmp_path/'caps')
    calls, _ = _x11_client(monkeypatch, c, pid='', wm_class=XEPHYR_CLASS, title=LIVE_TITLE)
    observed = await c.open('desktop', window_id='123')
    assert observed['state']['view_only'] is False
    assert observed['state']['pid'] == launched_xephyr.pid
    assert computer_module._own_window(observed['state']) is False
    clicked = await c.act(observed['target_id'], observed['observation_id'], 'click', x=5, y=5)
    assert ('click', '1') in calls
    await c.act(observed['target_id'], clicked['observation_id'], 'type', text='hello')
    assert any(call[0] == 'type' and call[-1] == 'hello' for call in calls)
    assert c.identity(observed['target_id']) == f'desktop\x00123\x00{launched_xephyr.pid}\x00["Xephyr", "Xephyr"]'


@pytest.mark.parametrize('how', ['no such process', 'another title', 'not xephyr', 'process gone'])
async def test_a_live_blender_shaped_window_without_dreams_xephyr_is_view_only(tmp_path, monkeypatch, how):
    """Registration cannot be spoofed: a window with the title prefix but no Xephyr of Dream's showing that
    exact title, one of another WM_CLASS, or one whose Xephyr is gone, is of unknown ownership: view-only.
    (These held before the fix too -- the fix admits only the genuine case.)"""
    stand_in = None
    if how in ('another title', 'not xephyr', 'process gone'):
        stand_in = _xephyr_stand_in(tmp_path, LIVE_TITLE)
    if how == 'process gone':
        stand_in.terminate()
        stand_in.wait()
    try:
        c = Computer(tmp_path, tmp_path/'caps')
        title = 'Dream live Blender: project [ffff]' if how == 'another title' else LIVE_TITLE
        calls, _ = _x11_client(monkeypatch, c, pid='', wm_class='"someapp", "Someapp"' if how == 'not xephyr' else XEPHYR_CLASS, title=title)
        observed = await c.open('desktop', window_id='123')
        assert observed['state']['view_only'] is True and 'unknown ownership' in observed['state']['note']
        assert observed['state']['pid'] is None
        with pytest.raises(ComputerError, match='view-only'):
            await c.act(observed['target_id'], observed['observation_id'], 'click', x=5, y=5)
        assert 'click' not in {call[0] for call in calls}
    finally:
        if stand_in is not None and stand_in.poll() is None:
            stand_in.terminate()
            stand_in.wait()


async def test_a_live_blender_window_is_view_only_once_its_xephyr_has_stopped(tmp_path, monkeypatch, launched_xephyr):
    """After the launcher stops the nested display, the same window id (reused or not) is trusted no more:
    the owner is re-derived on every read, and without the Xephyr the window is of unknown ownership."""
    c = Computer(tmp_path, tmp_path/'caps')
    calls, _ = _x11_client(monkeypatch, c, pid='', wm_class=XEPHYR_CLASS, title=LIVE_TITLE)
    observed = await c.open('desktop', window_id='123')
    assert observed['state']['view_only'] is False
    launched_xephyr.terminate()
    launched_xephyr.wait()
    with pytest.raises(ComputerError, match='state changed'):   # the owner is gone: another window now
        await c.act(observed['target_id'], observed['observation_id'], 'click', x=5, y=5)
    observed = await c.observe(observed['target_id'])
    assert observed['state']['view_only'] is True and observed['state']['pid'] is None
    with pytest.raises(ComputerError, match='view-only'):
        await c.act(observed['target_id'], observed['observation_id'], 'type', text='x')
    assert not {'click', 'type'} & {call[0] for call in calls}
