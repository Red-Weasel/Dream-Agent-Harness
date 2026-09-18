"""Bounded, non-executing reads and format checks for guided outputs."""
from __future__ import annotations

import io
import json
import math
import os
from pathlib import Path
import posixpath
import stat
from urllib.parse import unquote, urlsplit
import zipfile
from xml.etree import ElementTree

MAX_ARTIFACT = 8 * 1024 * 1024


def verify_page(workspace: Path, value: str) -> str:
    """Check static dependency presence without executing or fetching anything."""
    from html.parser import HTMLParser

    raw = Path(value)
    if raw.is_absolute():
        raw = raw.relative_to(workspace)
    data = read_bounded(workspace, str(raw))
    text = data.decode('utf-8')
    if not text.strip() or '\0' in text:
        raise ValueError('Page must contain nonempty UTF-8 HTML or SVG')
    references = []

    class Dependencies(HTMLParser):
        def handle_starttag(self, tag, attrs):
            first_attrs = {}
            for key, value in attrs:
                first_attrs.setdefault(key, value)
            attrs = first_attrs
            if tag == 'base' and attrs.get('href'):
                raise ValueError('Inline base-relative resources before delivery; base href is not checked')
            keys = ['src'] if tag in {'img', 'script', 'video', 'audio', 'source', 'track', 'iframe', 'embed'} else []
            if tag == 'video':
                keys.append('poster')
            if tag == 'link' and 'stylesheet' in (attrs.get('rel') or '').lower().split():
                keys.append('href')
            if tag in {'image', 'use'}:
                keys.extend(['href', 'xlink:href'])
            for key in keys:
                if attrs.get(key):
                    references.append(attrs[key].strip())
                    if len(references) > 128:
                        raise ValueError('Page exceeds the 128 static dependency check limit')

    Dependencies().feed(text)
    checked = set()
    for reference in references:
        url = urlsplit(reference)
        if url.scheme.lower() in {'http', 'https', 'data', 'blob'} or url.netloc or reference.startswith('#'):
            continue
        if url.scheme:
            raise ValueError(f'Unsupported local dependency URL: {reference!r}')
        # Browser URL resolution removes dot segments before opening the file.
        # Normalize against the page's parent, then enforce the workspace bound
        # and no-follow component opens on the resulting dependency path.
        local = unquote(url.path)
        name = str(relative_path(posixpath.normpath(posixpath.join(str(raw.parent), local))))
        if name in checked:
            continue
        try:
            check_dependency(workspace, name)
        except ValueError as exc:
            raise ValueError(f'Local media/resource dependency {reference!r}: {exc}') from exc
        checked.add(name)
    return (f'Checked page bytes and {len(checked)} static local dependency file(s) for regular, nonempty presence. '
            'Visual quality, factual accuracy, media decoding, remote/inline resources, '
            'CSS and dynamic dependencies remain unverified.')


