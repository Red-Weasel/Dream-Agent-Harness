"""Session-owned computer targets with observed, single-use action references.

The controlled browser is separate from Studio and the native browser. Desktop
control is explicitly attached to an X11 window, never a guessed active target.
All mutations are exposed through the normal tool permission middleware.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import time
from uuid import uuid4

from PIL import ImageGrab
from . import config


class ComputerError(ValueError):
    pass


_DOM = r"""() => {
  const all=[...document.querySelectorAll('button,a[href],input,textarea,select,[role=button],[role=tab],[contenteditable=true]')];
  const elements=all.map((e,index)=>{
    const r=e.getBoundingClientRect(),s=getComputedStyle(e);
    if(!r.width||!r.height||s.visibility==='hidden'||s.display==='none')return null;
    return {id:String(index),tag:e.tagName.toLowerCase(),role:e.getAttribute('role')||'',
      name:(e.getAttribute('aria-label')||e.labels?.[0]?.innerText||e.innerText||e.getAttribute('placeholder')||'').trim().slice(0,200),
      type:e.type||'',value:e.type==='password'?'[redacted]':String(e.value??'').slice(0,200),
      href:e.href||'',formAction:e.hasAttribute('formaction')?e.formAction:(e.form?.action||''),
      formMethod:e.hasAttribute('formmethod')?e.formMethod:(e.form?.method||''),
      disabled:!!e.disabled,checked:!!e.checked,
      rect:[r.x,r.y,r.width,r.height].map(v=>Math.round(v)),visible:r.bottom>0&&r.right>0&&r.top<innerHeight&&r.left<innerWidth};
  }).filter(Boolean).sort((a,b)=>Number(b.visible)-Number(a.visible)).slice(0,500);
  return {url:location.href,title:document.title,viewport:[innerWidth,innerHeight],scroll:[scrollX,scrollY],focus:all.indexOf(document.activeElement),
    text:(document.body?.innerText||'').slice(0,6000),elements,
    note:'Main document controls only; embedded frames and closed shadow roots require another available route.'};
}"""
_SELECTOR = 'button,a[href],input,textarea,select,[role=button],[role=tab],[contenteditable=true]'


def _fingerprint(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


_BROWSER_MODIFIERS = {'Control', 'Alt', 'Shift', 'Meta', 'ControlOrMeta',
                      'ControlLeft', 'ControlRight', 'AltLeft', 'AltRight',
                      'ShiftLeft', 'ShiftRight', 'MetaLeft', 'MetaRight'}
_BROWSER_KEYS = set("Backspace Tab Enter Escape Space Delete Insert Home End PageUp PageDown "
                    "ArrowUp ArrowDown ArrowLeft ArrowRight CapsLock NumLock ScrollLock Pause "
                    "PrintScreen ContextMenu AltGraph Backquote Minus Equal BracketLeft "
                    "BracketRight Backslash Semicolon Quote Comma Period Slash "
                    "NumpadDivide NumpadMultiply NumpadSubtract NumpadAdd NumpadDecimal NumpadEnter "
                    "AudioVolumeMute AudioVolumeDown AudioVolumeUp MediaTrackNext "
                    "MediaTrackPrevious MediaPlayPause".split())
_BROWSER_ALIASES = {'ctrl': 'Control', 'control': 'Control', 'shift': 'Shift',
                    'alt': 'Alt', 'option': 'Alt', 'meta': 'Meta', 'cmd': 'Meta',
                    'command': 'Meta', 'esc': 'Escape', 'return': 'Enter', 'del': 'Delete'}


def _browser_key(key):
    parts = [_BROWSER_ALIASES.get(p.lower(), p) for p in key.split('+')]
    modifiers = [re.sub(r'(Left|Right)$', '', p) for p in parts[:-1]]
    duplicate = len(modifiers) != len(set(modifiers)) or ('ControlOrMeta' in modifiers and bool({'Control','Meta'} & set(modifiers)))
    if (duplicate or any(p not in _BROWSER_MODIFIERS for p in parts[:-1]) or
        not all(p in _BROWSER_KEYS or p in _BROWSER_MODIFIERS or
                re.fullmatch(r'[A-Za-z0-9_]|Key[A-Z]|Digit[0-9]|Numpad[0-9]|F(?:[1-9]|1[0-2])', p) for p in parts)):
        raise ComputerError('Unsupported browser key/chord; use named keys and Control/Alt/Shift/Meta modifiers. No input dispatched.')
    return '+'.join(parts)


async def _command(*args: str, env=None) -> str:
    """Fixed binary/argument operations; no model-supplied shell or scripts."""
    process = await asyncio.create_subprocess_exec(*args, env=env, stdout=asyncio.subprocess.PIPE,
                                                  stderr=asyncio.subprocess.PIPE)
    try:
        out, error = await asyncio.wait_for(process.communicate(), 5)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await asyncio.shield(process.wait())
        raise
    if process.returncode:
        raise ComputerError(error.decode(errors='replace')[:1000] or f'{args[0]} failed ({process.returncode})')
    return out.decode(errors='replace')[:16000].strip()


async def _release_input(operation):
    """Finish bounded release before surrendering ownership, even on repeated Stop."""
    cleanup = asyncio.create_task(asyncio.wait_for(operation, 5))
    cancelled = None
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as exc:
            cancelled = exc
    cleanup.result()  # Release failure must remain visible.
    if cancelled is not None:
        raise cancelled


class Computer:
    def __init__(self, workspace: Path, captures: Path):
        self.workspace = Path(workspace).resolve()
        self.captures = captures
        self.targets = {}
        self._pw = self._browser = None
        self._lock = asyncio.Lock()

    def capabilities(self):
        desktop = (os.environ.get('XDG_SESSION_TYPE') != 'wayland'
                   and re.fullmatch(r':\d+(?:\.\d+)?', os.environ.get('DISPLAY', '')) is not None
                   and bool(shutil.which('xdotool')) and bool(shutil.which('xwininfo')))
        return {'browser': 'Chromium starts on explicit open; runtime not yet qualified',
                'desktop': bool(desktop), 'desktop_note': 'X11, xdotool and xwininfo required; Wayland control is unavailable',
                'drawing': {'actions': ['click', 'drag', 'stroke'], 'button': 'left',
                            'max_points': 128, 'duration_ms': {'minimum': 1, 'maximum': 2000, 'optional': True},
                            'max_paced_points': 256, 'browser_coordinates': 'viewport CSS pixels',
                            'desktop_coordinates': 'unscaled window-relative pixels'},
                'targets': [{'target_id': k, 'kind': v['kind']} for k, v in self.targets.items()]}

    async def open(self, kind: str, *, url: str = '', window_id: str = ''):
        async with self._lock:
            if len(self.targets) >= 4:
                raise ComputerError('Close an owned target before opening another (maximum four).')
            token = uuid4().hex
            if kind == 'browser':
                if not re.match(r'^https?://[^/\s]+', url) or len(url) > 4000:
                    raise ComputerError('Browser targets require an explicit http or https URL.')
                if self._browser is None:
                    from playwright.async_api import async_playwright
                    self._pw = await async_playwright().start()
                    try:
                        self._browser = await self._pw.chromium.launch(headless=True)
                    except BaseException:
                        await self._pw.stop()
                        self._pw = None
                        raise
                context = await self._browser.new_context(viewport={'width': 1280, 'height': 800},
                                                           accept_downloads=False, service_workers='block')
                async def route(request):
                    if request.request.url.startswith(('http://', 'https://', 'data:', 'blob:', 'about:')):
                        await request.continue_()
                    else:
                        await request.abort()
                await context.route('**/*', route)
                try:
                    page = await context.new_page()
                    context.on('page', lambda other: asyncio.create_task(other.close()) if other is not page else None)
                    page.on('dialog', lambda dialog: asyncio.create_task(dialog.dismiss()))
                    await page.goto(url, wait_until='domcontentloaded', timeout=15000)
                except BaseException:
                    await context.close()
                    raise
                target = {'kind': kind, 'context': context, 'page': page, 'navigation': 0}
                def navigated(frame):
                    if frame == page.main_frame:
                        target['navigation'] += 1
                page.on('framenavigated', navigated)
            elif kind == 'desktop':
                if not self.capabilities()['desktop']:
                    raise ComputerError('Desktop control requires X11, xdotool and xwininfo. Wayland is unsupported.')
                if not re.fullmatch(r'[1-9][0-9]{0,11}|0x[0-9a-fA-F]{1,8}', window_id):
                    raise ComputerError('Choose an explicit numeric X11 window ID from the window list.')
                target = {'kind': kind, 'window_id': str(int(window_id, 0) if window_id.startswith('0x') else int(window_id))}
                await self._desktop_state(target)  # Attach only; never switch focus implicitly.
            else:
                raise ComputerError('Target kind must be browser or desktop.')
            self.targets[token] = target
            try:
                return await self._observe(token, target)
            except BaseException:
                self.targets.pop(token, None)
                if kind == 'browser':
                    await context.close()
                raise

    async def windows(self):
        if not self.capabilities()['desktop'] or not shutil.which('wmctrl'):
            raise ComputerError('Listing desktop windows requires X11, xdotool, xwininfo and wmctrl.')
        output = await _command(shutil.which('wmctrl'), '-lp')
        rows = []
        for line in output.splitlines()[:64]:
            parts = line.split(None, 4)
            if len(parts) == 5:
                rows.append({'window_id': parts[0], 'pid': parts[2], 'title': parts[4][:200]})
        return {'windows': rows, 'note': 'Choose a window explicitly; opening a target does not focus it.'}

    def _target(self, identifier):
        try:
            return self.targets[identifier]
        except KeyError:
            raise ComputerError('Unknown target in this session; open or list targets first.') from None

    async def _desktop_state(self, target):
        binary = shutil.which('xdotool')
        geometry_binary = shutil.which('xwininfo')
        if not binary or not geometry_binary:
            raise ComputerError('xdotool or xwininfo is unavailable.')
        window = target['window_id']
        # libxdo's location helper double-counts parent offsets on decorated
        # clients. xwininfo reports the actual root origin used by our crop;
        # xdotool mousemove --window already translates client points correctly.
        geometry = await _command(geometry_binary, '-id', window, env={**os.environ, 'LC_ALL': 'C'})
        rect = []
        for field in ('Absolute upper-left X', 'Absolute upper-left Y', 'Width', 'Height'):
            values = re.findall(r'^\s*'+re.escape(field)+r':\s*(-?\d+)\s*$', geometry, re.MULTILINE)
            if len(values) != 1:
                raise ComputerError('Malformed desktop client geometry from xwininfo.')
            rect.append(int(values[0]))
        if not 1 <= rect[2] <= 7680 or not 1 <= rect[3] <= 4320:
            raise ComputerError('Unsupported desktop window dimensions.')
        return {'window_id': window, 'title': await _command(binary, 'getwindowname', window),
                'focus': await _command(binary, 'getwindowfocus'), 'rect': rect,
                'coordinate_space': 'unscaled window-relative pixels',
                'note': 'Desktop stale checks cover window title, focus and geometry. Pixels are an on-screen crop that may include overlays; inspect the visible target before acting.'}

    async def _state(self, target):
        if target['kind'] == 'browser':
            return {**await asyncio.wait_for(target['page'].evaluate(_DOM), 5), 'navigation': target['navigation']}
        return await self._desktop_state(target)

    async def _capture(self, target, state):
        if target['kind'] == 'browser':
            return await target['page'].screenshot(type='png', timeout=5000)
        # Capturing an obscured/background window would misidentify the pixels.
        if state['focus'] != state['window_id']:
            return None
        x, y, w, h = state['rect']
        def capture():
            shot = ImageGrab.grab(bbox=(x, y, x+w, y+h), xdisplay=os.environ['DISPLAY'])
            buffer = io.BytesIO()
            shot.save(buffer, format='PNG')
            return buffer.getvalue()
        return await asyncio.to_thread(capture)

    async def _settled_capture(self, target):
        latest = None
        async def sample_until_quiet():
            nonlocal latest
            if target['kind'] == 'browser':
                await target['page'].evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))')
            started = time.monotonic()
            quiet_since = started
            previous = None
            while True:
                state = await self._state(target)
                pixels = await self._capture(target, state)
                consistent = await self._state(target) == state
                if consistent:
                    latest = (state, pixels)
                signature = (_fingerprint(state), hashlib.sha256(pixels).hexdigest() if pixels else None)
                now = time.monotonic()
                if not consistent or signature != previous:
                    quiet_since = now
                if consistent and now-started >= .5 and now-quiet_since >= .25:
                    return state, pixels, True
                previous = signature if consistent else None
                await asyncio.sleep(.1)
        deadline = asyncio.timeout(3)
        try:
            async with deadline:
                return await sample_until_quiet()
        except asyncio.TimeoutError as exc:
            if not deadline.expired():
                raise
            if latest is not None:
                return *latest, False
            return None, None, False  # Readiness hints must not prevent an ordinary fresh capture.

    async def _observe(self, identifier, target, offset=0):
        # A failed refresh must not pair an old token with newly acquired handles.
        target.pop('observation', None)
        target.pop('visible_ids', None)
        await self._release_elements(target)
        state, settled_pixels, sampled_quiet = await self._settled_capture(target)
        if state is None:
            state = await self._state(target)
        if target['kind'] == 'browser':
            collection = await target['page'].evaluate_handle(
                '(ids)=>{const nodes=document.querySelectorAll('+json.dumps(_SELECTOR)+');return Object.fromEntries(ids.map(id=>[id,nodes[Number(id)]]));}',
                [e['id'] for e in state['elements'][offset:offset+30]])
            try:
                target['elements'] = await collection.get_properties()
            finally:
                await collection.dispose()
        pixels = await self._capture(target, state)
        # Do not issue an actionable token for state that changed during capture.
        if await self._state(target) != state:
            target.pop('observation', None)
            raise ComputerError('Interface changed during capture; observe again before acting.')
        observation = uuid4().hex
        path = None
        if pixels:
            self.captures.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self.captures / (identifier + '-' + observation + '.png')
            path.write_bytes(pixels)
            path.chmod(0o600)
        public = json.loads(json.dumps(state))
        if 'elements' in public:
            elements=public['elements']
            if type(offset) is not int or offset<0 or (offset>=len(elements) and offset!=0):
                raise ComputerError('Element offset is outside the current observation.')
            public.update(elements=elements[offset:offset+30], element_offset=offset, total_elements=len(elements))
        value = {'target_id': identifier, 'kind': target['kind'], 'observation_id': observation,
                'state': public, 'screenshot': str(path) if path else None,
                'image_note': 'Fresh pixels attached when image input is available.' if path else 'No pixels: selected window is not focused. Focus it, then observe.',
                'verification': 'observed state, not a task-success judgment',
                'readiness': {'status': 'pixels_unavailable' if pixels is None else
                              ('sampled_quiet' if sampled_quiet and pixels == settled_pixels else 'unsettled'),
                              'note': 'Bounded recent samples only; later changes remain possible. Action stale/pixel checks still apply.'}}
        # Bound serialized JSON, including escaped control characters. Keep room
        # for action metadata; never hand transport an invalid truncated object.
        budget=config.TOOL_RESULT_CAP-1800
        while len(json.dumps(value,ensure_ascii=False))>budget:
            if len(public.get('elements',[]))>1:
                public['elements'].pop()
            elif len(public.get('text',''))>128:
                public['text']=public['text'][:len(public['text'])//2]
                public['text_truncated']=True
            else:
                def shorten(item):
                    if isinstance(item,dict):return {k:shorten(v) for k,v in item.items()}
                    if isinstance(item,list):return [shorten(v) for v in item]
                    return item[:max(32,len(item)//2)] if isinstance(item,str) and len(item)>64 else item
                shortened=shorten(public)
                if shortened==public:
                    raise ComputerError('Tool result budget is too small for an observation.')
                public.clear();public.update(shortened);public['text_truncated']=True
        if 'elements' in public:
            end=offset+len(public['elements'])
            public['next_element_offset']=end if end<public['total_elements'] else None
            public['elements_note']='Up to 500 visible-first main-document controls; page with element_offset when present.'
            target['visible_ids']={e['id'] for e in public['elements']}
        target.update(observation=observation, fingerprint=_fingerprint(state), observed=time.monotonic(),
                      pixels_sha256=hashlib.sha256(pixels).hexdigest() if pixels else None)
        return value

    async def observe(self, identifier, *, offset=0):
        async with self._lock:
            return await self._observe(identifier, self._target(identifier), offset)

    async def _release_elements(self, target):
        for handle in target.pop('elements', {}).values():
            await handle.dispose()

    async def act(self, identifier, observation, action, **args):
        async with self._lock:
            target = self._target(identifier)
            if action not in {'click', 'type', 'key', 'scroll', 'focus', 'drag', 'stroke'}:
                raise ComputerError('Use click, drag, stroke, type, key, scroll or desktop focus.')
            if not observation or target.get('observation') != observation or time.monotonic()-target.get('observed', 0) > 300:
                raise ComputerError('Observation is stale or already used; observe again.')
            before = await self._state(target)
            if _fingerprint(before) != target['fingerprint']:
                target.pop('observation', None)
                raise ComputerError('Target state changed; observe again instead of replaying the action.')
            text = args.get('text', '')
            key = args.get('key', '')
            if action == 'type' and (not isinstance(text, str) or not text or len(text) > 4000 or '\x00' in text):
                raise ComputerError('Type needs 1–4000 text characters without NUL.')
            if action == 'key' and (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_+]{1,80}', key)):
                raise ComputerError('Key needs a named key or chord, such as Tab or Control+Return.')
            if action == 'key' and target['kind'] == 'browser':
                args['key'] = _browser_key(key)
            allowed = ({'click': {'element_id','x','y'}, 'drag': {'points','duration_ms'}, 'stroke': {'points','duration_ms'}, 'type': {'element_id','text'}, 'key': {'key'}, 'scroll': {'dx','dy'}}
                       if target['kind']=='browser' else
                       {'click': {'x','y'}, 'drag': {'points','duration_ms'}, 'stroke': {'points','duration_ms'}, 'type': {'text'}, 'key': {'key'}, 'scroll': {'dy'}, 'focus': set()})
            if action not in allowed or set(args)-allowed[action]:
                raise ComputerError('Unsupported action or unused parameters for this target; check the action schema.')
            if action=='scroll':
                self._deltas(args)
            coordinate = action in {'drag','stroke'} or (action=='click' and 'element_id' not in args)
            if action=='click' and 'element_id' in args and ({'x','y'} & set(args)):
                raise ComputerError('Use either element_id or x/y, not both.')
            if action in {'click','type'} and target['kind']=='browser' and not coordinate:
                entry=next((e for e in before['elements'] if e['id']==args.get('element_id')),None)
                if entry is None or entry['disabled'] or entry['id'] not in target.get('visible_ids',set()):
                    raise ComputerError('Choose an enabled element_id from the current observation.')
            if coordinate:
                points = self._points(before, target['kind'], action, args)
                if 'duration_ms' in args:
                    self._paced_plan(points, args['duration_ms'])
                pixels = await self._capture(target, before)
                if pixels is None or hashlib.sha256(pixels).hexdigest() != target.get('pixels_sha256'):
                    target.pop('observation', None)
                    raise ComputerError('Target pixels changed or unavailable; observe again.')
                if await self._state(target) != before:
                    target.pop('observation', None)
                    raise ComputerError('Target state changed during pixel check; observe again.')
            target.pop('observation', None)  # Consume before any possible side effect, including timeout.
            try:
                if target['kind'] == 'browser':
                    timing = await asyncio.wait_for(self._browser_action(target, before, action, args), 8)
                else:
                    # Preserve 4000-character input with conservative keystroke
                    # pacing and time for bounded per-chunk state checks.
                    timeout = 15 + len(text)*.012 + math.ceil(len(text)/128) if action == 'type' else 15
                    timing = await asyncio.wait_for(self._desktop_action(target, before, action, args), timeout)
            except Exception as exc:
                raise ComputerError(f'Action outcome is uncertain: {type(exc).__name__}: {exc}. Observe before retrying; no action was replayed.') from exc
            try:
                after = await self._observe(identifier, target)
            except Exception as exc:
                # Dispatch returned normally. A closed dialog or failed capture
                # must not turn that evidence into an invitation to replay input.
                target.pop('observation', None)
                target.pop('visible_ids', None)
                after = {'target_id': identifier, 'kind': target['kind'],
                         'observation_id': None, 'screenshot': None,
                         'observation_error': {'type': type(exc).__name__, 'message': str(exc)[:1000]},
                         'recovery': 'Do not replay the action. Observe this same target to verify the application outcome. '
                                     'If the window is gone, explicitly list windows and select/open the intended replacement; '
                                     'no target was switched automatically.',
                         'verification': 'Input dispatch completed; subsequent observation failed. Task success is unverified.'}
            after['action_result'] = {'action': action, 'before_observation_id': observation,
                                      'before_state_sha256': _fingerprint(before), 'after_observation_id': after['observation_id'],
                                      'outcome': 'dispatch completed; verify the requested result',
                                      'source': 'computer adapter', 'task_success': 'unverified'}
            if 'observation_error' in after:
                after['action_result']['action_dispatched'] = True
            if timing is not None:
                after['action_result'].update(timing)
            return after

    async def _browser_action(self, target, state, action, args):
        page = target['page']
        if action in {'drag', 'stroke'} or (action == 'click' and 'element_id' not in args):
            points = self._points(state, 'browser', action, args)
            plan = self._paced_plan(points, args['duration_ms']) if 'duration_ms' in args else None
            hit = await page.evaluate_handle('([x,y])=>document.elementFromPoint(x,y)', points[0])
            try:
                await page.mouse.move(*points[0])
                current = await self._state(target)
                if any(current[k] != state[k] for k in ('viewport', 'scroll', 'navigation', 'url', 'elements')):
                    raise ComputerError('Browser target or actionable controls changed before press.')
                if not await hit.evaluate('(e,[x,y])=>!!e && e.isConnected && document.elementFromPoint(x,y)===e', points[0]):
                    raise ComputerError('Coordinate target was replaced or overlaid before press; observe again.')
            finally:
                await hit.dispose()
            async def check():
                current = await self._state(target)
                if any(current[k] != state[k] for k in ('viewport', 'scroll', 'navigation', 'url')):
                    raise ComputerError('Browser viewport or navigation changed during stroke.')
            timing = None
            try:
                await page.mouse.down(button='left')
                if plan is not None:
                    timing = await self._paced_moves(plan, check, lambda point: page.mouse.move(*point))
                else:
                    for point in points[1:]:
                        # Pressing can change focus/DOM; never continue across viewport or navigation changes.
                        await check()
                        await page.mouse.move(*point)
            finally:
                await _release_input(page.mouse.up(button='left'))
            return timing
        elif action in {'click', 'type'}:
            entry = next((e for e in state['elements'] if e['id'] == args.get('element_id')), None)
            if entry is None:
                raise ComputerError('Choose an element_id from the current observation.')
            if entry['disabled']:
                raise ComputerError('Target control is disabled.')
            handle = target.get('elements', {}).get(entry['id'])
            element = handle.as_element() if handle is not None else None
            if element is None or not await element.evaluate('(e)=>e.isConnected'):
                raise ComputerError('Observed element was replaced; observe again.')
            if action == 'click':
                await element.click(timeout=3000)
            else:
                await element.fill(args['text'], timeout=3000)
        elif action == 'key':
            attempted = []
            async def release_keys():
                failures = []
                for key in reversed(attempted):
                    try:
                        await page.keyboard.up(key)
                    except Exception as exc:
                        failures.append(exc)
                if failures:
                    raise failures[0]
            dispatch_error = None
            try:
                for key in args['key'].split('+'):
                    attempted.append(key)
                    await page.keyboard.down(key)
            except BaseException as exc:
                dispatch_error = exc
            try:
                await _release_input(release_keys())
            except BaseException as cleanup_error:
                if dispatch_error is None:
                    raise
                if isinstance(cleanup_error, asyncio.CancelledError):
                    cleanup_error.add_note(f'Keyboard dispatch also failed: {dispatch_error}')
                    raise cleanup_error from dispatch_error
                if isinstance(dispatch_error, asyncio.CancelledError):
                    dispatch_error.add_note(f'Keyboard release also failed: {cleanup_error}')
                    raise dispatch_error from cleanup_error
                raise ComputerError(f'Keyboard dispatch failed: {dispatch_error}; keyboard release also failed: {cleanup_error}') from dispatch_error
            if dispatch_error is not None:
                raise dispatch_error
        elif action == 'scroll':
            dx, dy = self._deltas(args)
            await page.mouse.wheel(dx, dy)
            await page.wait_for_timeout(100)
        else:
            raise ComputerError('Browser targets already have their own focus; focus is a desktop action.')

    @staticmethod
    def _points(state, kind, action, args):
        points = [{'x': args.get('x'), 'y': args.get('y')}] if action == 'click' else args.get('points')
        low, high = (1, 1) if action == 'click' else ((2, 2) if action == 'drag' else (2, 128))
        if not isinstance(points, list) or not low <= len(points) <= high:
            raise ComputerError(f'{action} needs {low}–{high} points.')
        width, height = state['viewport'] if kind == 'browser' else state['rect'][2:]
        if any(not isinstance(p, dict) or set(p) != {'x','y'} or
               type(p['x']) is not int or type(p['y']) is not int or
               not 0 <= p['x'] < width or not 0 <= p['y'] < height for p in points):
            raise ComputerError('All points need integer x/y inside the observed viewport/window.')
        return [(p['x'], p['y']) for p in points]

    @staticmethod
    def _paced_plan(points, duration_ms):
        if type(duration_ms) is not int or not 1 <= duration_ms <= 2000:
            raise ComputerError('duration_ms needs an integer from 1 to 2000 for drag/stroke.')
        duration = duration_ms / 1000
        lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
        total = sum(lengths)
        if not total:
            count = math.ceil(duration / .02)
            return [(duration*i/count, points[0]) for i in range(count+1)]
        segments = []
        for a, b, length in zip(points, points[1:], lengths):
            seconds = duration * length / total
            # Two-pixel unrounded steps keep rounded native moves within four pixels.
            count = max(1, math.ceil(length/2), math.ceil(seconds/.02))
            segments.append((a, b, seconds, count))
        if 1 + sum(n for _, _, _, n in segments) > 256:
            raise ComputerError('Paced stroke needs too many subdivisions (maximum 256 points); use a shorter path.')
        plan = [(0, points[0])]
        elapsed = 0
        for a, b, seconds, count in segments:
            for i in range(1, count+1):
                point = b if i == count else tuple(round(x+(y-x)*i/count) for x,y in zip(a,b))
                plan.append((elapsed+seconds*i/count, point))
            elapsed += seconds
        plan[-1] = (duration, points[-1])
        return plan

    @staticmethod
    async def _paced_moves(plan, check, move):
        started = time.monotonic()
        for scheduled, point in plan[1:]:
            await check()
            await asyncio.sleep(max(0, started+scheduled-time.monotonic()))
            await check()
            await move(point)
        await check()
        return {'requested_duration_ms': round(plan[-1][0]*1000),
                'actual_hold_ms': round((time.monotonic()-started)*1000, 1)}

    @staticmethod
    def _deltas(args):
        dx, dy = args.get('dx', 0), args.get('dy', 0)
        if any(type(n) is not int or abs(n) > 2000 for n in (dx, dy)) or not (dx or dy):
            raise ComputerError('Scroll needs integer dx/dy within ±2000 and a nonzero delta.')
        return dx, dy

    async def _desktop_action(self, target, state, action, args):
        binary = shutil.which('xdotool')
        window = target['window_id']
        if action == 'focus':
            await _command(binary, 'windowactivate', '--sync', window)
            return
        if state['focus'] != window:
            raise ComputerError('Selected window is not focused. Focus it explicitly, then observe.')
        if action in {'drag', 'stroke'}:
            points = self._points(state, 'desktop', action, args)
            plan = self._paced_plan(points, args['duration_ms']) if 'duration_ms' in args else None
            async def check():
                current = await self._desktop_state(target)
                if any(current[k] != state[k] for k in ('focus', 'rect', 'title')):
                    raise ComputerError('Desktop focus or geometry changed during stroke.')
            await check()
            await _command(binary, 'mousemove', '--window', window, *map(str, points[0]))
            await check()
            timing = None
            try:
                await _command(binary, 'mousedown', '1')
                if plan is not None:
                    async def move(point):
                        await _command(binary, 'mousemove', '--window', window, *map(str, point))
                    timing = await self._paced_moves(plan, check, move)
                else:
                    for point in points[1:]:
                        await check()
                        await _command(binary, 'mousemove', '--window', window, *map(str, point))
                    await check()
            finally:
                await _release_input(_command(binary, 'mouseup', '1'))
            return timing
        elif action == 'click':
            x, y = args.get('x'), args.get('y')
            if type(x) is not int or type(y) is not int or not 0 <= x < state['rect'][2] or not 0 <= y < state['rect'][3]:
                raise ComputerError('Click needs integer x/y within the observed window.')
            await _command(binary, 'mousemove', '--window', window, str(x), str(y))
            if await _command(binary, 'getwindowfocus') != window:
                raise ComputerError('Focus changed before click.')
            await _command(binary, 'click', '1')
        elif action == 'type':
            async def check_typing():
                current = await self._desktop_state(target)
                if any(current[k] != state[k] for k in ('focus', 'rect', 'title')):
                    raise ComputerError('Desktop focus, title or geometry changed during typing; partial text may remain.')
            await check_typing()
            for offset in range(0, len(args['text']), 128):
                try:
                    # Finish the current small chunk on Stop, allowing xdotool
                    # to release generated keys and restore cleared modifiers.
                    # Subprocess/server failures cannot guarantee that cleanup.
                    await _release_input(_command(binary, 'type', '--clearmodifiers', '--delay', '12', '--', args['text'][offset:offset+128]))
                except asyncio.CancelledError as exc:
                    exc.add_note('Native typing cancelled after the current chunk; partial text may remain. Observe before retrying.')
                    raise
                except Exception as exc:
                    raise ComputerError(f'Native typing failed; partial text or held keys/modifiers may remain; cleanup could not be confirmed: {exc}') from exc
                await check_typing()
        elif action == 'key':
            if await _command(binary, 'getwindowfocus') != window:
                raise ComputerError('Focus changed before key dispatch.')
            await _command(binary, 'key', '--clearmodifiers', args['key'])
        elif action == 'scroll':
            dx, dy = self._deltas(args)
            if dx:
                raise ComputerError('Desktop scrolling supports vertical dy only.')
            await _command(binary, 'mousemove', '--window', window, str(state['rect'][2]//2), str(state['rect'][3]//2))
            if await _command(binary, 'getwindowfocus') != window:
                raise ComputerError('Focus changed before scrolling.')
            await _command(binary, 'click', '--repeat', str(min(20, max(1, abs(dy)//100))), '5' if dy > 0 else '4')

    async def close(self, identifier):
        async with self._lock:
            target = self._target(identifier)
            await self._release_elements(target)
            if target['kind'] == 'browser':
                await target['context'].close()
            del self.targets[identifier]
            return {'closed': identifier, 'note': 'Detached only; the desktop application stays open.' if target['kind']=='desktop' else 'Owned browser context closed.'}

    async def aclose(self):
        async with self._lock:
            self.targets.clear()
            try:
                if self._browser is not None:
                    await self._browser.close()
            finally:
                self._browser = None
                if self._pw is not None:
                    await self._pw.stop()
                    self._pw = None
