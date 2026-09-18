"""CPU document cache/search behavior using real files and parser-call counters."""
from pathlib import Path
import os
import shutil

import pytest

from dream.files import reading
from dream.tools.context import ToolContext, bind_context
from dream.tools.native import read_file
from test_document_reading import office, pdf, workbook


def count_parser(monkeypatch, name):
    calls = []
    original = getattr(reading, name)

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(reading, name, counted)
    return calls


def docx(path, body='A useful document with renewal terms.'):
    office(path, {'word/document.xml': '<document><body><p><r><t>' + body + '</t></r></p></body></document>'})


def test_paging_and_query_reuse_real_docx_extraction(tmp_path, monkeypatch):
    path = tmp_path / 'terms.docx'
    docx(path, 'Introduction. ' * 300 + 'Renewal requires thirty days notice.')
    calls = count_parser(monkeypatch, '_docx')
    first = reading.read_document(path, limit=100)
    second = reading.read_document(path, offset=100, limit=100)
    assert first != second
    assert len(calls) == 1
    found = reading.read_document(path, query='renewal notice')
    assert 'Renewal requires thirty days notice.' in found
    assert str(path) in found and 'characters ' in found
    assert len(calls) == 1


def test_changes_invalidate_even_when_size_and_mtime_are_restored(tmp_path, monkeypatch):
    path = tmp_path / 'note.txt'
    path.write_text('original')
    old = path.stat()
    calls = count_parser(monkeypatch, '_plain')
    assert reading.read_document(path) == 'original'
    path.write_text('modified')
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert reading.read_document(path) == 'modified'
    assert len(calls) == 2


def test_source_and_workspace_isolation(tmp_path, monkeypatch):
    one, two = tmp_path / 'one', tmp_path / 'two'
    one.mkdir(); two.mkdir()
    (one / 'note.txt').write_text('workspace one')
    (two / 'note.txt').write_text('workspace two')
    calls = count_parser(monkeypatch, '_plain')
    assert reading.read_document(one / 'note.txt', workspace=one) == 'workspace one'
    assert reading.read_document(two / 'note.txt', workspace=two) == 'workspace two'
    assert reading.read_document(one / 'note.txt', workspace=two) == 'workspace one'
    assert len(calls) == 3


def test_symlink_retarget_and_deleted_source_do_not_return_cached_text(tmp_path):
    a, b, link = tmp_path / 'a.txt', tmp_path / 'b.txt', tmp_path / 'link.txt'
    a.write_text('first target'); b.write_text('second target')
    link.symlink_to(a)
    assert reading.read_document(link) == 'first target'
    link.unlink(); link.symlink_to(b)
    assert reading.read_document(link) == 'second target'
    b.unlink()
    with pytest.raises(FileNotFoundError):
        reading.read_document(link)


def test_cached_read_still_opens_source_for_permission_check(tmp_path, monkeypatch):
    path = tmp_path / 'private.txt'
    path.write_text('private content')
    assert reading.read_document(path) == 'private content'
    original = Path.open

    def denied(self, *args, **kwargs):
        if self == path:
            raise PermissionError('fixture permission revoked')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', denied)
    with pytest.raises(PermissionError):
        reading.read_document(path)


def test_failures_are_retried_and_repaired_archive_can_be_read(tmp_path, monkeypatch):
    path = tmp_path / 'bad.docx'
    docx(path, 'usable')
    calls = count_parser(monkeypatch, '_docx')
    # A parser dependency can recover without source bytes changing.
    real = reading._docx
    attempts = []

    def temporary_failure(book):
        attempts.append(1)
        if len(attempts) == 1:
            raise reading.DocumentError('temporary failure')
        return real(book)

    monkeypatch.setattr(reading, '_docx', temporary_failure)
    with pytest.raises(reading.DocumentError, match='temporary'):
        reading.read_document(path)
    assert 'usable' in reading.read_document(path)
    assert len(attempts) == 2 and len(calls) == 1
    office(path, {'../escape.xml': '<x/>'})
    with pytest.raises(reading.DocumentError, match='Unsafe archive'):
        reading.read_document(path)