def check_dependency(workspace: Path, value: str) -> None:
    """Inspect metadata only; large media must not require whole-file reads."""
    path = relative_path(value)
    descriptors = []
    try:
        fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(fd)
        for part in path.parts[:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            descriptors.append(fd)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        descriptors.append(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not info.st_size:
            raise ValueError('Expected a nonempty regular dependency file')
    except FileNotFoundError as exc:
        raise ValueError('Local dependency is missing: ' + value) from exc
    except OSError as exc:
        raise ValueError('Cannot safely open local dependency: ' + value) from exc
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def relative_path(value: str) -> Path:
    if not isinstance(value, str) or not value or '\\' in value or '\0' in value:
        raise ValueError('Use a workspace-relative file path')
    path = Path(value)
    if path.is_absolute() or any(p in {'.', '..', '.dream', '.git'} for p in path.parts):
        raise ValueError('Use a workspace file outside private state')
    return path


def read_bounded(workspace: Path, value: str) -> bytes:
    """Open each component without symlinks, then bound the actual read."""
    path = relative_path(value)
    descriptors = []
    try:
        fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(fd)
        for part in path.parts[:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            descriptors.append(fd)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        descriptors.append(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARTIFACT:
            raise ValueError('Expected a regular file of at most 8 MiB')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            data = stream.read(MAX_ARTIFACT + 1)
        if len(data) > MAX_ARTIFACT:
            raise ValueError('Artifact exceeds 8 MiB')
        if not data:
            raise ValueError('Artifact is empty')
        return data
    except FileNotFoundError as exc:
        raise ValueError('Expected output or source file is missing: ' + value) from exc
    except OSError as exc:
        raise ValueError('Cannot safely open workspace file: ' + value) from exc
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def verify(data: bytes, recipe: str) -> str:
    if recipe == 'report':
        text = data.decode('utf-8')
        body = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))
        if '\0' in text or len(body.strip()) < 20 or not any(line.startswith('# ') and line[2:].strip() for line in text.splitlines()):
            raise ValueError('Report must be readable Markdown with a heading and body text')
        facts = 'Opened UTF-8 Markdown; heading and nonempty body checked.'
    elif recipe == 'analysis':
        def finite_number(value):
            number = float(value)
            if not math.isfinite(number):
                raise ValueError('Analysis JSON numbers must be finite')
            return number
        def invalid_constant(value):
            raise ValueError('Analysis JSON does not allow ' + value)
        obj = json.loads(data, parse_float=finite_number, parse_constant=invalid_constant)
        if not isinstance(obj, dict) or not isinstance(obj.get('summary'), str) or not obj['summary'].strip():
            raise ValueError('Analysis JSON needs a nonempty summary string')
        if not isinstance(obj.get('results'), list) or not obj['results'] or not all(isinstance(r, dict) and bool(r) for r in obj['results']):
            raise ValueError('Analysis JSON needs a nonempty results array of objects')
        if not isinstance(obj.get('sources'), list) or not obj['sources'] or not all(isinstance(s, str) and s.strip() for s in obj['sources']):
            raise ValueError('Analysis JSON needs source references')
        facts = 'Parsed JSON; summary, result records and source references checked.'
    elif recipe == 'presentation':
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as package:
                infos = package.infolist()
                if len(infos) > 1000 or sum(i.file_size for i in infos) > 32 * 1024 * 1024:
                    raise ValueError('Presentation expands beyond verification bounds')
                names = package.namelist()
                if len(set(names)) != len(names):
                    raise ValueError('Presentation package contains duplicate members')
                if any(name.startswith('/') or '\\' in name or
                       any(part in {'.', '..'} for part in name.split('/')) for name in names):
                    raise ValueError('Presentation package contains unsafe member paths')
                # Slides can parse while their images, charts or layouts are absent.
                # Inspect package relationships without extracting or following URLs.
                relationship_ns = '{http://schemas.openxmlformats.org/package/2006/relationships}'
                for name in names:
                    if not name.endswith('.rels'):
                        continue
                    root = ElementTree.fromstring(package.read(name))
                    if root.tag != relationship_ns + 'Relationships':
                        raise ValueError('Presentation contains invalid relationship XML')
                    seen = set()
                    for relation in root:
                        identifier = relation.get('Id')
                        if relation.tag != relationship_ns + 'Relationship' or not identifier or identifier in seen:
                            raise ValueError('Presentation contains invalid or duplicate relationship IDs')
                        seen.add(identifier)
                        if relation.get('TargetMode') == 'External':
                            continue
                        target = urlsplit(relation.get('Target', ''))
                        part = unquote(target.path)
                        base = posixpath.dirname(posixpath.dirname(name))
                        resolved = posixpath.normpath(posixpath.join(base, part)) if not part.startswith('/') else part[1:]
                        if target.scheme or target.netloc or not part or '\\' in part or resolved.startswith('../') or resolved == '..':
                            raise ValueError('Presentation contains an unsafe internal relationship')
                        if resolved not in names:
                            raise ValueError(f'Presentation relationship references a missing part: {resolved}')
                roots = {name: ElementTree.fromstring(package.read(name)) for name in
                         ('[Content_Types].xml', '_rels/.rels', 'ppt/presentation.xml', 'ppt/_rels/presentation.xml.rels')}
                pns = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
                rns = '{http://schemas.openxmlformats.org/package/2006/relationships}'
                ons = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
                cns = '{http://schemas.openxmlformats.org/package/2006/content-types}'
                expected = {'[Content_Types].xml': cns+'Types', '_rels/.rels': rns+'Relationships',
                            'ppt/presentation.xml': pns+'presentation', 'ppt/_rels/presentation.xml.rels': rns+'Relationships'}
                if any(roots[name].tag != tag for name, tag in expected.items()):
                    raise ValueError('Presentation package has invalid document roots')
                if not any(n.get('Target') in {'ppt/presentation.xml', '/ppt/presentation.xml'} and
                           n.get('Type', '').endswith('/officeDocument') for n in roots['_rels/.rels']):
                    raise ValueError('Presentation package has no office document relationship')
                main_types = [n.get('ContentType') for n in roots['[Content_Types].xml']
                              if n.tag == cns+'Override' and n.get('PartName') == '/ppt/presentation.xml']
                if main_types != ['application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml']:
                    raise ValueError('Presentation is missing its correct main document content type')
                declared = {n.get('PartName') for n in roots['[Content_Types].xml']
                            if n.get('ContentType') == 'application/vnd.openxmlformats-officedocument.presentationml.slide+xml'}
                relations = {n.get('Id'): n for n in roots['ppt/_rels/presentation.xml.rels']}
                slides = []
                for entry in roots['ppt/presentation.xml'].iter(pns+'sldId'):
                    relation = relations.get(entry.get(ons+'id'))
                    if relation is None or not relation.get('Type', '').endswith('/slide') or relation.get('TargetMode') == 'External':
                        raise ValueError('Presentation has an invalid slide relationship')
                    target = relation.get('Target', '')
                    name = target.lstrip('/') if target.startswith('/') else 'ppt/' + target
                    if '..' in Path(name).parts or name not in names or '/' + name not in declared:
                        raise ValueError('Presentation references a missing or undeclared slide')
                    slides.append(name)
                if not slides:
                    raise ValueError('Presentation contains no linked slides')
                ns = '{http://schemas.openxmlformats.org/drawingml/2006/main}t'
                slide_roots = [ElementTree.fromstring(package.read(s)) for s in slides]
                if any(root.tag != pns+'sld' for root in slide_roots):
                    raise ValueError('Presentation contains an invalid slide document')
                if not any((t.text or '').strip() for root in slide_roots for t in root.iter(ns)):
                    raise ValueError('Presentation contains no readable slide text')
                facts = f'Opened PPTX package; required XML and {len(slides)} slide files checked.'
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
            raise ValueError('Presentation is not a readable PPTX package') from exc
    else:
        raise ValueError('Unknown artifact recipe')
    return facts + ' Factual accuracy, calculations and visual quality still need your review.'
