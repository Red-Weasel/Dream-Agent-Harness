#!/usr/bin/env python3
"""Deterministic steps of Dream's `understand` skill. Copy me to .ua/tmp/ua_glue.py and run from the project root:
  python3 .ua/tmp/ua_glue.py imports-input | scan-result | batch-inputs | structure <i> | nodes | assemble | finish
The `understand-domain` skill uses two more: domain-context | domain
Every problem is printed by name with the step that fixes it; a non-zero exit means the map is not done."""
import datetime
import glob
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter

ROOT = os.getcwd()
UA = os.path.join(ROOT, '.ua')
LEGACY = os.path.join(ROOT, '.understand-anything')
TMP, INTER = os.path.join(UA, 'tmp'), os.path.join(UA, 'intermediate')
FILE_TYPES = ('file', 'config', 'document', 'service', 'pipeline', 'table', 'schema', 'resource', 'endpoint')
NODE_TYPES = FILE_TYPES + ('function', 'class', 'module', 'concept')
EDGE_TYPES = ('imports exports contains inherits implements calls subscribes publishes middleware reads_from writes_to '
              'transforms validates depends_on tested_by configures related similar_to deploys serves provisions '
              'triggers migrates documents routes defines_schema').split()
DOMAIN_NODES, DOMAIN_EDGES = ('domain', 'flow', 'step'), ('contains_flow', 'flow_step', 'cross_domain')
BATCH_NAME = re.compile(r'^batch-(\d+)(?:-part-(\d+))?\.json$')
STEP_OF = {'scan.json': 'scan-project.mjs (step 2)', 'imports-out.json': 'extract-import-map.mjs (step 2)',
           'project.json': 'write_file .ua/tmp/project.json (step 1)', 'scan-result.json': 'glue scan-result (step 2)',
           'batches.json': 'compute-batches.mjs (step 3)', 'assembled-graph.json': 'merge-batch-graphs.py (step 5)',
           'layers.json': 'write_file layers.json (step 6)', 'tour.json': 'write_file tour.json (step 6)'}


def load(path, default=None):
    if default is not None and not os.path.isfile(path):
        return default
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)


def rel(path):
    return os.path.relpath(path, ROOT)


def imports_input():
    scan = load(os.path.join(TMP, 'scan.json'))
    save(os.path.join(TMP, 'imports-in.json'), {'projectRoot': ROOT, 'files': scan['files']})
    print('wrote .ua/tmp/imports-in.json for %d files' % len(scan['files']))


def scan_result():
    scan, imports = load(os.path.join(TMP, 'scan.json')), load(os.path.join(TMP, 'imports-out.json'))
    proj = load(os.path.join(TMP, 'project.json'), {})
    result = {'name': proj.get('name') or os.path.basename(ROOT),
              'description': proj.get('description') or 'No description available',
              'languages': sorted(scan['stats']['byLanguage']), 'frameworks': list(proj.get('frameworks') or []),
              'files': scan['files'], 'totalFiles': scan['totalFiles'], 'filteredByIgnore': scan['filteredByIgnore'],
              'estimatedComplexity': scan['estimatedComplexity'], 'importMap': imports['importMap']}
    save(os.path.join(INTER, 'scan-result.json'), result)
    cats = ', '.join('%s %d' % kv for kv in sorted(scan['stats']['byCategory'].items()))
    print('%d files (%s); %d excluded by the ignore rules; languages: %s' % (
        scan['totalFiles'], cats, scan['filteredByIgnore'], ', '.join(result['languages'])))


def batch_inputs():
    for stale in glob.glob(os.path.join(INTER, 'batch*.json')) + [os.path.join(INTER, 'assembled-graph.json')]:
        if os.path.exists(stale) and os.path.basename(stale) != 'batches.json':
            os.remove(stale)
    batches = load(os.path.join(INTER, 'batches.json'))
    for b in batches['batches']:
        i = b['batchIndex']
        save(os.path.join(TMP, 'extract-in-%d.json' % i),
             {'projectRoot': ROOT, 'batchFiles': b['files'], 'batchImportData': b['batchImportData']})
        print('batch %d:' % i)
        for f in b['files']:
            imps = b['batchImportData'].get(f['path']) or []
            print('  %s (%s, %s lines, %s)%s' % (f['path'], f['language'], f['sizeLines'], f['fileCategory'],
                                                  ' imports: ' + ', '.join(imps) if imps else ''))
        for path, near in (b.get('neighborMap') or {}).items():
            print('  %s is linked to other batches: %s' % (path, '; '.join(
                '%s [%s]' % (n['path'], ', '.join((n.get('symbols') or [])[:8])) for n in near)))
    print('%d batches: write .ua/intermediate/batch-<i>.json for each' % batches['totalBatches'])