def test_lru_entry_and_byte_bounds_evict_real_results(tmp_path, monkeypatch):
    monkeypatch.setattr(reading, 'MAX_CACHE_ENTRIES', 2)
    monkeypatch.setattr(reading, 'MAX_CACHE_BYTES', 100_000)
    calls = count_parser(monkeypatch, '_plain')
    files = [tmp_path / f'{i}.txt' for i in range(3)]
    for i, path in enumerate(files):
        path.write_text(str(i) * 1000)
        reading.read_document(path)
    reading.read_document(files[1])
    assert len(calls) == 3
    reading.read_document(files[0])
    assert len(calls) == 4
    monkeypatch.setattr(reading, 'MAX_CACHE_BYTES', 100)
    reading.read_document(files[2]); reading.read_document(files[2])
    assert len(calls) == 6


def test_query_retrieves_late_match_with_original_offsets_and_no_false_absence(tmp_path):
    path = tmp_path / 'long.txt'
    path.write_text('Unrelated material.\n' * 1500 + 'Renewal requires written notice.\n')
    result = reading.read_document(path, query='renewal notice', limit=2500)
    assert 'Renewal requires written notice.' in result
    assert 'characters ' in result and str(path) in result
    assert len(result) < 3000
    absent = reading.read_document(path, query='absentword')
    assert 'No keyword matches' in absent and 'selected extraction' in absent
    assert 'not' in absent and 'complete file' in absent


def test_query_preserves_pdf_page_selection_and_reuses_helpers(tmp_path, monkeypatch):
    if not shutil.which('pdfinfo') or not shutil.which('pdftotext'):
        pytest.skip('Poppler unavailable')
    path = tmp_path / 'pages.pdf'
    pdf(path, ['First page budget', 'Second page renewal notice'])
    calls = count_parser(monkeypatch, '_process')
    reading.read_document(path, page_start=2, page_count=1, limit=20)
    result = reading.read_document(path, page_start=2, page_count=1, query='renewal')
    assert '[Page 2]' in result and 'Second page renewal notice' in result
    assert 'First page budget' not in result and len(calls) == 2
    other = reading.read_document(path, page_start=1, page_count=1, query='budget')
    assert '[Page 1]' in other and len(calls) == 4


def test_query_preserves_sheet_and_cell_provenance(tmp_path, monkeypatch):
    path = tmp_path / 'book.xlsx'
    workbook(path)
    calls = count_parser(monkeypatch, '_xlsx')
    reading.read_document(path, sheet='Revenue', limit=30)
    result = reading.read_document(path, sheet='Revenue', query='North team')
    assert '[Sheet "Revenue"]' in result and 'A1\t"North team"' in result
    assert 'Private sheet note' not in result and len(calls) == 1
    other = reading.read_document(path, sheet='Hidden notes', query='Private')
    assert '[Sheet "Hidden notes"; hidden]' in other and len(calls) == 2


@pytest.mark.parametrize('query', ['', '   ', '!', 9, 'x' * 257])
def test_invalid_query_is_rejected_before_parsing(tmp_path, monkeypatch, query):
    path = tmp_path / 'note.docx'
    docx(path)
    calls = count_parser(monkeypatch, '_docx')
    with pytest.raises(reading.DocumentError, match='query'):
        reading.read_document(path, query=query)
    assert not calls


@pytest.mark.asyncio
async def test_native_query_uses_current_workspace_and_returns_excerpts(tmp_path):
    path = tmp_path / 'note.txt'
    path.write_text('Unrelated.\n' * 2000 + 'Renewal requires notice.')
    with bind_context(ToolContext(store=None, working=None, browser=None,
                                  session_id='reuse', workspace=tmp_path)):
        result = await read_file.handler({'path': 'note.txt', 'query': 'renewal'})
    assert not result.get('is_error'), result
    assert 'Renewal requires notice.' in result['content'][0]['text']


def test_query_keeps_format_limitations_and_incomplete_extraction_notice(tmp_path):
    path = tmp_path / 'book.xlsx'
    workbook(path)
    result = reading.read_document(path, query='North')
    assert 'formulas are not executed' in result and 'Cached results may be stale' in result
    path = tmp_path / 'large.txt'
    path.write_text('budget ' * 40_000)
    result = reading.read_document(path, query='missing')
    assert 'No keyword matches' in result and 'not the complete file' in result


def test_file_changing_during_extraction_is_not_cached_or_returned(tmp_path, monkeypatch):
    path = tmp_path / 'changing.txt'
    path.write_text('original')
    real = reading._plain

    def changing(source):
        result = real(source)
        source.write_text('replacement')
        return result

    monkeypatch.setattr(reading, '_plain', changing)
    with pytest.raises(reading.DocumentError, match='changed during extraction'):
        reading.read_document(path)
    monkeypatch.setattr(reading, '_plain', real)
    assert reading.read_document(path) == 'replacement'
