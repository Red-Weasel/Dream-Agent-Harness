"""Extract text and metadata without executing documents or following references.

Paths are resolved by the caller under Dream's existing file permission policy.
Office relationships are ZIP member references, never filesystem/network paths.
"""
from __future__ import annotations

import bz2
import codecs
from collections import OrderedDict
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import selectors
import shutil
import stat
import struct
import sys
import threading
import subprocess
import tarfile
import time
import warnings
import xml.etree.ElementTree as ET
import zipfile

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_EXTRACT_CHARS = 200_000
MAX_ZIP_MEMBERS = 2_000
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_XML_BYTES = 8 * 1024 * 1024
MAX_CELLS = 10_000
MAX_SHEETS = 50
MAX_SLIDES = 200
MAX_PIXELS = 25_000_000
PDF_TIMEOUT = 15
MAX_CACHE_ENTRIES = 16
MAX_CACHE_BYTES = 8 * 1024 * 1024
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
_OFFICE = {'.docx', '.xlsx', '.pptx'}
_IMAGES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tif', '.tiff', '.ico'}
_MEDIA = {'.mp3', '.mp4', '.wav', '.flac', '.ogg', '.m4a', '.webm', '.mov', '.avi'}


class DocumentError(ValueError):
    """A user-actionable refusal or extraction failure."""


def _integer(value, name, lower, upper):
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise DocumentError(f'{name} must be an integer from {lower} to {upper}.')
    return value


class _Text:
    def __init__(self):
        self.parts = []
        self.size = 0
        self.truncated = False

    def add(self, value):
        value = str(value) + '\n'
        remaining = MAX_EXTRACT_CHARS - self.size
        if len(value) > remaining:
            self.truncated = True
        self.parts.append(value[:remaining])
        self.size += min(remaining, len(value))

    @property
    def full(self):
        return self.size >= MAX_EXTRACT_CHARS

    def result(self):
        return ''.join(self.parts).rstrip('\n'), self.truncated


def _name(tag):
    return tag.rsplit('}', 1)[-1]


def _attr(element, name):
    return next((value for key, value in element.attrib.items() if key.endswith('}' + name)), None)


def _children(element, name):
    return [child for child in element if _name(child.tag) == name]


def _texts(element):
    return ''.join(node.text or '' for node in element.iter() if _name(node.tag) == 't')


def _member_name(name):
    if (not name or '\\' in name or '\0' in name or name.startswith('/')
            or '..' in PurePosixPath(name).parts or re.match(r'^[A-Za-z]:', name)):
        raise DocumentError(f'Unsafe archive member path: {name!r}. No members were extracted.')
    return name


def _zip_precheck(source):
    """Bound entries before ZipFile allocates its eager central-directory index.

    Fixed field offsets follow PKWARE APPNOTE 6.3.10 sections 4.3.12/4.3.16.
    ZIP64, split archives and extended/signed central directories are refused.
    """
    source.seek(0, 2)
    size = source.tell()
    if size > MAX_FILE_BYTES:
        raise DocumentError('ZIP exceeds the 32 MiB reading limit.')
    start = max(0, size - 65557)  # 22-byte EOCD plus the 65535-byte comment.
    source.seek(start)
    tail = source.read(65557)
    at = tail.rfind(b'PK\x05\x06')
    if at < 0 or len(tail) - at < 22:
        raise DocumentError('Corrupt ZIP: missing end-of-directory record.')
    _, disk, cd_disk, local_count, count, directory_size, offset, comment = struct.unpack_from('<4s4H2LH', tail, at)
    if count == 65535 or local_count == 65535 or directory_size == 0xffffffff or offset == 0xffffffff:
        raise DocumentError('ZIP64 archives are not supported. Export a smaller standard ZIP.')
    if disk or cd_disk or local_count != count:
        raise DocumentError('Split or inconsistent ZIP archives are not supported.')
    if count > MAX_ZIP_MEMBERS:
        raise DocumentError(f'Archive exceeds {MAX_ZIP_MEMBERS} members before ZIP indexing.')
    end = start + at
    if end + 22 + comment != size or offset + directory_size != end:
        raise DocumentError('Corrupt or extended ZIP directory layout; export a standard ZIP copy.')
    source.seek(offset)
    actual = 0
    while source.tell() < end:
        header = source.read(min(46, end - source.tell()))
        if len(header) != 46 or header[:4] != b'PK\x01\x02':
            raise DocumentError('Corrupt or unsupported ZIP central-directory header.')
        actual += 1
        if actual > MAX_ZIP_MEMBERS:
            raise DocumentError(f'Archive exceeds {MAX_ZIP_MEMBERS} actual members before ZIP indexing.')
        if (0xffffffff in struct.unpack_from('<LL', header, 20)
                or struct.unpack_from('<L', header, 42)[0] == 0xffffffff):
            raise DocumentError('ZIP64 members are not supported. Export a smaller standard ZIP.')
        lengths = struct.unpack_from('<HHH', header, 28)
        following = source.tell() + sum(lengths)
        if following > end:
            raise DocumentError('Corrupt ZIP central-directory member length.')
        source.seek(following)
    if actual != count:
        raise DocumentError('ZIP declares an inconsistent number of members.')
    source.seek(0)


