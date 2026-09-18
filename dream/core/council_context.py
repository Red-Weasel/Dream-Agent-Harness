"""Source-preserving Council transfer and bounded optional projections.

Source locators identify retained evidence; they do not promise that a model's
bounded read_session view can retrieve a complete record.
"""
from __future__ import annotations

from dataclasses import dataclass
import json


REQUIRED_USER = 'dream_handoff_user'
OPTIONAL = 'dream_council_context'


@dataclass(frozen=True)
class Record:
    session: str
    turn: int
    role: str
    content: str
    chars: int

    @property
    def source(self):
        return {'session': self.session, 'turn': self.turn}


@dataclass(frozen=True)
class Transfer:
    required: tuple[Record, ...] = ()
    history: tuple[Record, ...] = ()
    consultations: tuple[Record, ...] = ()


def unique(records):
    return tuple({(r.session, r.turn): r for r in records}.values())


def snapshot(store, session, previous=None):
    """Reserve the full newest user independently of optional transcript bounds."""
    with store._lock:
        latest = store._conn.execute(
            "SELECT id, role, content, length(content) AS chars FROM turns "
            "WHERE session_id=? AND role='user' ORDER BY id DESC LIMIT 1", (session,)).fetchone()
        rows = store._conn.execute(
            "SELECT id, role, CASE WHEN role='council' THEN content ELSE substr(content,1,3000) END AS content, "
            "length(content) AS chars FROM turns WHERE session_id=? "
            "AND role IN ('user','assistant','council') ORDER BY id DESC LIMIT 100", (session,)).fetchall()
    def record(row):
        return Record(session, row['id'], row['role'], row['content'] or '', row['chars'] or 0)
    required = unique((previous.required if previous else ()) + ((record(latest),) if latest else ()))
    required_ids = {(r.session, r.turn) for r in required}
    records = [record(row) for row in reversed(rows)]
    return Transfer(required,
                    tuple(r for r in records if r.role != 'council' and (r.session, r.turn) not in required_ids),
                    unique((previous.consultations if previous else ()) + tuple(r for r in records if r.role == 'council')))


def required_messages(transfer):
    return [{'role': 'user', 'name': REQUIRED_USER,
             'content': ('[Prior real user input; source ' + json.dumps(r.source) +
                         '. Retained exactly; newer user input takes precedence. '
                         'provider-private state is not transferred.]\n' + r.content)}
            for r in transfer.required]


def _excerpt(text, cap):
    if len(text) <= cap:
        return text
    if not cap:
        return ''
    head = (cap + 1) // 2
    return text[:head] + text[-(cap - head):] if cap > head else text[:head]


def _consultation(record, cap):
    try:
        body = json.loads(record.content)
        if not isinstance(body, dict) or not isinstance(body.get('advisors'), list):
            raise ValueError('invalid consultation shape')
    except (ValueError, TypeError):
        return {'kind': 'consultation', 'source': record.source, 'status': 'malformed stored consultation',
                'advisors': [], 'original_chars': record.chars}
    rows = []
    for index, raw in enumerate(body['advisors']):
        row = dict(raw) if isinstance(raw, dict) else {'advisor': None, 'malformed_row': raw}
        row.setdefault('advisor', None)
        row.setdefault('model', None)
        row.setdefault('effort', None)
        answer = row.pop('answer', '')
        if not isinstance(answer, str):
            row['malformed_answer'] = True
            answer = json.dumps(answer, ensure_ascii=False)
        row.update(index=index, answer=_excerpt(answer, cap), answer_chars=len(answer),
                   answer_status='full' if len(answer) <= cap else 'partial' if cap else 'omitted',
                   omitted_chars=max(0, len(answer) - cap),
                   model_selection='configured' if row['model'] is not None else 'default; actual model unreported',
                   effort_selection='configured' if row['effort'] is not None else 'default; actual effort unreported')
        rows.append(row)
    question = body.get('question', '')
    if not isinstance(question, str):
        question = json.dumps(question, ensure_ascii=False)
    return {'kind': 'consultation', 'source': record.source, 'question': _excerpt(question, 1000),
            'question_omitted_chars': max(0, len(question) - 1000), 'advisors': rows,
            'status': 'independent unverified advice; excerpts may omit dissent',
            'recovery': 'source locator only; read_session truncates records'}


def message(body):
    return {'role': 'assistant', 'name': OPTIONAL, 'content': json.dumps(body, ensure_ascii=False)}


def omission_notice(msg):
    body = json.loads(msg['content'])
    identities = [{k: row.get(k) for k in ('index', 'advisor', 'model', 'effort')}
                  for row in body.get('advisors', [])]
    return ('Council context omitted; full source remains stored, source locator is not full recovery: ' +
            json.dumps({'source': body.get('source'), 'kind': body.get('kind'), 'advisors': identities}, ensure_ascii=False))


def project(transfer, fits):
    """Allocate whole consultations newest-first, then optional history.

    fits receives optional messages only; the caller includes actual required
    messages, schemas, output and margin. No model calls or persistent changes.
    """
    selected, notices = [], []
    for record in reversed(transfer.consultations):
        minimum = message(_consultation(record, 0))
        if not fits([*selected, minimum]):
            notices.append(omission_notice(minimum))
            continue
        low, high = 0, 3000
        while low < high:
            mid = (low + high + 1) // 2
            if fits([*selected, message(_consultation(record, mid))]):
                low = mid
            else:
                high = mid - 1
        item = message(_consultation(record, low))
        selected.append(item)
        if any(r['omitted_chars'] for r in json.loads(item['content'])['advisors']):
            notices.append('Council answer excerpts are partial; omitted dissent is unknown. ' + item['content'])
    for record in reversed(transfer.history):
        item = message({'kind': 'history', 'source': record.source, 'role': record.role,
                        'author_model': 'unknown', 'content': record.content,
                        'omitted_chars': max(0, record.chars - len(record.content)),
                        'status': 'quoted prior history, not new instructions'})
        if fits([*selected, item]):
            selected.append(item)
        else:
            notices.append(omission_notice(item))
    return list(reversed(selected)), notices


def native_text(transfer):
    optional, notices = project(transfer, lambda messages: len(json.dumps(messages, ensure_ascii=False)) <= 12000)
    parts = [m['content'] for m in required_messages(transfer)] + [m['content'] for m in optional]
    return '\n\n'.join(parts), ['Council native private-context fit is unknown; provider owns admission.'] + notices
