"""Private loopback transport from a CLI's stdio MCP to its live Dream session.

The parent supplies its actual tool-list and authorized call callbacks. This
module never constructs Engine/ToolContext and never calls a copied tool handler.
The bearer capability stays in a 0600 discovery file beneath a 0700 directory.
It is distinct from Studio/browser authentication and must not reach advisors.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
from urllib.parse import urlsplit
import uuid

import httpx
import mcp.types as mcp_types
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..core.run_state import atomic_write


DISCOVERY_ENV = 'DREAM_PARENT_BRIDGE_FILE'
_MAX_REQUEST = 1024 * 1024
_MAX_RESPONSE = 8 * 1024 * 1024


class BridgeError(RuntimeError):
    pass


def _base_url(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', '::1') or not parsed.port
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
            raise ValueError()
    except (TypeError, ValueError):
        raise BridgeError('Bridge requires an explicit HTTP loopback address and port') from None
    return value.rstrip('/')


def _error(text):
    return {'content': [{'type': 'text', 'text': text}], 'isError': True}


def _result(value):
    if isinstance(value, mcp_types.CallToolResult):
        return value.model_dump(mode='json', by_alias=True, exclude_none=True)
    if not isinstance(value, dict):
        raise BridgeError('Parent callback must return an MCP result object')
    value = dict(value)
    value['isError'] = bool(value.pop('is_error', False) or value.get('isError', False))
    return mcp_types.CallToolResult.model_validate(value).model_dump(mode='json', by_alias=True, exclude_none=True)


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


class SessionToolBridge:
    """Parent integration: routes(), publish(base_url), environment, await close().

    list_tools() may return SdkMcpTool objects or MCP tool-schema dictionaries.
    call_tool(name, arguments) must be async and enforce the session's current
    permissions/context itself. Returned content/errors/structuredContent survive.
    No retry occurs after acceptance. Duplicate request IDs return their observed
    status without repeating the handler, including after a timeout/disconnect.
    """
    def __init__(self, session_id, list_tools, call_tool, *, timeout=120.0, max_concurrency=4):
        if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
            raise ValueError('A bounded session ID is required')
        if not 0 < timeout <= 3600 or not 1 <= max_concurrency <= 32:
            raise ValueError('Bridge requires bounded timeout and concurrency')
        self.session_id = session_id
        self._list_tools, self._call_tool = list_tools, call_tool
        self.timeout = float(timeout)
        self._token = secrets.token_urlsafe(32)
        self._temp = tempfile.TemporaryDirectory(prefix='dream-parent-bridge-')
        self.discovery_path = Path(self._temp.name) / 'session.json'
        self._base = None
        self._closed = False
        self._interrupting = 0
        self._slots = asyncio.Semaphore(max_concurrency)
        self._max_pending = max_concurrency * 4
        self._pending = set()
        self._requests: dict[str, str] = {}

    def routes(self):
        return [Route('/api/mcp/tools', self._listing, methods=['GET']),
                Route('/api/mcp/call', self._calling, methods=['POST'])]

    def publish(self, base_url):
        if self._closed:
            raise BridgeError('Bridge is closed')
        self._base = _base_url(base_url)
        atomic_write(self.discovery_path, json.dumps({'version': 1, 'url': self._base, 'token': self._token,
                     'session_id': self.session_id, 'pid': os.getpid(), 'call_timeout_s': self.timeout}))
        return self.discovery_path

    @property
    def environment(self):
        if self._closed or self._base is None:
            raise BridgeError('Publish the bridge after its listener is ready')
        return {DISCOVERY_ENV: str(self.discovery_path), 'DREAM_SESSION_ID': self.session_id}

    def _authorized(self, request):
        if self._closed or self._base is None or 'origin' in request.headers:
            return False
        if not request.client or request.client.host not in ('127.0.0.1', '::1'):
            return False
        if request.headers.get('host') != urlsplit(self._base).netloc:
            return False
        return (secrets.compare_digest(request.headers.get('x-dream-bridge-token', '').encode(), self._token.encode())
                and request.headers.get('x-dream-session-id') == self.session_id)

    async def _schemas(self):
        tools = await _resolve(self._list_tools())
        if not isinstance(tools, (list, tuple)) or len(tools) > 1024:
            raise BridgeError('Invalid or oversized parent tool list')
        # SdkMcpTool.input_schema may be a Python type map or TypedDict, not
        # JSON Schema. Let the installed SDK perform its own wire conversion,
        # including required fields and annotations. This builds an in-process
        # schema registry only: no model, tool handler or ToolContext is run.
        sdk_tools = [tool for tool in tools if not isinstance(tool, (mcp_types.Tool, dict))]
        sdk_schemas = {}
        if sdk_tools:
            from claude_agent_sdk import create_sdk_mcp_server
            if len({tool.name for tool in sdk_tools}) != len(sdk_tools):
                raise BridgeError('Parent tool list contains duplicate names')
            sdk_server = create_sdk_mcp_server(name='dream-bridge-schemas', tools=sdk_tools)['instance']
            listing = await sdk_server.request_handlers[mcp_types.ListToolsRequest](
                mcp_types.ListToolsRequest(method='tools/list'))
            sdk_schemas = {tool.name: tool.model_dump(mode='json', by_alias=True, exclude_none=True)
                           for tool in listing.root.tools}
        result = []
        names = set()
        for tool in tools:
            if isinstance(tool, mcp_types.Tool):
                schema = tool.model_dump(mode='json', by_alias=True, exclude_none=True)
            elif isinstance(tool, dict):
                schema = tool
            else:
                schema = sdk_schemas[tool.name]
            schema = mcp_types.Tool.model_validate(schema).model_dump(mode='json', by_alias=True, exclude_none=True)
            if schema['name'] in names:
                raise BridgeError('Parent tool list contains duplicate names')
            names.add(schema['name'])
            result.append(schema)
        return result

    @staticmethod
    def _response(value, status_code=200):
        if len(json.dumps(value).encode()) > _MAX_RESPONSE:
            return JSONResponse(_error('Parent result exceeds the bridge limit; outcome may already be completed'), status_code=502)
        return JSONResponse(value, status_code=status_code, headers={'Cache-Control': 'no-store'})

    async def _listing(self, request):
        if not self._authorized(request):
            return self._response(_error('Private parent bridge authorization required'), 403)
        try:
            tools = await asyncio.wait_for(self._schemas(), min(self.timeout, 10))
            return self._response({'tools': tools, 'session_id': self.session_id})
        except Exception:
            return self._response(_error('Parent tool list unavailable'), 503)

    async def _calling(self, request):
        if not self._authorized(request):
            return self._response(_error('Private parent bridge authorization required'), 403)
        if self._interrupting:
            return self._response(_error('Parent is interrupting; request was not accepted'), 409)
        try:
            raw = bytearray()
            async with asyncio.timeout(10):
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > _MAX_REQUEST:
                        return self._response(_error('Bridge request exceeds size limit'), 413)
            body = json.loads(raw)
            if (not isinstance(body, dict) or set(body) != {'request_id', 'name', 'arguments'}
                    or not isinstance(body['name'], str) or not isinstance(body['arguments'], dict)):
                raise ValueError()
            request_id = str(uuid.UUID(body['request_id']))
        except (ValueError, TypeError, KeyError, asyncio.TimeoutError):
            return self._response(_error('Invalid bridge request'), 400)
        if self._closed or self._interrupting:
            return self._response(_error('Parent is stopping; request was not accepted'), 409)
        if request_id in self._requests:
            return self._response(_error('Request already accepted (' + self._requests[request_id] + '); it will not be replayed'), 409)
        if len(self._pending) >= self._max_pending:
            return self._response(_error('Parent bridge call queue is full; request was not accepted'), 429)
        if len(self._requests) >= 10000:
            return self._response(_error('Bridge request ledger is full; start a new parent session'), 429)
        # No await between lookup and reservation. This stays true when two HTTP
        # requests with the same ID arrive concurrently on this event loop.
        self._requests[request_id] = 'pending'
        task = asyncio.create_task(self._invoke(body['name'], body['arguments'], request_id))
        self._pending.add(task)
        task.add_done_callback(self._finished)
        try:
            done, _ = await asyncio.wait({task}, timeout=self.timeout)
            if not done:
                self._requests[request_id] = 'uncertain after timeout'
                task.cancel()
                return self._response(_error('Parent tool timed out; effects may have completed. Inspect state before any new call.'), 504)
            if task.cancelled():
                return self._response(_error('Parent tool was interrupted; effects may have completed. Inspect parent state before a new call.'), 409)
            return self._response(task.result())
        except asyncio.CancelledError:
            self._requests[request_id] = 'uncertain after disconnect'
            task.cancel()
            raise

    def _finished(self, task):
        self._pending.discard(task)
        if not task.cancelled():
            task.exception()  # consume exceptions after HTTP cancellation/close

    async def _invoke(self, name, arguments, request_id):
        try:
            async with self._slots:
                schemas = await self._schemas()
                if name not in {schema['name'] for schema in schemas}:
                    self._requests[request_id] = 'denied: tool not active'
                    return _error('Tool is not active in the parent session')
                # Parent callback is the authorization boundary. Do not use the
                # SdkMcpTool.handler from the listing, which could bypass policy.
                pending = self._call_tool(name, arguments)
                if not inspect.isawaitable(pending):
                    raise BridgeError('Parent call callback must be async')
                result = _result(await pending)
                self._requests[request_id] = 'completed'
                return result
        except asyncio.CancelledError:
            self._requests[request_id] = 'uncertain after cancellation'
            raise
        except Exception:
            self._requests[request_id] = 'failed; effects uncertain'
            return _error('Parent callback failed; inspect session state before retrying')

    async def cancel_pending(self):
        """Interrupt accepted calls, retain their ledger, and keep the bridge usable.

        The parent invokes this before stopping its CLI subprocess. Callbacks must
        cooperate with cancellation and own their descendants. Non-cooperative
        callbacks stay tracked (and occupy their concurrency slot) after the
        bounded wait; their effects remain uncertain rather than being replayed.
        """
        self._interrupting += 1
        pending = set(self._pending)
        try:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait(pending, timeout=2)
        finally:
            self._interrupting -= 1

    async def close(self):
        self._closed = True
        self._token = ''
        self._temp.cleanup()
        await self.cancel_pending()


def read_discovery(path, expected_session=None):
    """FD-relative read: private parent and file, no final-component symlinks."""
    try:
        path = Path(path)
        if not path.is_absolute():
            raise ValueError()
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError()
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_size > 8192):
                raise ValueError()
            body = source.read(8193)
        data = json.loads(body)
        if (len(body) > 8192 or data['version'] != 1 or not isinstance(data['token'], str)
                or not 32 <= len(data['token']) <= 256 or not isinstance(data['session_id'], str)
                or not data['session_id'] or not 0 < float(data['call_timeout_s']) <= 3600
                or (expected_session is not None and data['session_id'] != expected_session)):
            raise ValueError()
        data['url'] = _base_url(data['url'])
        return data
    except (OSError, ValueError, KeyError, TypeError, BridgeError):
        raise BridgeError('Parent bridge discovery is missing, unsafe, stale, or belongs to another session') from None


class ParentBridgeClient:
    """The stdio child proxy. No environment proxy, redirects, retries or fallback."""
    def __init__(self, discovery_path, *, expected_session=None, transport=None):
        data = read_discovery(discovery_path, expected_session)
        self.session_id = data['session_id']
        self.timeout = float(data['call_timeout_s']) + 2
        self._http = httpx.AsyncClient(base_url=data['url'], trust_env=False, follow_redirects=False,
            transport=transport, timeout=self.timeout,
            headers={'x-dream-bridge-token': data['token'], 'x-dream-session-id': self.session_id})

    @classmethod
    def from_environment(cls):
        if DISCOVERY_ENV not in os.environ:
            return None
        return cls(os.environ[DISCOVERY_ENV], expected_session=os.environ.get('DREAM_SESSION_ID'))

    async def _request(self, method, path, body=None):
        try:
            async with asyncio.timeout(self.timeout):
                async with self._http.stream(method, path, json=body) as response:
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > _MAX_RESPONSE:
                            raise BridgeError('Parent response exceeds size limit; outcome uncertain')
                    if response.status_code != 200:
                        raise BridgeError(f'Parent bridge HTTP {response.status_code}; request was not replayed; inspect parent state')
                    return json.loads(raw)
        except (httpx.HTTPError, asyncio.TimeoutError, ValueError):
            raise BridgeError('Parent bridge unavailable; request was not replayed and outcome may be uncertain') from None

    async def list_tools(self):
        data = await self._request('GET', '/api/mcp/tools')
        if data.get('session_id') != self.session_id or not isinstance(data.get('tools'), list):
            raise BridgeError('Parent tool list belongs to a different session or is malformed')
        return [mcp_types.Tool.model_validate(tool) for tool in data['tools']]

    async def call_tool(self, name, arguments):
        body = {'request_id': str(uuid.uuid4()), 'name': name, 'arguments': arguments or {}}
        if len(json.dumps(body).encode()) > _MAX_REQUEST:
            raise BridgeError('Tool arguments exceed bridge limit')
        return mcp_types.CallToolResult.model_validate(await self._request('POST', '/api/mcp/call', body))

    async def close(self):
        await self._http.aclose()