class _Office:
    def __init__(self, path):
        self.source = path.open('rb')
        self.archive = None
        try:
            _zip_precheck(self.source)
            self.archive = zipfile.ZipFile(self.source)
            members = self.archive.infolist()
            if len(members) > MAX_ZIP_MEMBERS:
                raise DocumentError(f'Office file exceeds {MAX_ZIP_MEMBERS} ZIP members.')
            self.names = set()
            expanded = 0
            for member in members:
                _member_name(member.orig_filename)
                if member.filename in self.names:
                    raise DocumentError('Office file contains duplicate ZIP members.')
                self.names.add(member.filename)
                if member.flag_bits & 1:
                    raise DocumentError('Encrypted Office content is not supported. Save an unencrypted copy.')
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise DocumentError('Office file contains a symbolic link; refusing to follow it.')
                expanded += member.file_size
                if expanded > MAX_EXPANDED_BYTES:
                    raise DocumentError('Office file exceeds the 64 MiB expanded-size limit.')
                if member.file_size > max(1, member.compress_size) * 200:
                    raise DocumentError('Office file exceeds the ZIP compression-ratio limit of 200:1.')
        except Exception:
            self.close()
            raise

    def close(self):
        if self.archive is not None:
            self.archive.close()
        self.source.close()

    def xml(self, name):
        _member_name(name)
        try:
            member = self.archive.getinfo(name)
        except KeyError as exc:
            raise DocumentError(f'Office file is missing required part {name!r}. It may be corrupt.') from exc
        if member.file_size > MAX_XML_BYTES:
            raise DocumentError(f'Office XML part {name!r} exceeds 8 MiB. Split the document first.')
        with self.archive.open(member) as source:
            data = source.read(MAX_XML_BYTES + 1)
        if len(data) > MAX_XML_BYTES:
            raise DocumentError('Office XML exceeds the 8 MiB limit.')
        encoding = 'utf-16' if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else 'utf-8-sig'
        try:
            xml = data.decode(encoding)
        except UnicodeError as exc:
            raise DocumentError('Office XML must use UTF-8 or BOM-marked UTF-16.') from exc
        if re.search(r'<!\s*(DOCTYPE|ENTITY)\b', xml, re.I):
            raise DocumentError('Office XML contains a DTD or entity declaration; it was not expanded.')
        try:
            return ET.fromstring(xml)
        except ET.ParseError as exc:
            raise DocumentError(f'Corrupt Office XML in {name!r}: {exc}') from exc

    def relationships(self, source):
        parent, leaf = posixpath.split(source)
        relfile = posixpath.join(parent, '_rels', leaf + '.rels')
        if relfile not in self.names:
            return {}
        relations = {}
        for node in self.xml(relfile):
            if _name(node.tag) != 'Relationship':
                continue
            key = node.get('Id')
            if node.get('TargetMode', '').lower() == 'external':
                relations[key] = None
                continue
            target = node.get('Target', '')
            if '\\' in target or '\0' in target or ':' in target or '?' in target or '#' in target:
                raise DocumentError('Office relationship has an unsupported target. No external content was read.')
            target = target.lstrip('/') if target.startswith('/') else posixpath.join(parent, target)
            target = posixpath.normpath(target)
            _member_name(target)
            relations[key] = target
        return relations