def batch_indices():
    return [b['batchIndex'] for b in load(os.path.join(INTER, 'batches.json'), {}).get('batches', [])]


def structure(i):
    path = os.path.join(TMP, 'extract-out-%s.json' % i)
    if not os.path.isfile(path):
        print('problem: no %s -- the batch indices are %s; run extract-structure.mjs for batch %s first (step 4.1)'
              % (rel(path), ', '.join(map(str, batch_indices())) or 'unknown (run step 3)', i))
        return 1
    out = load(path)
    for r in out.get('results') or []:
        print('%s: %s lines' % (r['path'], r.get('totalLines')))
        for fn in r.get('functions') or []:
            print('  function %s lines %s-%s params %s' % (fn['name'], fn.get('startLine'), fn.get('endLine'),
                                                            ', '.join(fn.get('params') or [])))
        for c in r.get('classes') or []:
            print('  class %s lines %s-%s methods %s' % (c['name'], c.get('startLine'), c.get('endLine'),
                                                          ', '.join(c.get('methods') or [])))
        if r.get('exports'):
            print('  exports: ' + ', '.join(str(e.get('name')) for e in r['exports']))
        for key in ('sections', 'services', 'endpoints', 'steps', 'resources', 'definitions'):
            if r.get(key):
                print('  %s: %s' % (key, ', '.join(str(x.get('name') or x.get('heading') or x.get('kind')
                                                      or ('%s %s' % (x.get('method', ''), x.get('path', ''))).strip())
                                                  for x in r[key])))
    if out.get('filesSkipped'):
        print('no parser for: %s -- read them and list their functions yourself' % ', '.join(map(str, out['filesSkipped'])))
    if out.get('filesUnreadable'):
        print('UNREADABLE (wrong path?): %s' % json.dumps(out['filesUnreadable']))
    return 0


def file_nodes(graph):
    return [n for n in graph.get('nodes') or [] if n.get('type') in FILE_TYPES]


def node_path(n):
    fp = n.get('filePath')
    if isinstance(fp, str) and fp:
        return fp
    nid = str(n.get('id') or '')
    return nid.split(':', 1)[1] if ':' in nid else nid


def coverage_problems(graph, scan):
    """Every scanned file must have a file-level node; a skipped or truncated batch file is how files go missing."""
    present = {node_path(n) for n in file_nodes(graph)}
    missing = [f['path'] for f in scan.get('files') or [] if f['path'] not in present]
    if not missing:
        return []
    shown = ', '.join(missing[:8]) + (' ...' if len(missing) > 8 else '')
    return ['%d scanned file%s ha%s no node: %s -- a batch file was skipped or truncated; see the merge report, '
            'rewrite it whole (step 4), rerun step 5' % (len(missing), 's' if len(missing) != 1 else '',
                                                        've' if len(missing) != 1 else 's', shown)]


def batch_problems():
    """Read every batch file myself: the merge skips a broken or misnamed one with only a warning and exit 0."""
    problems, seen = [], {}
    files = [p for p in sorted(glob.glob(os.path.join(INTER, 'batch*.json'))) if os.path.basename(p) != 'batches.json']
    for path in files:
        name = os.path.basename(path)
        m = BATCH_NAME.match(name)
        if not m:
            problems.append('%s is not a name the merge reads (batch-<i>.json or batch-<i>-part-<k>.json) -- rename it, rerun step 5' % name)
            continue
        try:
            data = load(path)
        except ValueError as exc:
            problems.append('%s is not valid JSON (%s) -- rewrite it whole (step 4), rerun step 5' % (name, exc))
            continue
        if not isinstance(data, dict) or not isinstance(data.get('nodes'), list) or not isinstance(data.get('edges'), list):
            problems.append('%s lacks the "nodes" and "edges" lists -- rewrite it whole (step 4), rerun step 5' % name)
        seen.setdefault(int(m.group(1)), set()).add(int(m.group(2) or 0))
    for i, parts in sorted(seen.items()):
        nums = sorted(p for p in parts if p)
        if nums and nums != list(range(1, nums[-1] + 1)):
            problems.append('batch %d has parts %s: a part is missing -- write it (step 4), rerun step 5' % (i, nums))
    for i in batch_indices():
        if i not in seen:
            problems.append('batch %d has no readable batch-%d.json -- write it whole (step 4), rerun step 5' % (i, i))
    asm = os.path.join(INTER, 'assembled-graph.json')
    if os.path.isfile(asm):
        newer = [os.path.basename(p) for p in files if os.path.getmtime(p) > os.path.getmtime(asm)]
        if newer:
            problems.append('%s changed after the last merge -- rerun step 5' % ', '.join(newer))
    return problems


