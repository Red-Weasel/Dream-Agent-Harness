"""Explicit host-prepared Responses advice; no automatic accounts or CLI fallback.

Disclosure approval covers a count POST and one inference POST. Provider-count
receipts are private journal metadata, not model assertions or local estimates.
No live provider qualification follows from MockTransport fixture success.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx

from .frontier_help import AdviceChunk, ActualUsage, FrontierHelp, TransportQualification, _hash
from . import context_inspection
from ..tools.context import _BOUND


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _positive(value, limit):
    if type(value) is not int or not 0 < value <= limit:
        raise ValueError('Invalid bounded provider input count')
    return value


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate provider JSON key')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('Non-finite provider JSON number')


def _observe(attempt, method, *args):
    # Inspection is observational: a journal failure cannot alter admission,
    # retry a disclosure or prevent response/client cleanup.
    try:
        getattr(attempt, method)(*args)
    except Exception:
        pass


class ResponsesHelpTransport:
    """Fixed endpoint, no retries, synchronous qualification after async count.

    ``io_transport`` accepts only an exact MockTransport for offline fixtures.
    Ordinary construction reads no credentials or accounts and sends nothing.
    The operator explicitly supplies the credential and approved packet.
    """
    def __init__(self, *, api_key, io_transport=None):
        if (not isinstance(api_key, str) or not api_key or len(api_key) > 16384
                or any(ord(c) < 33 or ord(c) > 126 for c in api_key)):
            raise ValueError('An explicit bounded API credential is required')
        if io_transport is not None and type(io_transport) is not httpx.MockTransport:
            raise ValueError('Only an exact offline MockTransport may replace HTTP')
        self.__key = api_key
        self.__io = io_transport
        self.__version = self._source_version()
        self.__prepared = None
        self.__claimed = False
        self.__failed = False
        self.__inspection_id = uuid4().hex
        self.__inspection_meta = {}

    @staticmethod
    def _source_version():
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    def _for_task(self):
        """Host-only template clone; task journals retain count/send authority."""
        if self._source_version() != self.__version or self.__failed:
            raise PermissionError('Selected transport source or admission changed')
        return ResponsesHelpTransport(api_key=self.__key, io_transport=self.__io)

    def _binding(self, helper, request):
        if self.__failed or self._source_version() != self.__version:
            raise PermissionError('Prepared transport admission is closed or source changed')
        effective = {'model': request.route.model_id,
                     'input': request.payload.decode('utf-8'), 'tools': [],
                     'tool_choice': 'none', 'parallel_tool_calls': False,
                     'truncation': 'disabled'}
        return dict(scope=helper._scope(), route_sha256=request.route_sha256,
                    packet_sha256=request.packet_sha256,
                    input_sha256=hashlib.sha256(_encode(effective)).hexdigest(),
                    transport_version=self.__version, model_id=request.route.model_id), effective

    def _saved(self, coordinator, binding, route):
        saved = coordinator.journal.state.get('responses_help_preflight')
        if saved is None:
            return None
        if (type(saved) is not dict
                or set(saved) != {'version', 'binding', 'status', 'input_tokens', 'reason'}
                or type(saved['version']) is not int or saved['version'] != 1
                or _encode(saved['binding']) != _encode(binding)
                or saved['status'] not in {'intent', 'counted', 'unknown'}):
            raise ValueError('Prepared count scope, input or source changed')
        if saved['status'] == 'counted':
            _positive(saved['input_tokens'], route.input_token_limit)
            if saved['reason'] is not None:
                raise ValueError('Invalid settled count receipt')
        elif (saved['input_tokens'] is not None
              or saved['reason'] not in ((None,) if saved['status'] == 'intent'
                                        else ('cancelled', 'count_failed'))):
            raise ValueError('Invalid unsettled count receipt')
        return deepcopy(saved)

    def _record(self, coordinator, status, binding, *, tokens=None, reason=None):
        coordinator.seal_actions()
        try:
            state = dict(version=1, binding=binding, status=status, input_tokens=tokens, reason=reason)
            sequence = coordinator.journal.seq
            coordinator.journal.record('responses_help_count_' + status, incremental=True,
                                      responses_help_preflight=state)
            if (coordinator.journal.seq != sequence + 1
                    or _encode(coordinator.journal.state.get('responses_help_preflight')) != _encode(state)):
                raise ValueError('Provider-count checkpoint did not commit its exact projection')
        except BaseException:
            self.__failed = True
            raise

    async def _post(self, path, body, *, timeout, limit):
        attempt = context_inspection.NO_ATTEMPT
        try:
            bound = _BOUND.get()
            attempt = context_inspection.capture_responses(body, self.__inspection_meta,
                session_id=getattr(bound, 'session_id', None), private_id=self.__inspection_id,
                purpose='input_tokens' if path == 'responses/input_tokens' else 'inference')
        except Exception:
            pass
        try:
            result = await self._post_raw(path, body, timeout=timeout, limit=limit, attempt=attempt)
        except (httpx.HTTPError, OSError):
            # Exception messages may echo headers or provider-controlled data.
            _observe(attempt, 'unwound', RuntimeError())
            raise RuntimeError('Selected provider transport failed') from None
        except BaseException as exc:
            _observe(attempt, 'unwound', exc)
            raise
        return result, attempt

    async def _post_raw(self, path, body, *, timeout, limit, attempt):
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        deadline = time.monotonic() + timeout
        # A fresh owned client has no SDK retry, environment proxy or redirect.
        io = self.__io if self.__io is not None else httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        async with httpx.AsyncClient(transport=io, trust_env=False, follow_redirects=False,
                                    timeout=timeout) as client:
            async with client.stream('POST', 'https://api.openai.com/v1/' + path,
                    headers={'authorization': 'Bearer ' + self.__key,
                             'content-type': 'application/json', 'accept-encoding': 'identity'},
                    content=_encode(body)) as response:
                _observe(attempt, 'response', response.status_code)
                if response.status_code != 200:
                    raise ValueError('Selected provider request was refused')
                if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                    raise ValueError('Unexpected encoded provider response')
                pieces, size, frames = [], 0, 0
                async for piece in response.aiter_raw():
                    size += len(piece)
                    frames += 1
                    if size > limit or frames > 8192:
                        raise ValueError('Provider wire response exceeds its bounded allowance')
                    pieces.append(piece)
                try:
                    result = json.loads(b''.join(pieces).decode('utf-8'),
                                        object_pairs_hook=_pairs, parse_constant=_constant)
                except (ValueError, UnicodeError):
                    raise ValueError('Invalid provider response JSON') from None
                if type(result) is not dict:
                    raise ValueError('Expected a provider response object')
        # Both response and client cleanup settle before any receipt or output.
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        if time.monotonic() >= deadline:
            raise TimeoutError('Provider response cleanup exceeded its deadline')
        return result

    async def prepare(self, coordinator, route, packet):
        """One count disclosure intent; unknown preparation is never resent."""
        helper = FrontierHelp(coordinator)
        request = helper._request(route, packet)
        with helper.lock:
            binding, effective = self._binding(helper, request)
            saved = self._saved(coordinator, binding, route)
            existing_help = helper._saved()
            if self.__claimed or existing_help and existing_help['status'] != 'reserved':
                raise PermissionError('An inference intent cannot be prepared or resent')
            if saved is not None:
                if saved['status'] != 'counted':
                    raise PermissionError('Unsettled count intent requires explicit reconciliation')
                self.__prepared = (coordinator, request, deepcopy(binding), effective, saved['input_tokens'])
                return
            self._record(coordinator, 'intent', binding)
        attempt = context_inspection.NO_ATTEMPT
        try:
            async with asyncio.timeout(route.timeout_seconds):
                result, attempt = await self._post('responses/input_tokens', effective,
                                          timeout=route.timeout_seconds, limit=4096)
            if set(result) != {'object', 'input_tokens'} or result['object'] != 'response.input_tokens':
                raise ValueError('Invalid provider-count response')
            tokens = _positive(result['input_tokens'], route.input_token_limit)
            with helper.lock:
                current, _ = self._binding(helper, request)
                if current != binding:
                    raise ValueError('Managed scope changed during count preparation')
                self._record(coordinator, 'counted', binding, tokens=tokens)
                self.__prepared = (coordinator, request, deepcopy(binding), effective, tokens)
            _observe(attempt, 'responses_body', result)
            _observe(attempt, 'unwound', None)
        except BaseException as exc:
            _observe(attempt, 'unwound', exc)
            self.__failed = True
            try:
                self._record(coordinator, 'unknown', binding,
                             reason='cancelled' if isinstance(exc, asyncio.CancelledError) else 'count_failed')
            except BaseException:
                exc.add_note('Count-outcome persistence failed; admission remains closed')
            raise

    def _qualified(self, request):
        if self.__prepared is None or self.__failed:
            raise PermissionError('No settled qualified provider input')
        coordinator, expected, binding, effective, tokens = self.__prepared
        if request != expected:
            raise ValueError('Prepared input changed before inference')
        helper = FrontierHelp(coordinator)
        current, _ = self._binding(helper, request)
        saved = self._saved(coordinator, current, request.route)
        if current != binding or saved is None or saved['status'] != 'counted' or saved['input_tokens'] != tokens:
            raise ValueError('Prepared count receipt changed before inference')
        return coordinator, effective, tokens

    def qualify(self, request):
        _, _, tokens = self._qualified(request)
        return TransportQualification(request.route_sha256, request.packet_sha256, self.__version,
            input_tokens=tokens, output_token_ceiling=request.output_token_limit,
            output_byte_ceiling=request.output_byte_limit, single_request=True,
            retries=False, continuations=False, tools=False, fallback=False,
            actual_token_count=True, cancel_safe=True)

    async def stream_once(self, request):
        coordinator, effective, tokens = self._qualified(request)
        if self.__claimed:
            raise PermissionError('The one inference has already been claimed')
        saved = FrontierHelp(coordinator)._saved()
        if saved is None or saved['status'] != 'intent':
            raise PermissionError('A durable exact inference intent is required')
        if (saved['binding']['packet_sha256'] != request.packet_sha256
                or saved['binding']['route_sha256'] != request.route_sha256
                or saved['qualification']['transport_version'] != self.__version):
            raise ValueError('Inference intent changed its prepared input')
        self.__claimed = True
        body = {**effective, 'max_output_tokens': request.output_token_limit,
                'store': False, 'stream': False}
        result, attempt = await self._post('responses', body, timeout=request.route.timeout_seconds, limit=131072)
        try:
            self._qualified(request)
        except BaseException as exc:
            _observe(attempt, 'unwound', exc)
            raise
        try:
            _observe(attempt, 'responses_body', result)
            _observe(attempt, 'unwound', None)
            if (result.get('object') != 'response' or result.get('status') != 'completed'
                    or result.get('model') != request.route.model_id
                    or result.get('tools', []) != [] or result.get('previous_response_id') is not None):
                raise ValueError('Provider returned an incomplete or changed advisory response')
            usage = result.get('usage')
            if type(usage) is not dict or usage.get('input_tokens') != tokens or type(usage.get('input_tokens')) is not int:
                raise ValueError('Actual provider input usage differs from its counted input')
            output_tokens = usage.get('output_tokens')
            if type(output_tokens) is not int or not 0 <= output_tokens <= request.output_token_limit:
                raise ValueError('Actual provider output exceeds its token ceiling')
            output = result.get('output')
            if type(output) is not list or len(output) > 32:
                raise ValueError('Invalid bounded advisory output')
            texts, size = [], 0
            for item in output:
                if type(item) is not dict:
                    raise ValueError('Unexpected advisory output item')
                if item.get('type') == 'reasoning' and item.get('summary') == []:
                    continue
                if (item.get('type') != 'message' or item.get('role') != 'assistant'
                        or item.get('status') != 'completed' or type(item.get('content')) is not list):
                    raise ValueError('Tools and non-advisory output are forbidden')
                for block in item['content']:
                    if type(block) is not dict or block.get('type') != 'output_text' or type(block.get('text')) is not str:
                        raise ValueError('Unexpected advisory content block')
                    text = block['text']
                    size += len(text.encode('utf-8'))
                    if size > request.output_byte_limit:
                        raise ValueError('Advisory output exceeds its byte ceiling')
                    texts.append(text)
            if not texts or not ''.join(texts).strip():
                raise ValueError('No usable advisory text was returned')
            # Bound every emitted UTF-8 frame, not merely its Python character count.
            chunk, size = [], 0
            for char in ''.join(texts):
                width = len(char.encode('utf-8'))
                if size + width > 4096:
                    yield AdviceChunk(''.join(chunk))
                    chunk, size = [], 0
                chunk.append(char)
                size += width
            if chunk:
                yield AdviceChunk(''.join(chunk))
            yield ActualUsage(tokens, output_tokens)
        except BaseException as exc:
            _observe(attempt, 'unwound', exc)
            raise