def _paragraph(node):
    text = []
    for child in node.iter():
        tag = _name(child.tag)
        if tag == 't':
            text.append(child.text or '')
        elif tag == 'tab':
            text.append('\t')
        elif tag in {'br', 'cr'}:
            text.append('\n')
    return ''.join(text)


def _word_blocks(root, output):
    for child in root:
        if output.full:
            output.truncated = True
            return
        tag = _name(child.tag)
        if tag == 'p':
            output.add(_paragraph(child))
        elif tag == 'tbl':
            for row in _children(child, 'tr'):
                output.add(' | '.join(' / '.join(_paragraph(p) for p in cell.iter()
                                                if _name(p.tag) == 'p')
                                      for cell in _children(row, 'tc')))
                if output.full:
                    output.truncated = True
                    return
        else:
            _word_blocks(child, output)


def _docx(book):
    output = _Text()
    output.add('[DOCX text and tables. Layout, images, tracked deletions and embedded objects are not interpreted.]')
    document = book.xml('word/document.xml')
    if _name(document.tag) != 'document' or not any(_name(node.tag) == 'body' for node in document):
        raise DocumentError('Corrupt DOCX: expected a document body.')
    _word_blocks(document, output)
    for name in sorted(book.names):
        if output.full:
            break
        if re.fullmatch(r'word/(header\d+|footer\d+|footnotes|endnotes|comments)\.xml', name):
            output.add(f'[{name}]')
            _word_blocks(book.xml(name), output)
    return output.result()


def _xlsx(book, selected):
    workbook = book.xml('xl/workbook.xml')
    if _name(workbook.tag) != 'workbook':
        raise DocumentError('Corrupt XLSX: expected a workbook.')
    sheets = [node for node in workbook.iter() if _name(node.tag) == 'sheet']
    names = [node.get('name', '') for node in sheets]
    if selected is not None:
        sheets = [node for node in sheets if node.get('name') == selected]
        if not sheets:
            raise DocumentError('No sheet named ' + repr(selected) + '. Available sheets: ' + json.dumps(names, ensure_ascii=False)[:4000])
    relations = book.relationships('xl/workbook.xml')
    strings = []
    if 'xl/sharedStrings.xml' in book.names:
        strings = [_texts(node) for node in book.xml('xl/sharedStrings.xml') if _name(node.tag) == 'si']
    output = _Text()
    output.add('[XLSX cells. Raw values and formulas are shown; formulas are not executed. Cached results may be stale. Date/number styles, charts and images are not interpreted.]')
    count = 0
    for sheet in sheets[:MAX_SHEETS]:
        output.add('\n[Sheet ' + json.dumps(sheet.get('name', ''), ensure_ascii=False) +
                   ('; hidden' if sheet.get('state') in {'hidden', 'veryHidden'} else '') + ']')
        target = relations.get(_attr(sheet, 'id'))
        if not target:
            raise DocumentError('Workbook sheet has a missing or external relationship; no external content was read.')
        for cell in book.xml(target).iter():
            if _name(cell.tag) != 'c':
                continue
            if count >= MAX_CELLS or output.full:
                output.add(f'[Stopped at {MAX_CELLS} cells or {MAX_EXTRACT_CHARS} characters. Select a sheet or split the workbook to read more.]')
                output.truncated = True
                return output.result()
            count += 1
            kind = cell.get('t', '')
            values = _children(cell, 'v')
            value = values[0].text or '' if values else ''
            if kind == 's':
                try:
                    index = int(value)
                    if not 0 <= index < len(strings):
                        raise ValueError()
                    value = strings[index]
                except (ValueError, IndexError) as exc:
                    raise DocumentError('Workbook has an invalid shared-string index.') from exc
            elif kind == 'inlineStr':
                value = _texts(cell)
            elif kind == 'b':
                value = {'0': 'FALSE', '1': 'TRUE'}.get(value, value)
            formula = _children(cell, 'f')
            if formula:
                expression = formula[0].text
                formula_text = '=' + expression if expression else '[shared/array formula; expression unavailable here]'
                value = formula_text + (' [cached: ' + value + ']' if values and value else ' [cached value unavailable]')
            output.add((cell.get('r') or '?') + '\t' + json.dumps(value, ensure_ascii=False))
    if len(sheets) > MAX_SHEETS:
        output.add(f'[Only the first {MAX_SHEETS} sheets were read. Use sheet to select another.]')
        output.truncated = True
    return output.result()