def nodes():
    g, scan = load(os.path.join(INTER, 'assembled-graph.json')), load(os.path.join(INTER, 'scan-result.json'), {})
    for n in file_nodes(g):
        print('%s -- %s' % (n['id'], (n.get('summary') or '')[:90]))
    print('%d file-level nodes: put every id in exactly one layer' % len(file_nodes(g)))
    problems = batch_problems() + coverage_problems(g, scan)
    for q in problems:
        print('problem: ' + q)
    return 1 if problems else 0


def as_list(data, *keys):
    if isinstance(data, dict):
        data = next((data[k] for k in keys if isinstance(data.get(k), list)), [])
    return data if isinstance(data, list) else []


def kebab(text):
    return ''.join(c if c.isalnum() else '-' for c in str(text).lower()).strip('-') or 'layer'


def node_ids(raw, ids, problems, where):
    out = []
    for item in raw or []:
        nid = str(item.get('id') if isinstance(item, dict) else item)
        nid = nid if nid in ids or 'file:' + nid not in ids else 'file:' + nid
        if nid not in ids:
            problems.append('%s names unknown node %s (dropped)' % (where, nid))
        elif nid not in out:
            out.append(nid)
    return out


def assemble():
    g, scan = load(os.path.join(INTER, 'assembled-graph.json')), load(os.path.join(INTER, 'scan-result.json'))
    ids = {n['id'] for n in g['nodes']}
    problems, layers, tour = batch_problems() + coverage_problems(g, scan), [], []
    for k, L in enumerate(as_list(load(os.path.join(INTER, 'layers.json'), []), 'layers'), 1):
        name = L.get('name') or L.get('id') or 'Layer %d' % k
        lid = str(L.get('id') or 'layer:' + kebab(name))
        layers.append({'id': lid if lid.startswith('layer:') else 'layer:' + kebab(lid), 'name': name,
                       'description': L.get('description') or '',
                       'nodeIds': node_ids(L.get('nodeIds', L.get('nodes')), ids, problems, 'layer ' + lid)})
    steps = as_list(load(os.path.join(INTER, 'tour.json'), []), 'steps', 'tour')
    for k, s in enumerate(sorted(steps, key=lambda s: float(s.get('order') or 0)), 1):
        step = {'order': len(tour) + 1, 'title': s.get('title') or 'Step %d' % k,
                'description': s.get('description') or s.get('whyItMatters') or '',
                'nodeIds': node_ids(s.get('nodeIds', s.get('nodesToInspect')), ids, problems, 'tour step %d' % k)}
        if s.get('languageLesson'):
            step['languageLesson'] = s['languageLesson']
        if step['nodeIds']:
            tour.append(step)
        else:
            problems.append('tour step %d has no existing nodeIds (dropped)' % k)
    problems += ['no %s: write .ua/intermediate/%s.json (step 6)' % (w, w) for w, items in (('layers', layers), ('tour', tour)) if not items]
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        commit = ''
    when = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    project = {'name': scan['name'], 'languages': scan['languages'], 'frameworks': scan['frameworks'],
               'description': scan['description'], 'analyzedAt': when, 'gitCommitHash': commit or 'none'}
    graph = {'version': '1.0.0', 'project': project, 'nodes': g['nodes'], 'edges': g['edges'], 'layers': layers, 'tour': tour}
    analyzed = len({node_path(n) for n in file_nodes(graph)})       # the files actually in the map, not the scan's count
    save(os.path.join(UA, 'knowledge-graph.json'), graph)
    save(os.path.join(UA, 'meta.json'), {'lastAnalyzedAt': when, 'gitCommitHash': project['gitCommitHash'],
                                          'version': '1.0.0', 'analyzedFiles': analyzed})
    save(os.path.join(UA, 'diff-overlay.json'), {'version': '1.0.0', 'baseBranch': 'HEAD', 'generatedAt': when,
                                                  'changedFiles': [], 'changedNodeIds': [], 'affectedNodeIds': []})
    save(os.path.join(INTER, 'fingerprint-input.json'), {'projectRoot': ROOT, 'gitCommitHash': project['gitCommitHash'],
                                                         'filePaths': [f['path'] for f in scan['files']]})
    print('wrote .ua/knowledge-graph.json, .ua/meta.json, .ua/diff-overlay.json, .ua/intermediate/fingerprint-input.json'
          + ('' if analyzed == scan['totalFiles'] else ' (%d of %d scanned files are in the map)' % (analyzed, scan['totalFiles'])))
    return report(graph, problems + check_graph(graph, NODE_TYPES, EDGE_TYPES, True))


