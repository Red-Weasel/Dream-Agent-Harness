"""Exact HTTP worker snapshots; capture is not an execution or restart grant.

Messages are immutable, content-addressed artifacts so unchanged history is not
copied into every round snapshot. WorkerJournal owns admission and settlement;
the backend must still reconcile effects and acquire current parent authority.
"""
from __future__ import annotations

import hashlib

from .parent_continuation import _plain
from .worker_checkpoints import WorkerCheckpointError, _decode, _encode

MAX_MESSAGES = 8192


class WorkerSnapshots:
    def __init__(self, journal):
        self.journal = journal
        self._messages = {}

    def pack(self, snapshot):
        """Save exact messages once; leave all other host state in the index."""
        value = _plain(snapshot)
        if (type(value) is not dict or value.get('kind') != 'dream.worker_http_snapshot'
                or type(value.get('version')) is not int or value['version'] != 1):
            raise WorkerCheckpointError('invalid_snapshot')
        for field in ('messages', 'original_history'):
            messages = value.get(field)
            if type(messages) is not list or len(messages) > MAX_MESSAGES:
                raise WorkerCheckpointError('invalid_snapshot')
            references = []
            for message in messages:
                if type(message) is not dict:
                    raise WorkerCheckpointError('invalid_snapshot')
                raw = _encode(message)
                digest = hashlib.sha256(raw).hexdigest()
                receipt = self._messages.get(digest)
                if receipt is None:
                    receipt = self.journal.store.save(raw.decode('utf-8'),
                        'worker-message:' + digest).metadata()
                    self._messages[digest] = receipt
                references.append(dict(receipt))
            value[field] = references
        return value

    def unpack(self, snapshot=None):
        """Host-only exact read; never settle debt, restore a lease or run tools."""
        value = _plain(self.journal.inspect()['snapshot'] if snapshot is None else snapshot)
        if (type(value) is not dict or value.get('kind') != 'dream.worker_http_snapshot'
                or type(value.get('version')) is not int or value['version'] != 1):
            raise WorkerCheckpointError('invalid_snapshot')
        for field in ('messages', 'original_history'):
            references = value.get(field)
            if type(references) is not list or len(references) > MAX_MESSAGES:
                raise WorkerCheckpointError('invalid_snapshot')
            messages = []
            for receipt in references:
                if (type(receipt) is not dict
                        or self.journal.store._receipt(receipt.get('handle')).metadata() != receipt):
                    raise WorkerCheckpointError('invalid_snapshot')
                raw = self.journal.store.load_original(receipt['handle'])
                message = _decode(raw)
                if type(message) is not dict:
                    raise WorkerCheckpointError('invalid_snapshot')
                messages.append(message)
            value[field] = messages
        return value