def _pptx(book):
    source = 'ppt/presentation.xml'
    relations = book.relationships(source)
    presentation = book.xml(source)
    if _name(presentation.tag) != 'presentation':
        raise DocumentError('Corrupt PPTX: expected a presentation.')
    slides = [node for node in presentation.iter() if _name(node.tag) == 'sldId']
    output = _Text()
    output.add('[PPTX slide text and notes in presentation order. Layout, charts, images and animations are not interpreted.]')
    for index, slide in enumerate(slides[:MAX_SLIDES], 1):
        if output.full:
            output.truncated = True
            break
        target = relations.get(_attr(slide, 'id'))
        if not target:
            raise DocumentError('Presentation has a missing or external slide relationship.')
        output.add(f'\n[Slide {index}]')
        for node in book.xml(target).iter():
            if _name(node.tag) == 'p':
                output.add(_paragraph(node))
            if output.full:
                output.truncated = True
                break
        for note in book.relationships(target).values():
            if note and re.fullmatch(r'ppt/notesSlides/notesSlide\d+\.xml', note) and not output.full:
                output.add('[Speaker notes]')
                for node in book.xml(note).iter():
                    if _name(node.tag) == 'p':
                        output.add(_paragraph(node))
                    if output.full:
                        output.truncated = True
                        break
    if len(slides) > MAX_SLIDES:
        output.add(f'[Only the first {MAX_SLIDES} slides were read. Split the presentation to read more.]')
        output.truncated = True
    return output.result()