def check_graph(graph, ntypes, etypes, layered):
    problems, ids = [], set()
    for n in graph.get('nodes') or []:
        nid = n.get('id')
        problems += ['node %s lacks %s' % (nid, k) for k in ('id', 'type', 'name', 'summary', 'tags', 'complexity') if not n.get(k)]
        if n.get('type') not in ntypes:
            problems.append("node %s: type '%s' is not in the schema" % (nid, n.get('type')))
        if n.get('complexity') not in ('simple', 'moderate', 'complex'):
            problems.append("node %s: complexity '%s' is not simple/moderate/complex" % (nid, n.get('complexity')))
        if not isinstance(n.get('tags'), list):
            problems.append('node %s: tags must be a list' % nid)
        if n.get('type') in FILE_TYPES:
            fp = n.get('filePath')
            if not isinstance(fp, str) or not fp:
                problems.append('node %s lacks filePath (the panel previews the file through it)' % nid)
            elif os.path.isabs(fp) or fp.startswith('..'):
                problems.append('node %s: filePath must be relative to the project root, not %s' % (nid, fp))
        if nid in ids:
            problems.append('duplicate node id %s' % nid)
        ids.add(nid)
    for e in graph.get('edges') or []:
        label = '%s -> %s' % (e.get('source'), e.get('target'))
        if e.get('type') not in etypes:
            problems.append("edge %s: type '%s' is not in the schema" % (label, e.get('type')))
        problems += ['edge %s: %s is not a node' % (label, e.get(end)) for end in ('source', 'target') if e.get(end) not in ids]
        w = e.get('weight')
        if isinstance(w, bool) or not isinstance(w, (int, float)) or not 0 <= w <= 1:
            problems.append('edge %s: weight %r must be a number from 0 to 1' % (label, w))
        if e.get('direction') not in ('forward', 'backward', 'bidirectional'):
            problems.append('edge %s: direction must be forward' % label)
    problems += ['project lacks %s' % k for k in ('name', 'languages', 'frameworks', 'description', 'analyzedAt', 'gitCommitHash')
                 if k not in (graph.get('project') or {})]
    if not isinstance(graph.get('layers'), list) or not isinstance(graph.get('tour'), list):
        problems.append('layers and tour must be lists')
    elif layered:
        assigned = Counter(i for L in graph['layers'] for i in (L.get('nodeIds') or []))
        problems += ['file node %s is in %d layers (needs exactly 1)' % (n['id'], assigned[n['id']])
                     for n in file_nodes(graph) if assigned[n['id']] != 1]
        problems += ['a layer names unknown node %s' % i for i in assigned if i not in ids]
        problems += ['tour step %s names unknown node %s' % (s.get('order'), i)
                     for s in graph['tour'] for i in (s.get('nodeIds') or []) if i not in ids]
    return problems


def finish():
    kg_path = os.path.join(UA, 'knowledge-graph.json')
    if not os.path.isfile(kg_path):
        print('problem: no .ua/knowledge-graph.json yet -- run `assemble` (step 7) first')
        return 1
    kg = load(kg_path)
    problems = batch_problems() + coverage_problems(kg, load(os.path.join(INTER, 'scan-result.json'), {}))
    problems += check_graph(kg, NODE_TYPES, EDGE_TYPES, True)
    src, dg_path = os.path.join(INTER, 'domain.json'), os.path.join(UA, 'domain-graph.json')
    if os.path.isfile(src):
        d = load(src)
        save(dg_path, {'version': '1.0.0', 'project': kg['project'], 'nodes': d.get('nodes') or [],
                       'edges': d.get('edges') or [], 'layers': [], 'tour': []})
    if os.path.isfile(dg_path):
        dg = load(dg_path)
        problems += ['domain-graph: ' + q for q in check_graph(dg, DOMAIN_NODES, DOMAIN_EDGES, False)]
        into = {(e.get('type'), e.get('target')) for e in dg.get('edges') or []}
        for n in dg.get('nodes') or []:
            edge = {'flow': 'contains_flow', 'step': 'flow_step'}.get(n.get('type'))
            if edge and (edge, n.get('id')) not in into:
                problems.append('domain-graph: %s %s has no %s edge into it' % (n.get('type'), n.get('id'), edge))
    else:
        problems.append('no domain graph: write .ua/intermediate/domain.json (step 8)')
    overlay = load(os.path.join(UA, 'diff-overlay.json'), {})
    if not all(isinstance(overlay.get(k), list) for k in ('changedNodeIds', 'affectedNodeIds')):
        problems.append('diff-overlay.json is malformed: run `assemble` again')
    meta = load(os.path.join(UA, 'meta.json'), {})
    problems += ['meta.json lacks %s: run `assemble` again' % k
                 for k in ('lastAnalyzedAt', 'gitCommitHash', 'version', 'analyzedFiles') if k not in meta]
    return report(kg, problems)


def report(graph, problems):
    def fmt(counter):
        return ', '.join('%s %d' % kv for kv in counter.most_common())
    print('%d nodes (%s); %d edges (%s); layers: %s; %d tour steps' % (
        len(graph.get('nodes') or []), fmt(Counter(n.get('type') for n in graph.get('nodes') or [])),
        len(graph.get('edges') or []), fmt(Counter(e.get('type') for e in graph.get('edges') or [])),
        ', '.join(str(L.get('name', '?')) for L in graph.get('layers') or []), len(graph.get('tour') or [])))
    print('problems: %d' % len(problems))
    for q in problems[:40]:
        print(' - ' + q)
    if len(problems) > 40:
        print(' - ... %d more' % (len(problems) - 40))
    return 1 if problems else 0


# ---- DREAM-103: the `understand-domain` skill -- a domain graph on its own, without the map's other files -------------
DOMAIN_SCAN = 'python3 "$UA_SKILLS/understand-domain/extract-domain-context.py" "$PWD"'
ENTRY_TYPES = ('http', 'cli', 'event', 'cron', 'manual')
DREAM_STATE = ('.dream/', '.remember/')          # Dream's own state in the workspace; the plugin's scan does not skip it
TOOL_DATA = ('.git', '.ua', '.dream', '.remember')   # a step's filePath names project code, never these
LANGUAGES = {'.py': 'python', '.pyi': 'python', '.ts': 'typescript', '.tsx': 'typescript', '.js': 'javascript',
             '.jsx': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript', '.go': 'go', '.rs': 'rust', '.java': 'java',
             '.kt': 'kotlin', '.scala': 'scala', '.rb': 'ruby', '.cs': 'csharp', '.php': 'php', '.swift': 'swift', '.c': 'c',
             '.h': 'c', '.cpp': 'cpp', '.hpp': 'cpp', '.ex': 'elixir', '.exs': 'elixir', '.hs': 'haskell', '.lua': 'lua',
             '.r': 'r'}


def flat(text, limit):
    text = ' '.join(str(text).split())
    return text if len(text) <= limit else text[:limit - 3] + '...'


def domain_context():
    """What the domain graph is written from: the map when .ua/knowledge-graph.json exists, else the plugin's scan."""
    kg_path, scan_path = os.path.join(UA, 'knowledge-graph.json'), os.path.join(INTER, 'domain-context.json')
    if not os.path.isfile(kg_path) and not os.path.isfile(scan_path):
        print('problem: no map (.ua/knowledge-graph.json) and no scan yet -- run %s (step 2), then rerun domain-context'
              % DOMAIN_SCAN)
        return 1
    path = kg_path if os.path.isfile(kg_path) else scan_path
    data = load(path)
    if not isinstance(data, dict):
        print('problem: %s is not a JSON object -- %s' % (rel(path), 'rebuild the map' if path == kg_path else 'rerun step 2'))
        return 1
    return context_from_map(data) if path == kg_path else context_from_scan(data)