def _process(command):
    """Bound both subprocess output and wall time; no shell or document execution."""
    stdout, stderr = bytearray(), bytearray()
    deadline = time.monotonic() + PDF_TIMEOUT
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env={**os.environ, 'LC_ALL': 'C'}) as process:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, stdout)
                selector.register(process.stderr, selectors.EVENT_READ, stderr)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DocumentError('PDF extraction timed out. Request fewer pages or repair the document.')
                    for key, _ in selector.select(min(remaining, .1)):
                        block = os.read(key.fd, 65536)
                        if not block:
                            selector.unregister(key.fileobj)
                            continue
                        if len(stdout) + len(stderr) + len(block) > MAX_TEXT_BYTES:
                            raise DocumentError('PDF output exceeded 2 MiB. Request fewer pages.')
                        key.data.extend(block)
            process.wait(timeout=max(.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise DocumentError('PDF extraction timed out. Request fewer pages.') from exc
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    diagnostic = stderr.decode('utf-8', 'replace').strip()
    if process.returncode:
        if 'password' in diagnostic.lower() or 'encrypted' in diagnostic.lower():
            raise DocumentError('PDF is encrypted or password-protected. Save an unlocked copy; passwords are not accepted here.')
        raise DocumentError('Could not extract PDF text. The file may be corrupt or restricted. ' + diagnostic[:1500])
    return stdout.decode('utf-8', 'replace'), diagnostic


def _pdf(path, start, count):
    info, extractor = shutil.which('pdfinfo'), shutil.which('pdftotext')
    if not info or not extractor:
        raise DocumentError('PDF text reading requires Poppler pdfinfo and pdftotext. Install Poppler or provide a text/DOCX export.')
    metadata, diagnostic = _process([info, str(path)])
    match = re.search(r'^Pages:\s+(\d+)\s*$', metadata, re.M)
    if not match:
        raise DocumentError('PDF page count could not be determined. Repair or re-export the document.')
    pages = int(match[1])
    if start > pages:
        raise DocumentError(f'page_start={start} is past the PDF\'s {pages} pages.')
    last = min(pages, start + count - 1)
    extracted, extraction_note = _process([extractor, '-f', str(start), '-l', str(last),
                                           '-layout', '-enc', 'UTF-8', '-eol', 'unix', str(path), '-'])
    chunks = extracted.split('\f')
    if not extracted.strip():
        raise DocumentError(f'No extractable text on PDF pages {start}-{last} of {pages}. They may be scanned or blank. OCR has not been run; provide OCR text or inspect rendered pages with a vision-capable tool.')
    output = _Text()
    output.add(f'[PDF text only; pages {start}-{last} of {pages}. Images and visual layout have not been inspected.]')
    for page in range(start, last + 1):
        output.add(f'\n[Page {page}]')
        value = chunks[page-start].strip() if page-start < len(chunks) else ''
        output.add(value or '[No extractable text on this page; it may be scanned or blank. OCR has not been run.]')
    if last < pages:
        output.add(f'[More pages: call read_file with page_start={last+1}, page_count={count}.]')
    if diagnostic or extraction_note:
        output.add('[PDF parser warning: ' + (diagnostic + '\n' + extraction_note).strip()[:1500] + ']')
    return output.result()


def _image(path):
    from PIL import Image
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(path) as picture:
            width, height = picture.size
            if width * height > MAX_PIXELS:
                raise DocumentError('Image exceeds the 25-million-pixel metadata limit. Resize a copy first.')
            description = f'format: {picture.format}\nsize: {width}×{height}\nmode: {picture.mode}'
            # verify checks file integrity without claiming to inspect depicted content.
            picture.verify()
    return '[Image metadata only. No visual content or OCR was read. Use a vision-capable image tool to inspect the picture.]\n' + description, False


class _BoundedStream:
    """Cap decompressed TAR bytes, including bytes skipped between member headers."""
    def __init__(self, stream):
        self.stream = stream
        self.used = 0

    def read(self, size=-1):
        remaining = MAX_EXPANDED_BYTES - self.used
        value = self.stream.read(min(size if size >= 0 else remaining + 1, remaining + 1))
        self.used += len(value)
        if self.used > MAX_EXPANDED_BYTES:
            raise DocumentError('TAR exceeds the 64 MiB expanded-size limit.')
        return value


def _archive(path, is_zip):
    output = _Text()
    output.add('[Archive member listing only. Nothing was extracted or executed; nested archives and links were not followed.]')
    if is_zip:
        with path.open('rb') as source:
            _zip_precheck(source)
            with zipfile.ZipFile(source) as archive:
                for member in archive.infolist():
                    _member_name(member.orig_filename)
                    kind = 'directory' if member.is_dir() else 'symlink' if stat.S_ISLNK(member.external_attr >> 16) else 'file'
                    encrypted = '; encrypted, not opened' if member.flag_bits & 1 else ''
                    output.add(f'{json.dumps(member.filename, ensure_ascii=False)}\t{member.file_size} bytes\t{kind}{encrypted}')
    else:
        suffix = path.name.lower()
        opener = gzip.open if suffix.endswith(('.tar.gz', '.tgz')) else bz2.open if suffix.endswith(('.tar.bz2', '.tbz2')) else open
        with opener(path, 'rb') as source:
            with tarfile.open(fileobj=_BoundedStream(source), mode='r|') as archive:
                expanded = 0
                for index, member in enumerate(archive):
                    if index >= MAX_ZIP_MEMBERS:
                        raise DocumentError(f'Archive exceeds {MAX_ZIP_MEMBERS} members.')
                    _member_name(member.name)
                    expanded += member.size
                    if expanded > MAX_EXPANDED_BYTES:
                        raise DocumentError('TAR exceeds the 64 MiB expanded-size limit.')
                    kind = 'directory' if member.isdir() else 'link (not followed)' if member.issym() or member.islnk() else 'file' if member.isfile() else 'special entry (not opened)'
                    output.add(f'{json.dumps(member.name, ensure_ascii=False)}\t{member.size} bytes\t{kind}')
    return output.result()


def _plain(path):
    with path.open('rb') as source:
        data = source.read(MAX_TEXT_BYTES + 1)
    byte_truncated = len(data) > MAX_TEXT_BYTES
    data = data[:MAX_TEXT_BYTES]
    encoding = 'utf-8-sig'
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = 'utf-32'
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = 'utf-16'
    try:
        value = codecs.getincrementaldecoder(encoding)('strict').decode(data, final=not byte_truncated)
    except UnicodeError as exc:
        raise DocumentError('File is binary or is not UTF-8/BOM-marked Unicode text. Convert the encoding or export a supported document format; no replacement text was fabricated.') from exc
    if any(ord(char) < 32 and char not in '\t\n\r\f' for char in value):
        raise DocumentError('Unsupported binary content; it cannot be read as text.')
    # Match normal text-mode reads while preserving CSV/code/XML/HTML as source.
    value = value.replace('\r\n', '\n').replace('\r', '\n')
    return value[:MAX_EXTRACT_CHARS], byte_truncated or len(value) > MAX_EXTRACT_CHARS


def _identity(status):
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns,
            status.st_ctime_ns, status.st_mode, status.st_uid, status.st_gid)


def _cached(key):
    with _CACHE_LOCK:
        # Enforce both bounds on lookup as well as insertion.
        while _CACHE and (len(_CACHE) > MAX_CACHE_ENTRIES or
                          sum(item[1] for item in _CACHE.values()) > MAX_CACHE_BYTES):
            _CACHE.popitem(last=False)
        item = _CACHE.get(key)
        if item is not None:
            _CACHE.move_to_end(key)
            return item[0]
    return None


def _retain(key, result):
    size = sys.getsizeof(result[0]) + sys.getsizeof(key) + sum(sys.getsizeof(part) for part in key)
    with _CACHE_LOCK:
        # Drop old versions of this workspace/source/selection immediately.
        for old in list(_CACHE):
            if old[:2] == key[:2] and old[2] != key[2]:
                del _CACHE[old]
        if size > MAX_CACHE_BYTES or MAX_CACHE_ENTRIES < 1:
            return
        _CACHE[key] = (result, size)
        _CACHE.move_to_end(key)
        while len(_CACHE) > MAX_CACHE_ENTRIES or sum(item[1] for item in _CACHE.values()) > MAX_CACHE_BYTES:
            _CACHE.popitem(last=False)


def _extract(path, head, page_start, page_count, sheet):
    suffix = path.suffix.lower()
    is_pdf = head.startswith(b'%PDF-') or suffix == '.pdf'
    if sheet is not None and suffix != '.xlsx':
        raise DocumentError('sheet is only supported for XLSX workbooks.')
    if not is_pdf and (page_start != 1 or page_count != 10):
        raise DocumentError('page_start and page_count are only supported for PDF files.')
    if is_pdf:
        value, truncated = _pdf(path, page_start, page_count)
    elif suffix in _OFFICE:
        if head.startswith(b'\xd0\xcf\x11\xe0'):
            raise DocumentError('Encrypted or legacy Office container. Save an unencrypted DOCX, XLSX or PPTX copy.')
        book = _Office(path)
        try:
            value, truncated = _docx(book) if suffix == '.docx' else _xlsx(book, sheet) if suffix == '.xlsx' else _pptx(book)
        finally:
            book.close()
    elif suffix in {'.doc', '.xls', '.ppt', '.docm', '.xlsm', '.pptm', '.xlsb'} or head.startswith(b'\xd0\xcf\x11\xe0'):
        raise DocumentError('Legacy, macro-enabled or encrypted Office format is not supported. Export an unencrypted DOCX, XLSX, PPTX, CSV or PDF copy; macros are not executed.')
    elif suffix in _IMAGES or head.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff', b'GIF87a', b'GIF89a')):
        value, truncated = _image(path)
    elif suffix == '.zip' or head.startswith((b'PK\x03\x04', b'PK\x05\x06')):
        value, truncated = _archive(path, True)
    elif path.name.lower().endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2')):
        value, truncated = _archive(path, False)
    elif suffix in _MEDIA:
        raise DocumentError('Audio/video content is not transcribed or visually understood by read_file. Supply a transcript, captions or extracted images, then use the appropriate media/vision tools.')
    elif suffix in {'.rar', '.7z', '.gz', '.bz2', '.xz', '.txz', '.rtf', '.odt', '.ods', '.odp'}:
        raise DocumentError('Unsupported document/archive format. Export text, DOCX/XLSX/PPTX, PDF, or a ZIP/TAR archive.')
    else:
        value, truncated = _plain(path)
    return value, truncated


def _relevant(value, query, path):
    """Rank bounded overlapping excerpts by keyword coverage, with original offsets."""
    terms = set(re.findall(r'\w+', query.casefold()))
    phrase = query.casefold().strip()
    markers = {match.start(): match.group() for match in
               re.finditer(r'^\[(?:Page \d+|Sheet .*|Slide \d+)\]$', value, re.M)}
    candidates = []
    # Do not cross a page/sheet boundary: each excerpt gets its own provenance.
    boundaries = sorted({0, len(value), *markers})
    for begin, end in zip(boundaries, boundaries[1:]):
        label = markers.get(begin, '')
        for start in range(begin, end, 900):
            stop = min(start + 1200, end)
            chunk = value[start:stop]
            words = set(re.findall(r'\w+', chunk.casefold()))
            score = len(terms & words)
            if score:
                candidates.append((score, phrase in chunk.casefold(), start, stop, label))
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
    selected = []
    for candidate in candidates:
        if any(candidate[2] < item[3] and candidate[3] > item[2] for item in selected):
            continue
        selected.append(candidate)
        if len(selected) == 8:
            break
    heading = ('[Keyword excerpts from ' + json.dumps(str(path), ensure_ascii=False) +
               '. Search covers only the selected extraction, not necessarily the complete file. '
               'At most 8 excerpts; original extracted character offsets follow.]')
    if re.match(r'^\[(?:PDF |DOCX |XLSX |PPTX |Image |Archive )', value):
        heading += '\n' + value.split('\n', 1)[0]
    if not selected:
        return heading + '\n[No keyword matches in the selected extraction.]'
    return heading + ''.join(
        f'\n\n[Source characters {start}-{stop}; end exclusive] {label}\n' + value[start:stop]
        for _, _, start, stop, label in selected
    )


def read_document(path: Path, *, offset=0, limit=12_000, page_start=1, page_count=10,
                  sheet=None, query=None, workspace=None):
    """Read a checked source, reusing bounded extraction across paging/search calls."""
    offset = _integer(offset, 'offset', 0, MAX_EXTRACT_CHARS)
    limit = _integer(limit, 'limit', 1, 50_000)
    page_start = _integer(page_start, 'page_start', 1, 1_000_000)
    page_count = _integer(page_count, 'page_count', 1, 50)
    if sheet is not None and (not isinstance(sheet, str) or not sheet):
        raise DocumentError('sheet must be a non-empty worksheet name.')
    if query is not None and (not isinstance(query, str) or not 1 <= len(query) <= 256
                              or not re.search(r'\w', query)):
        raise DocumentError('query must contain keywords and be 1-256 characters.')
    path = path.resolve(strict=True)
    status = path.stat()
    if not stat.S_ISREG(status.st_mode):
        raise DocumentError('Only regular files can be read.')
    if status.st_size > MAX_FILE_BYTES:
        raise DocumentError('File exceeds the 32 MiB reading limit. Split or export a smaller copy.')
    identity = _identity(status)
    # Never bypass current OS access checks, including for cached text.
    with path.open('rb') as source:
        if _identity(os.fstat(source.fileno())) != identity:
            raise DocumentError('File changed while opening it. Retry the read.')
        head = source.read(1024)
    key = (str(Path(workspace).resolve()) if workspace is not None else None,
           str(path), identity, path.suffix.lower(), page_start, page_count, sheet)
    extracted = _cached(key)
    if extracted is None:
        extracted = _extract(path, head, page_start, page_count, sheet)
        if _identity(path.stat()) != identity:
            raise DocumentError('File changed during extraction. Retry the read.')
        _retain(key, extracted)
    elif _identity(path.stat()) != identity:
        raise DocumentError('File changed during the read. Retry the read.')
    value, truncated = extracted
    if query is not None:
        value = _relevant(value, query, path)
    if offset > len(value):
        raise DocumentError(f'offset={offset} is past the {len(value)} extracted characters. Reset offset or select another PDF page range/XLSX sheet.')
    end = min(len(value), offset + limit)
    result = value[offset:end]
    if end < len(value):
        kind = 'Search result' if query is not None else 'Extracted'
        # Name the path: told "the same options", a model continued with read_file({"limit": 12000}) and no path (2026-09-23).
        result += (f'\n[More text: call read_file with path={json.dumps(str(path), ensure_ascii=False)}, offset={end}, '
                   f'limit={limit} (keep any page, sheet or query options). {kind} characters: {len(value)}.]')
    if truncated:
        result += f'\n[Extraction stopped at a safety limit: at most {MAX_EXTRACT_CHARS} characters, 2 MiB text input, 10000 cells, 50 sheets or 200 slides. This is not the complete file. Select a sheet/page range or split the source.]'
    return result or '[Empty file or no text in the selected range.]'