def context_from_map(kg):
    p, nodes = kg.get('project') or {}, [n for n in kg.get('nodes') or [] if isinstance(n, dict)]
    files = {n.get('id'): n for n in file_nodes({'nodes': nodes})}
    print('source: the map (.ua/knowledge-graph.json): %s -- %s; %d files' % (
        p.get('name'), flat(p.get('description') or '', 200), len(files)))
    symbols = {}
    for n in nodes:
        if n.get('type') in ('function', 'class'):
            symbols.setdefault(node_path(n), []).append(str(n.get('name')))

    def line(n):
        defs = symbols.get(node_path(n))
        return '  %s -- %s%s' % (node_path(n), flat(n.get('summary') or '', 140), '; defines ' + ', '.join(defs[:12]) if defs else '')
    placed = set()
    for L in kg.get('layers') or []:
        print('layer %s -- %s' % (L.get('name'), flat(L.get('description') or '', 140)))
        for nid in L.get('nodeIds') or []:
            if nid in files and nid not in placed:
                placed.add(nid)
                print(line(files[nid]))
    rest = [n for nid, n in files.items() if nid not in placed]
    if rest:
        print('files in no layer:')
        for n in rest:
            print(line(n))
    names = {n.get('id'): node_path(n) if n.get('type') in FILE_TYPES else '%s:%s' % (node_path(n), n.get('name')) for n in nodes}
    links = ['  %s -> %s (%s)' % (names[e['source']], names[e['target']], e.get('type')) for e in kg.get('edges') or []
             if isinstance(e, dict) and e.get('type') not in ('contains', 'exports') and e.get('source') in names
             and e.get('target') in names]
    if links:
        print('links:\n' + '\n'.join(links[:80]) + ('\n  ... %d more' % (len(links) - 80) if len(links) > 80 else ''))
    for s in kg.get('tour') or []:
        print('tour %s. %s -- %s' % (s.get('order'), s.get('title'), flat(s.get('description') or '', 120)))
    print('next: write .ua/intermediate/domain.json (step 3)')
    return 0


def context_from_scan(scan):
    def ours(path):
        return not str(path).startswith(DREAM_STATE)
    tree = [f for f in scan.get('fileTree') or [] if ours(f)]
    print('source: the scan (.ua/intermediate/domain-context.json): %d source files' % len(tree))
    for name, value in (scan.get('metadata') or {}).items():
        if isinstance(value, dict):
            value = '; '.join('%s: %s' % (k, ', '.join(map(str, v)) if isinstance(v, list) else v) for k, v in value.items() if v)
        print('%s: %s' % (name, flat(value, 600)))
    entries, seen = [], set()
    for e in scan.get('entryPoints') or []:
        if ours(e.get('file')) and (e.get('file'), e.get('line')) not in seen:   # two patterns often hit the same line
            seen.add((e.get('file'), e.get('line')))
            entries.append(e)
    print('entry points (%d):' % len(entries))
    for e in entries[:120]:
        print('  %s %s:%s %s' % (e.get('type'), e.get('file'), e.get('line'), flat(e.get('match') or '', 100)))
    signatures = [s for s in scan.get('fileSignatures') or [] if ours(s.get('file'))]
    print('files (exports | imports):')
    for s in signatures:
        print('  %s: %s | %s' % (s.get('file'), ', '.join(s.get('exports') or []) or '-', ', '.join(s.get('imports') or []) or '-'))
    listed = {s.get('file') for s in signatures}
    other = [f for f in tree if f not in listed]
    if other:
        print('other files: ' + ', '.join(other[:80]) + (' ... %d more' % (len(other) - 80) if len(other) > 80 else ''))
    print('next: read_file the files behind the entry points you need, then write .ua/intermediate/domain.json (step 3)')
    return 0


def head_commit():
    try:
        run = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
    except OSError:
        return 'none'
    return run.stdout.strip() if run.returncode == 0 and run.stdout.strip() else 'none'


def non_finite(value, where=''):
    """Where NaN or Infinity sits: Python's json reads and writes them, the browser's JSON.parse rejects the whole file."""
    if isinstance(value, float) and not math.isfinite(value):
        return [where]
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in non_finite(v, '%s.%s' % (where, k) if where else str(k))]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in non_finite(v, '%s[%d]' % (where, i))]
    return []


def domain_problems(dg):
    """`finish`'s checks on the domain file, plus what the dashboard's Domain view needs to show every node: at least
    one domain, each edge between the right kinds of node, every flow under a domain and every step under such a flow
    (DomainGraphView lists a domain's flows by its contains_flow edges and their steps by flow_step), a real project
    filePath on every step, and no value the dashboard would drop or could not parse."""
    problems = check_graph(dg, DOMAIN_NODES, DOMAIN_EDGES, False)
    problems += ['%s is not a finite number (NaN or Infinity): the dashboard cannot read the file' % where
                 for where in non_finite(dg)]
    kinds = {n.get('id'): n.get('type') for n in dg['nodes']}
    if 'domain' not in kinds.values():
        problems.append('no domain node: write 1-6 domain:<kebab> nodes with contains_flow edges to their flows (step 3)')
    for e in dg['edges']:
        ends = {'contains_flow': ('domain', 'flow'), 'flow_step': ('flow', 'step'), 'cross_domain': ('domain', 'domain')}.get(e.get('type'))
        got = (kinds.get(e.get('source')), kinds.get(e.get('target')))
        if ends and None not in got and got != ends:
            problems.append('edge %s -> %s: %s must go %s -> %s, not %s -> %s' % (e.get('source'), e.get('target'), e.get('type'), *ends, *got))
        if e.get('type') == 'cross_domain' and e.get('source') == e.get('target'):
            problems.append('edge %s -> %s: a cross_domain edge must link two different domains' % (e.get('source'), e.get('target')))
    flows = {e.get('target') for e in dg['edges'] if e.get('type') == 'contains_flow'
             and kinds.get(e.get('source')) == 'domain' and kinds.get(e.get('target')) == 'flow'}
    steps = {e.get('target') for e in dg['edges'] if e.get('type') == 'flow_step'
             and e.get('source') in flows and kinds.get(e.get('target')) == 'step'}
    stepped = {e.get('source') for e in dg['edges'] if e.get('type') == 'flow_step' and kinds.get(e.get('target')) == 'step'}
    for n in dg['nodes']:
        nid, kind = n.get('id'), n.get('type')
        if kind == 'flow' and nid not in flows:
            problems.append('flow %s has no contains_flow edge into it from a domain (the Domain view lists a flow only '
                            'under its domain)' % nid)
        if kind == 'flow' and nid not in stepped:
            problems.append('flow %s has no flow_step edge out of it: write its 2-8 steps (step 3)' % nid)
        if isinstance(n.get('summary'), str) and n['summary'] and not n['summary'].strip():
            problems.append('node %s: summary is blank' % nid)
        if kind == 'step' and nid not in steps:
            problems.append('step %s has no flow_step edge into it from a flow under a domain (the Domain view lists a '
                            'step only under its flow)' % nid)
        # the dashboard drops a node whose fields have the wrong kind, without a word
        problems += ['node %s: %s must be text' % (nid, k) for k in ('id', 'name', 'summary') if k in n and not isinstance(n[k], str)]
        if isinstance(n.get('tags'), list) and not all(isinstance(t, str) for t in n['tags']):
            problems.append('node %s: tags must be a list of words' % nid)
        fp = n.get('filePath')
        if kind == 'step' and (not isinstance(fp, str) or not fp):
            problems.append('step %s lacks filePath (the project file that does this step)' % nid)
        elif kind == 'step' and (os.path.isabs(fp) or os.path.normpath(fp).split(os.sep)[0] == '..'):
            problems.append('step %s: filePath must be relative to the project root, not %s' % (nid, fp))
        elif kind == 'step' and os.path.normpath(fp).split(os.sep)[0] in TOOL_DATA:
            problems.append("step %s: filePath %s is inside %s/ -- tool data, not the project's code"
                            % (nid, fp, os.path.normpath(fp).split(os.sep)[0]))
        elif kind == 'step' and not os.path.isfile(os.path.join(ROOT, fp)):
            problems.append('step %s: filePath %s is not a file in the project' % (nid, fp))
        elif fp is not None and not isinstance(fp, str):
            problems.append('node %s: filePath must be text' % nid)
        lr = n.get('lineRange')
        if lr is not None and not (isinstance(lr, list) and len(lr) == 2
                                   and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in lr)
                                   and 1 <= lr[0] <= lr[1]):
            problems.append('node %s: lineRange must be [start, end] with 1 <= start <= end, not %s' % (nid, json.dumps(lr)))
        meta = n.get('domainMeta')
        if meta is not None and not isinstance(meta, dict):
            problems.append('node %s: domainMeta must be an object' % nid)
        elif meta is not None:
            if meta.get('entryType') is not None and meta['entryType'] not in ENTRY_TYPES:
                problems.append("node %s: entryType '%s' must be one of %s" % (nid, meta['entryType'], ', '.join(ENTRY_TYPES)))
            if not isinstance(meta.get('entryPoint', ''), str):
                problems.append('node %s: entryPoint must be text' % nid)
            problems += ['node %s: domainMeta.%s must be a list of strings' % (nid, k)
                         for k in ('entities', 'businessRules', 'crossDomainInteractions')
                         if k in meta and not (isinstance(meta[k], list) and all(isinstance(x, str) for x in meta[k]))]
    problems += ['edge %s -> %s: description must be text' % (e.get('source'), e.get('target'))
                 for e in dg['edges'] if 'description' in e and not isinstance(e['description'], str)]
    p = dg.get('project') or {}
    problems += ['project: %s must be text' % k for k in ('name', 'description', 'analyzedAt', 'gitCommitHash')
                 if k in p and not isinstance(p[k], str)]
    problems += ['project: %s must be a list of names' % k for k in ('languages', 'frameworks')
                 if k in p and not (isinstance(p[k], list) and all(isinstance(x, str) for x in p[k]))]
    return problems


def domain():
    """.ua/intermediate/domain.json -> .ua/domain-graph.json, written only when it passes every check."""
    src = os.path.join(INTER, 'domain.json')
    if not os.path.isfile(src):
        print('problem: no .ua/intermediate/domain.json -- write it (step 3), then rerun domain')
        return 1
    d = load(src)
    if not isinstance(d, dict) or not isinstance(d.get('nodes'), list) or not isinstance(d.get('edges'), list) or not all(
            isinstance(x, dict) for x in d['nodes'] + d['edges']):
        print('problem: .ua/intermediate/domain.json must be {"nodes": [{...}], "edges": [{...}]} -- rewrite it whole (step 3)')
        return 1
    problems, kg_path = [], os.path.join(UA, 'knowledge-graph.json')
    if os.path.isfile(kg_path):
        kg = load(kg_path)
        project = kg.get('project') if isinstance(kg, dict) and isinstance(kg.get('project'), dict) else {}
    else:
        proj = load(os.path.join(TMP, 'project.json'), {})
        if not isinstance(proj, dict):
            problems.append('.ua/tmp/project.json must be {"name", "description", "frameworks"} (step 3)')
            proj = {}
        scan = load(os.path.join(INTER, 'domain-context.json'), {})
        found = sorted({LANGUAGES[os.path.splitext(f)[1].lower()] for f in (scan.get('fileTree') if isinstance(scan, dict) else None) or []
                        if not str(f).startswith(DREAM_STATE) and os.path.splitext(f)[1].lower() in LANGUAGES})
        project = {'name': proj.get('name') or os.path.basename(ROOT), 'languages': proj.get('languages') or found,
                   'frameworks': proj.get('frameworks') or [],
                   'description': proj.get('description') or 'No description available',
                   'analyzedAt': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                   'gitCommitHash': head_commit()}
    graph = {'version': '1.0.0', 'project': project, 'nodes': d['nodes'], 'edges': d['edges'], 'layers': [], 'tour': []}
    problems += domain_problems(graph)

    def fmt(counter):
        return ', '.join('%s %d' % kv for kv in counter.most_common())
    print('%d nodes (%s); %d edges (%s); domains: %s' % (
        len(d['nodes']), fmt(Counter(n.get('type') for n in d['nodes'])), len(d['edges']),
        fmt(Counter(e.get('type') for e in d['edges'])),
        ', '.join(str(n.get('name')) for n in d['nodes'] if n.get('type') == 'domain')))
    print('problems: %d' % len(problems))
    for q in problems[:40]:
        print(' - ' + q)
    if len(problems) > 40:
        print(' - ... %d more' % (len(problems) - 40))
    if problems:
        print('not written: .ua/domain-graph.json is unchanged -- fix .ua/intermediate/domain.json (step 3), rerun domain')
        return 1
    save(os.path.join(UA, 'domain-graph.json'), graph)
    print('wrote .ua/domain-graph.json')
    return 0


def main(argv):
    commands = {'imports-input': imports_input, 'scan-result': scan_result, 'batch-inputs': batch_inputs,
                'nodes': nodes, 'assemble': assemble, 'finish': finish,
                'domain-context': domain_context, 'domain': domain}
    if not (len(argv) == 1 and argv[0] in commands) and not (len(argv) == 2 and argv[0] == 'structure'):
        print(__doc__ if argv != ['structure'] else 'structure needs a batch index: python3 .ua/tmp/ua_glue.py structure <i>')
        return 2
    if os.path.isdir(LEGACY):
        print('problem: this project has a legacy .understand-anything/ data folder; the plugin scripts and the Understand '
              'panel use it instead of .ua/, so the map would land in two places. Rename it first '
              '(run_bash `mv .understand-anything .understand-anything.old`), then rerun from step 2.')
        return 1
    try:
        if argv[0] == 'structure':
            return structure(argv[1])
        return commands[argv[0]]() or 0
    except FileNotFoundError as exc:
        name = os.path.basename(str(exc.filename or ''))
        print('problem: missing %s -- run %s first' % (rel(str(exc.filename)), STEP_OF.get(name, 'the step that writes it')))
        return 1
    except ValueError as exc:
        print('problem: a JSON file could not be read (%s) -- rewrite the file named in the error whole, then rerun' % exc)
        return 1
    except KeyError as exc:
        print('problem: a file lacks the field %s -- rerun the step that writes it' % exc)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
