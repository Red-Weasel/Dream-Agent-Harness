#!/usr/bin/env python3
"""Deterministic steps of Dream's `understand` skill. Copy me to .ua/tmp/ua_glue.py and run from the project root:
  python3 .ua/tmp/ua_glue.py status [--exclude "<p1,p2>"] [--include "<dir>"] [--full]
  python3 .ua/tmp/ua_glue.py imports-input | scan-result | batch-inputs | structure <i> | draft | nodes | assemble | finish
`status` comes first: the map is current (stop), out of date (update mode: only what changed is redone) or missing.
The `understand-domain` skill uses two more: domain-context | domain
Every problem is printed by name with the step that fixes it; a non-zero exit means the map is not done."""
import ast
import datetime
import glob
import hashlib
import json
import math
import os
import pathlib
import re
import subprocess
import sys
from collections import Counter

ROOT = os.getcwd()
UA = os.path.join(ROOT, '.ua')
LEGACY = os.path.join(ROOT, '.understand-anything')
TMP, INTER = os.path.join(UA, 'tmp'), os.path.join(UA, 'intermediate')
KG = os.path.join(UA, 'knowledge-graph.json')
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
           'layers.json': 'write_file layers.json (step 6)', 'tour.json': 'write_file tour.json (step 6)',
           'extract-out-all.json': 'the extract-structure.mjs command glue batch-inputs printed (step 3)'}
# ---- DREAM-106: map once, update after --------------------------------------------------------------------------------
STATE = os.path.join(UA, 'map-state.json')      # what the last finished map covered: file hashes, rules, the map's digests
RUN = os.path.join(TMP, 'run.json')             # this run: its id, mode, exclusions and skipped trees (status writes it)
HASHES = os.path.join(INTER, 'scan-hashes.json')      # the scanned files' hashes and rules (only scan-result writes it)
FINISHED = os.path.join(INTER, 'finished-graph.json')  # the last map finish passed, for an update after an unfinished run
DG = os.path.join(UA, 'domain-graph.json')
MAX_READ = 1 << 20                               # the draft reads at most this much of a file
DRAFT_OVER = 150                                 # more files to analyse than this: the big-repo (draft) path
KEY_MAX = 30                                     # the most key files the draft asks the model to summarise for real
DEFAULT_IGNORE = ('.ua/\n.understand-anything/\n.dream/\n.remember/\n*.lock\n*.pyc\n*.so\n*.dll\n*.bin\n*.sqlite*\n'
                  '*.wasm\n')
DREAM_EXCLUDES = ['.dream/**', '.remember/**']   # Dream's own state: excluded from every scan
SCAN = 'node "$UA_SKILLS/understand/scan-project.mjs" "$PWD" "$PWD/.ua/tmp/scan.json" --exclude-analysis-data --exclude "%s"'
BATCH_CHANGED = 'node "$UA_SKILLS/understand/compute-batches.mjs" "$PWD" --changed-files="$PWD/.ua/tmp/changed-files.json"'
EXTRACT_ALL = ('node "$UA_SKILLS/understand/extract-structure.mjs" "$PWD/.ua/tmp/extract-in-all.json" '
               '"$PWD/.ua/tmp/extract-out-all.json"')
UPDATE_TAIL = ('; then the merge (step 5), which joins them to the unchanged part of the map (batch-existing.json); then '
               'skip step 6 -- update mode keeps the layers and the tour -- and run glue assemble (step 7)')
HARD_SKIP = {'node_modules', '.git', '.svn', '.hg', '__pycache__'}      # scan-project.mjs's walk never enters these
BROWSER_DIR = re.compile(r'^(chromium|chromium_headless_shell|chromium-headless-shell|chromium-tip-of-tree|firefox|'
                         r'firefox-beta|webkit|ffmpeg)-\d+$')
# core's createIgnoreFilter, the scan's own matcher (defaults, .understandignore, --exclude), over paths on stdin
FILTER_JS = ("const [root, core, patterns] = process.argv.slice(1);"
             "const { createIgnoreFilter } = await import(core);"
             "const filter = createIgnoreFilter(root, JSON.parse(patterns));"
             "const chunks = []; for await (const c of process.stdin) chunks.push(c);"
             "process.stdout.write(Buffer.concat(chunks).toString('utf8').split('\\0')"
             ".filter(p => p && !filter.isIgnored(p)).join('\\0'));")


class Problem(Exception):
    """A named problem: main prints it and exits 1."""


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
    run = load(RUN, {})
    last = finished_map() if run.get('mode') == 'update' else None       # the last map finish passed
    update = last is not None
    prev, old_imports = (last['graph'], last.get('importMap') or {}) if update else ({}, {})
    if not scan['files']:
        print('nothing to map: the scan found no file to map -- every file is excluded, ignored or skipped. Stop here '
              'and tell the user; run status again for what was skipped and how to bring it back.')
        return 1
    proj = load(os.path.join(TMP, 'project.json'), {}) or (prev.get('project') or {})
    result = {'name': proj.get('name') or os.path.basename(ROOT),
              'description': proj.get('description') or 'No description available',
              'languages': sorted(scan['stats']['byLanguage']), 'frameworks': list(proj.get('frameworks') or []),
              'files': scan['files'], 'totalFiles': scan['totalFiles'], 'filteredByIgnore': scan['filteredByIgnore'],
              'estimatedComplexity': scan['estimatedComplexity'], 'importMap': imports['importMap']}
    save(os.path.join(INTER, 'scan-result.json'), result)
    cats = ', '.join('%s %d' % kv for kv in sorted(scan['stats']['byCategory'].items()))
    print('%d files (%s); %d excluded by the ignore rules; languages: %s' % (
        scan['totalFiles'], cats, scan['filteredByIgnore'], ', '.join(result['languages'])))
    print_trees(run.get('trees') or [])
    # the files as scanned now: what map-state.json records when the map is finished (edits after the scan show next
    # time). Its own file, which status never rewrites, so a status run mid-map cannot lose it.
    save(HASHES, {'run': run.get('runId'), 'mode': run.get('mode'), 'rules': rules_digest(run.get('excludes') or [],
                                                                                           run.get('include') or []),
                  'hashes': {f['path']: file_hash(f['path']) for f in scan['files']}})
    return prepare_update(result, prev, old_imports) if update else 0


def batch_inputs():
    update = load(RUN, {}).get('mode') == 'update'
    for stale in glob.glob(os.path.join(INTER, 'batch*.json')) + [os.path.join(INTER, 'assembled-graph.json')]:
        if os.path.exists(stale) and os.path.basename(stale) != 'batches.json' and not (
                update and os.path.basename(stale) == 'batch-existing.json'):    # the unchanged part of the map
            os.remove(stale)
    batches = load(os.path.join(INTER, 'batches.json'))
    files = [f for b in batches['batches'] for f in b['files']]
    binary = {f['path']: binary_kind(read_head(f['path'])) for f in files}      # never parsed or read as text
    if len(files) > DRAFT_OVER:                         # DREAM-106: one parser run and a drafted batch file per batch
        imports = {}
        for b in batches['batches']:
            imports.update(b.get('batchImportData') or {})
        save(os.path.join(TMP, 'extract-in-all.json'), {'projectRoot': ROOT, 'batchImportData': imports,
                                                        'batchFiles': [f for f in files if not binary[f['path']]]})
        if os.path.exists(os.path.join(INTER, 'summaries.json')):      # the last run's: written for the files as they were
            os.remove(os.path.join(INTER, 'summaries.json'))
        print('%d files in %d batches: the big-repo path. Skip step 4 -- the glue drafts every batch file from the parser '
              'and you write real summaries only for the key files it names.' % (len(files), batches['totalBatches']))
        print('next: run the parser once over every file:\n  %s\nthen glue draft%s.' % (EXTRACT_ALL, UPDATE_TAIL if update else ''))
        return 0
    for b in batches['batches']:
        i = b['batchIndex']
        save(os.path.join(TMP, 'extract-in-%d.json' % i), {'projectRoot': ROOT, 'batchImportData': b['batchImportData'],
                                                            'batchFiles': [f for f in b['files'] if not binary[f['path']]]})
        print('batch %d:' % i)
        for f in b['files']:
            imps = b['batchImportData'].get(f['path']) or []
            print('  %s (%s, %s lines, %s)%s%s' % (f['path'], f['language'], f['sizeLines'], f['fileCategory'],
                                                    ' imports: ' + ', '.join(imps) if imps else '',
                                                    ' -- binary (%s): do not read it; its summary is its type and size'
                                                    % binary[f['path']] if binary[f['path']] else ''))
        for path, near in (b.get('neighborMap') or {}).items():
            print('  %s is linked to other batches: %s' % (path, '; '.join(
                '%s [%s]' % (n['path'], ', '.join((n.get('symbols') or [])[:8])) for n in near)))
    print('%d batches: write .ua/intermediate/batch-<i>.json for each' % batches['totalBatches'])
    if update:
        print('update mode: only these files changed. next: analyse them (step 4)%s.' % UPDATE_TAIL)
    return 0


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
        if not m and name != 'batch-existing.json':        # update mode's unchanged part of the map (the glue writes it)
            problems.append('%s is not a name the merge reads (batch-<i>.json or batch-<i>-part-<k>.json) -- rename it, rerun step 5' % name)
            continue
        try:
            data = load(path)
        except ValueError as exc:
            problems.append('%s is not valid JSON (%s) -- rewrite it whole (step 4), rerun step 5' % (name, exc))
            continue
        if not isinstance(data, dict) or not isinstance(data.get('nodes'), list) or not isinstance(data.get('edges'), list):
            problems.append('%s lacks the "nodes" and "edges" lists -- rewrite it whole (step 4), rerun step 5' % name)
        if m:
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
    if len(file_nodes(g)) > DRAFT_OVER:
        print_folders(file_nodes(g))
    else:
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


def expand_layer_paths(layers, path_by_id):
    """Layer members named by folder (DREAM-106, for maps too big to list): "paths": ["src/api/"] takes every file-level
    node under that folder and "." every one. A file named in any layer's nodeIds stays there; otherwise the longest
    matching path wins, the first layer on a tie. Returns {layer id: [node ids its paths took]}."""
    named = {x for L in layers for i in (L.get('nodeIds') or L.get('nodes') or []) for x in (str(i), 'file:' + str(i))}
    out = {str(L.get('id')): [] for L in layers}
    for nid, path in path_by_id.items():
        best = None
        for L in [] if nid in named else layers:
            for p in L.get('paths') or []:
                p = str(p).strip().strip('/')
                length = 0 if p in ('', '.') else len(p) + 1 if path == p or path.startswith(p + '/') else -1
                if length >= 0 and (best is None or length > best[0]):
                    best = (length, str(L.get('id')))
        if best:
            out[best[1]].append(nid)
    return out


def written_layers(g, ids, problems):
    raw = []
    for k, L in enumerate(as_list(load(os.path.join(INTER, 'layers.json'), []), 'layers'), 1):
        name = L.get('name') or L.get('id') or 'Layer %d' % k
        lid = str(L.get('id') or 'layer:' + kebab(name))
        raw.append(dict(L, id=lid if lid.startswith('layer:') else 'layer:' + kebab(lid), name=name))
    by_path = expand_layer_paths(raw, {n['id']: node_path(n) for n in file_nodes(g)})
    return [{'id': L['id'], 'name': L['name'], 'description': L.get('description') or '',
             'nodeIds': node_ids(L.get('nodeIds', L.get('nodes')), ids, problems, 'layer ' + L['id']) + by_path[L['id']]}
            for L in raw]


def written_tour(ids, problems):
    tour = []
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
    return tour


def assemble():
    g, scan = load(os.path.join(INTER, 'assembled-graph.json')), load(os.path.join(INTER, 'scan-result.json'))
    last = finished_map() if load(RUN, {}).get('mode') == 'update' else None
    update, notes, prev = last is not None, [], (last or {}).get('graph') or {}
    kept = set()
    if update:                                          # DREAM-106: the unchanged files' nodes exactly as the last finish had them
        g, kept = carry_over(g, prev, scan, notes)
    for n in g['nodes']:                                # the merge's "tested" can make a sixth tag: keep 3-5, "tested" too
        tags = n.get('tags')
        if n.get('id') not in kept and isinstance(tags, list) and len(tags) > 5:
            n['tags'] = [t for t in tags if t != 'tested'][:4] + ['tested'] if 'tested' in tags else tags[:5]
    ids = {n['id'] for n in g['nodes']}
    problems = batch_problems() + coverage_problems(g, scan)
    layers, tour = carried_layers_and_tour(prev, g, notes) if update else ([], [])
    layers = layers or written_layers(g, ids, problems)
    tour = tour or written_tour(ids, problems)
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
    for note in notes:
        print(note)
    rc = report(graph, problems + check_graph(graph, NODE_TYPES, EDGE_TYPES, True))
    if update and not rc:
        print('next: the fingerprints command (step 7), then glue finish (step 9) -- update mode keeps the domain graph, '
              'so skip step 8 unless finish names a step to fix.')
    return rc


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
    kg, scan = load(kg_path), load(os.path.join(INTER, 'scan-result.json'), {})
    problems = batch_problems() + coverage_problems(kg, scan)
    problems += check_graph(kg, NODE_TYPES, EDGE_TYPES, True)
    mapped = {f['path'] for f in scan.get('files') or []}
    src, dg_path = os.path.join(INTER, 'domain.json'), os.path.join(UA, 'domain-graph.json')
    run = load(RUN, {})
    if run.get('mode') == 'update' and not os.path.isfile(src) and os.path.isfile(dg_path):
        kept = load(dg_path)                            # update mode keeps the domain graph: give the model a file to fix
        save(src, {'nodes': kept.get('nodes') or [], 'edges': kept.get('edges') or []})
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
            fp = n.get('filePath')
            if n.get('type') == 'step' and isinstance(fp, str) and fp and (mapped and fp not in mapped or not mapped
                                                                            and not os.path.isfile(os.path.join(ROOT, fp))):
                problems.append('domain-graph: step %s: its file %s is not in the map (deleted, excluded or skipped) -- '
                                'in .ua/intermediate/domain.json point the step at a mapped file that does it now, or '
                                'remove the step and its flow_step edge (step 8), then rerun finish' % (n.get('id'), fp))
    else:
        problems.append('no domain graph: write .ua/intermediate/domain.json (step 8)')
    overlay = load(os.path.join(UA, 'diff-overlay.json'), {})
    if not all(isinstance(overlay.get(k), list) for k in ('changedNodeIds', 'affectedNodeIds')):
        problems.append('diff-overlay.json is malformed: run `assemble` again')
    meta = load(os.path.join(UA, 'meta.json'), {})
    problems += ['meta.json lacks %s: run `assemble` again' % k
                 for k in ('lastAnalyzedAt', 'gitCommitHash', 'version', 'analyzedFiles') if k not in meta]
    rc = report(kg, problems)
    if not rc:
        record_state(kg, run)
    return rc


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


# ---- DREAM-106: status first; update mode redoes only what changed ---------------------------------------------------
# Change detection is the glue's own, not upstream's prepare-incremental.mjs: that one needs git, refuses uncommitted
# changes and diffs commits, while Dream's users edit without committing and map folders with no git at all. `status`
# rebuilds the scan's file list (git ls-files or the scan's walk, then core's own ignore filter) and compares every file's
# SHA-256 with .ua/map-state.json, which `finish` writes when a map passes every check.

def ua_skills():
    return os.environ.get('UA_SKILLS') or os.path.expanduser('~/.understand-anything/repo/understand-anything-plugin/skills')


def split_patterns(text):
    return [p.strip() for p in str(text).split(',') if p.strip()]


def bare(pattern):
    """A folder named by the user or a pattern, compared without slashes or a trailing /**: tools/pylibs."""
    p = str(pattern).strip().removeprefix('./').strip('/')
    return p[:-3].rstrip('/') if p.endswith('/**') else p


def status_args(argv):
    opts, rest = {'exclude': [], 'include': [], 'full': False}, list(argv)
    while rest:
        arg = rest.pop(0)
        key, eq, value = arg.partition('=')
        if key in ('--exclude', '--include'):
            if not eq:
                if not rest:
                    raise Problem('%s needs a value, e.g. %s "tests/,docs/"' % (key, key))
                value = rest.pop(0)
            opts[key[2:]] += split_patterns(value)
        elif arg == '--full':
            opts['full'] = True
        else:
            raise Problem('status takes --exclude "<p1,p2>", --include "<dir>" and --full, not %s' % arg)
    return opts


def listed_files():
    """The scan's candidates: `git ls-files -co --exclude-standard` in a repository, else its walk (scan-project.mjs)."""
    try:
        run = subprocess.run(['git', 'ls-files', '-z', '-co', '--exclude-standard'], cwd=ROOT, capture_output=True,
                             timeout=120)
        if run.returncode == 0 and run.stdout:
            return [p for p in run.stdout.decode('utf-8', 'replace').split('\0') if p]
    except (OSError, subprocess.SubprocessError):
        pass
    out = []
    for top, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in HARD_SKIP and not os.path.islink(os.path.join(top, d))]
        for name in files:
            path = rel(os.path.join(top, name)).replace(os.sep, '/')
            try:
                path.encode('utf-8')        # a name that is not UTF-8: the scan reads it as U+FFFD, cannot open it, skips it
            except UnicodeEncodeError:
                continue
            out.append(path)
    return out


def ignore_filter(paths, patterns):
    core = os.path.normpath(os.path.join(ua_skills(), '..', 'packages', 'core', 'dist', 'index.js'))
    if not os.path.isfile(core):
        raise Problem('the plugin core is not built (%s is missing); the scan needs it too' % core)
    try:
        run = subprocess.run(['node', '--input-type=module', '-e', FILTER_JS, ROOT, pathlib.Path(core).as_uri(),
                              json.dumps(patterns)], input='\0'.join(paths).encode('utf-8'), capture_output=True,
                             timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Problem('node could not apply the ignore rules (%s)' % exc)
    if run.returncode != 0:
        raise Problem('the ignore rules could not be applied: %s' % run.stderr.decode('utf-8', 'replace').strip()[-400:])
    return [p for p in run.stdout.decode('utf-8', 'replace').split('\0') if p]


def in_scope(excludes):
    """Every file the scan would map with these user exclusions, before installed trees are skipped."""
    paths = [p for p in listed_files() if not p.startswith(('.ua/', '.understand-anything/'))]
    kept = ignore_filter(paths, DREAM_EXCLUDES + excludes)
    full = {p: os.path.join(ROOT, p) for p in kept}
    return sorted(p for p, f in full.items() if os.path.isfile(f) and not os.path.islink(f) and os.access(f, os.R_OK))


def install_reason(folder):
    """Why a folder holds installed dependencies rather than the project's code, or ''. Deterministic: markers only."""
    name, full = os.path.basename(folder), os.path.join(ROOT, folder)
    if name in ('site-packages', 'dist-packages'):
        return 'installed Python packages (%s)' % name
    if name == 'ms-playwright':
        return 'Playwright browsers (ms-playwright)'
    if os.path.isfile(os.path.join(full, 'pyvenv.cfg')):
        return 'a Python virtual environment (pyvenv.cfg)'
    if os.path.isdir(os.path.join(full, 'conda-meta')):
        return 'a conda environment (conda-meta)'
    if os.path.isfile(os.path.join(full, 'INSTALLATION_COMPLETE')):
        return 'a Playwright browser install (INSTALLATION_COMPLETE)'
    try:
        entries = list(os.scandir(full))
    except OSError:
        return ''
    subdirs = [e for e in entries if e.is_dir(follow_symlinks=False)]
    # a *.dist-info, or an *.egg-info an installer recorded -- a project's own setuptools metadata has neither
    installed = [e for e in subdirs if e.name.endswith('.dist-info') or e.name.endswith('.egg-info') and any(
        os.path.isfile(os.path.join(e.path, f)) for f in ('installed-files.txt', 'RECORD'))]
    if installed:
        return 'installed Python packages (%d *.dist-info / *.egg-info)' % len(installed)
    # a browsers folder: nothing in it but browser install dirs (and Playwright's .links), at least one carrying its
    # marker. A lone firefox-115 beside project files is not one; a marked install dir is skipped on its own.
    others = [e for e in entries if e.name != '.links']
    marked = [e for e in subdirs if os.path.isfile(os.path.join(e.path, 'INSTALLATION_COMPLETE'))]
    if marked and all(e.is_dir(follow_symlinks=False) and (BROWSER_DIR.match(e.name) or e in marked) for e in others):
        return 'Playwright browsers (%s)' % ', '.join(sorted(e.name for e in others)[:4])
    return ''


def installed_trees(files):
    """The outermost folders of installed packages and browsers among the files, each with its file count."""
    folders = sorted({'/'.join(p.split('/')[:k]) for p in files for k in range(1, p.count('/') + 1)},
                     key=lambda d: (d.count('/'), d))
    roots = []
    for d in folders:
        if not any(d.startswith(r + '/') for r, _ in roots):
            why = install_reason(d)
            if why:
                roots.append((d, why))
    return [{'path': r, 'reason': why, 'files': sum(1 for p in files if p.startswith(r + '/'))} for r, why in roots]


def file_hash(path, method='bytes'):
    """SHA-256 of a project file; 'text' hashes it as the plugin's fingerprints do (decoded UTF-8)."""
    try:
        with open(os.path.join(ROOT, path), 'rb') as f:
            data = f.read()
    except OSError:
        return None
    return hashlib.sha256(data.decode('utf-8', 'replace').encode('utf-8') if method == 'text' else data).hexdigest()


def rules_digest(excludes, include):
    texts = {}
    for name in ('.ua/.understandignore', '.understandignore'):
        try:
            with open(os.path.join(ROOT, name), encoding='utf-8', errors='replace') as f:
                texts[name] = f.read()
        except OSError:
            texts[name] = None
    rules = {'ignore files': texts, 'exclude': sorted(set(excludes)), 'include': sorted(set(include))}
    return hashlib.sha256(json.dumps(rules, sort_keys=True).encode('utf-8')).hexdigest()


def file_digest(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def baseline():
    """(files -> hash, hash method, state) of the last finished map, from .ua/map-state.json; None without one."""
    state = load(STATE, {})
    if isinstance(state, dict) and isinstance(state.get('files'), dict):
        return state['files'], state.get('hash') or 'bytes', state
    return None


def legacy_state():
    """A map built before DREAM-106 has no .ua/tmp/run.json and no .ua/map-state.json (a DREAM-106 run leaves run.json,
    and its own step 7 writes fingerprints.json too): its baseline is the plugin's fingerprints.json, hashed as text."""
    if any(os.path.exists(p) for p in (RUN, STATE, HASHES, FINISHED)) or not os.path.isfile(KG):
        return None
    prints = load(os.path.join(UA, 'fingerprints.json'), {})
    files = prints.get('files') if isinstance(prints, dict) else None
    if not isinstance(files, dict) or not files:
        return None
    meta = load(os.path.join(UA, 'meta.json'), {})
    return {'version': 1, 'legacy': True, 'hash': 'text', 'run': None, 'builtAt': meta.get('lastAnalyzedAt'),
            'gitCommitHash': meta.get('gitCommitHash'), 'graphDigest': file_digest(KG), 'domainDigest': file_digest(DG),
            'files': {p: (f or {}).get('contentHash') for p, f in files.items()}}


def keep_finished(state, graph, import_map):
    """Record a finished map: the state status compares with, and a copy of the graph an update starts from even when a
    later, unfinished run has rewritten .ua/knowledge-graph.json."""
    save(FINISHED, {'graphDigest': state['graphDigest'], 'importMap': import_map, 'graph': graph})
    save(STATE, state)


def finished_map():
    """{'graph', 'importMap'} of the last map finish passed: its copy, or .ua/knowledge-graph.json while that is still the
    same file; None when neither matches the digest map-state.json recorded."""
    state = load(STATE, {})
    digest = state.get('graphDigest') if isinstance(state, dict) else None
    if not digest:
        return None
    copy = load(FINISHED, {})
    if isinstance(copy, dict) and copy.get('graphDigest') == digest and isinstance(copy.get('graph'), dict):
        return copy
    if file_digest(KG) == digest:
        return {'graph': load(KG), 'importMap': load(os.path.join(INTER, 'scan-result.json'), {}).get('importMap') or {}}
    return None


def exclude_arg(patterns):
    """--exclude's value inside double quotes: gitignore specials in folder names escaped, then the shell's."""
    text = ','.join(patterns)
    for c in '\\"$`':
        text = text.replace(c, '\\' + c)
    return text


def tree_pattern(path):
    return '/%s/' % re.sub(r'([*?\[\]\\])', r'\\\1', path)


def print_trees(trees):
    skipped = [t for t in trees if not t.get('kept')]
    if skipped:
        print('skipped installed dependency trees (not mapped; if the user names one, rerun status with --include "<dir>"):')
        for t in skipped:
            print('  %s -- %d files: %s' % (t['path'], t['files'], t['reason']))
    for t in trees:
        if t.get('kept'):
            print('mapped at the user\'s request (--include): %s -- %d files: %s' % (t['path'], t['files'], t['reason']))


def listing(items, limit=12):
    return ', '.join(items[:limit]) + (' ... %d more' % (len(items) - limit) if len(items) > limit else '')


def status(argv):
    opts = status_args(argv)
    ignore = os.path.join(UA, '.understandignore')
    wrote = not os.path.isfile(ignore)
    if wrote:                                            # the default excludes, before anything is compared or scanned
        os.makedirs(UA, exist_ok=True)
        with open(ignore, 'w', encoding='utf-8') as f:
            f.write(DEFAULT_IGNORE)
    persisted = load(STATE, {})
    dropped = {bare(p) for p in opts['exclude']}        # a later --exclude wins over an earlier --include
    include = [i for i in dict.fromkeys(bare(p) for p in (persisted.get('include') or []) + opts['include'] if bare(p))
               if i not in dropped]
    excludes = [p for p in dict.fromkeys((persisted.get('excludes') or []) + opts['exclude'])  # Dream's own are always there
                if bare(p) not in {bare(d) for d in DREAM_EXCLUDES} and bare(p) not in include]
    candidates = in_scope(excludes)
    trees = installed_trees(candidates)
    for t in trees:                                     # the user brings a tree back by naming it; the scan cannot take a comma
        t['kept'] = t['path'] in include or os.path.basename(t['path']) in include or ',' in t['path']
    include = [i for i in include if any(i in (t['path'], os.path.basename(t['path'])) for t in trees)]  # only what acts
    skipped = [t['path'] for t in trees if not t['kept']]
    inventory = [p for p in candidates if not any(p.startswith(s + '/') for s in skipped)]
    if not inventory:
        return nothing_to_map(trees, wrote)
    rules = rules_digest(excludes, include)
    command = SCAN % exclude_arg(DREAM_EXCLUDES + excludes + [tree_pattern(s) for s in skipped])
    mode, why, state = choose_mode(opts)
    if state:
        files, method = state['files'], state.get('hash') or 'bytes'
        now = set(inventory)
        edited = [p for p in inventory if p in files and file_hash(p, method) != files[p]]
        added = [p for p in inventory if p not in files]
        gone = [p for p in sorted(files) if p not in now]
        rules_changed = state.get('rules') not in (None, rules)
        built = '%s (%s)' % (state.get('builtAt') or 'at an unknown time', 'git %s' % str(state['gitCommitHash'])[:7]
                             if state.get('gitCommitHash') not in (None, '', 'none') else 'no git commit')
        if mode == 'compare' and not (edited or added or gone or rules_changed):
            return report_current(built, state, len(inventory), trees, wrote)
        if state.get('legacy') and not os.path.exists(STATE):   # adopt the map from before DREAM-106 once, as finished
            keep_finished(state, load(KG), load(os.path.join(INTER, 'scan-result.json'), {}).get('importMap') or {})
        mode = 'update'
    save(RUN, {'runId': datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-%d' % os.getpid(),
               'mode': mode, 'excludes': excludes, 'include': include, 'trees': trees})
    if wrote:
        print('wrote .ua/.understandignore (the default excludes)')
    if state:
        removed = [p for p in gone if not os.path.lexists(os.path.join(ROOT, p))]
        left = [p for p in gone if p not in removed]
        print('update mode: %sthe map built %s is out of date: %d edited, %d added, %d deleted%s%s.' % (
            why + ', so this run starts again from the last finished map; ' if why else '', built, len(edited), len(added),
            len(removed), ', %d left the map\'s scope' % len(left) if left else '',
            '; the ignore rules changed' if rules_changed else ''))
        for label, items in (('edited', edited), ('added', added), ('deleted', removed)):
            if items:
                print('  %s: %s' % (label, listing(items)))
        for s in skipped:                               # a whole installed tree that left: one line, not its files
            inside = {p for p in left if p.startswith(s + '/')}
            if inside:
                print('  left the scope: %s -- %d files, now skipped as an installed dependency tree' % (s, len(inside)))
                left = [p for p in left if p not in inside]
        if left:
            print('  left the scope (now ignored or excluded): %s' % listing(left))
        print('Only these are redone: every other file\'s nodes, the layers, the tour and the domain graph carry over.')
    else:
        print('%s -- the full map, steps 1-9.' % why)
    print_trees(trees)
    print('%d files to map. next: %sscan (step 2) with exactly this command:\n  %s\nthen glue imports-input, the import '
          'map and glue scan-result (step 2).' % (len(inventory), '' if state else 'write .ua/tmp/project.json (step 1), then ',
                                                   command))
    return 0


def choose_mode(opts):
    """('full' | 'update' | 'compare', why, the finished map's state). A map counts only once finish passed it: a run
    that started after the last finish (its scan ran; status alone only plans), or map files that are not the ones
    finish checked (their digests), are never current."""
    if not os.path.isfile(KG):
        return 'full', 'no map yet', None
    if opts['full']:
        return 'full', 'no map kept: a full rebuild, as asked (--full)', None
    legacy = legacy_state()
    if legacy:
        return 'compare', '', legacy
    scanned, state = load(HASHES, {}), (baseline() or (None, None, None))[2]
    if not state:
        return 'full', 'no map record%s' % (': the last run did not finish, and a map counts only once finish passes '
                                            'it' if os.path.exists(RUN) else ' to compare with (.ua/map-state.json)'), None
    unfinished = bool(scanned.get('run')) and scanned.get('run') != state.get('run')
    if unfinished and scanned.get('mode') == 'full':
        return 'full', 'no map kept: the last run, a full map, did not finish', None
    touched = file_digest(KG) != state.get('graphDigest') or file_digest(DG) != state.get('domainDigest')
    if not (unfinished or touched):
        return 'compare', '', state
    if finished_map() is None:
        return 'full', 'no map kept: the last run did not finish and the last finished map is gone', None
    return 'update', ('the last run did not finish' if unfinished else
                      'the map files are not the ones the last finish checked'), state


def nothing_to_map(trees, wrote):
    print('nothing to map: no file is in scope -- every file is %s.' % (
        'excluded, ignored or inside a skipped installed dependency tree' if any(not t.get('kept') for t in trees)
        else 'excluded or ignored'))
    if wrote:
        print('wrote .ua/.understandignore (the default excludes)')
    print_trees(trees)
    print('Stop here: tell the user there is nothing to map and why. If they name a skipped folder, run status again '
          'with --include "<dir>".')
    return 0


def report_current(built, facts, count, trees, wrote):
    """The map matches the files: say when it was built and what it covers, and write nothing."""
    cats = ', '.join('%s %d' % kv for kv in sorted((facts.get('byCategory') or {}).items()))
    parts = ['%s %s' % (facts[k], k) for k in ('nodes', 'edges', 'layers') if isinstance(facts.get(k), int)]
    if isinstance(facts.get('tour'), int):
        parts.append('%d tour steps' % facts['tour'])
    print('The map is current: built %s from %d files%s%s.' % (built, count, ' (%s)' % cats if cats else '',
                                                               '; ' + ', '.join(parts) if parts else ''))
    print('Nothing changed since: no file edited, added or deleted, and the same ignore rules.%s' % (
        ' (wrote the missing .ua/.understandignore with the default excludes)' if wrote else ''))
    print_trees(trees)
    print('Stop here: tell the user the map is current -- when it was built and what it covers. Nothing else runs. '
          '(Only if the user asks for a full rebuild: status --full.)')
    return 0


def carried(n, paths, todo):
    """True for a node of an unchanged file that is still scanned, or one tied to no file."""
    if n.get('type') in FILE_TYPES + ('function', 'class') or n.get('filePath'):
        return node_path(n) in paths and node_path(n) not in todo
    return True


def prepare_update(result, prev, old_imports):
    """After the update's scan: the files to analyse (new, or not as recorded), the previous map without them and
    without what is gone as batch-existing.json for the merge, and what to run next."""
    files, method, _ = baseline() or ({}, 'bytes', {})
    paths = [f['path'] for f in result['files']]
    todo = sorted(p for p in paths if p not in files or file_hash(p, method) != files[p])
    gone = sorted(set(files) - set(paths))
    for stale in glob.glob(os.path.join(INTER, 'batch*.json')) + [os.path.join(INTER, 'assembled-graph.json')]:
        if os.path.exists(stale):
            os.remove(stale)
    save(os.path.join(TMP, 'changed-files.json'), todo)
    save(os.path.join(INTER, 'batch-existing.json'), carried_graph(prev, set(paths), set(todo), old_imports,
                                                                   result['importMap']))
    print('update mode: %d to analyse (%d edited, %d added); %d deleted from the map' % (
        len(todo), sum(1 for p in todo if p in files), sum(1 for p in todo if p not in files), len(gone)))
    if todo:
        print('next: batches for these files only:\n  %s\nthen glue batch-inputs (step 3).' % BATCH_CHANGED)
    else:
        save(os.path.join(INTER, 'batches.json'), {'schemaVersion': 1, 'totalBatches': 0, 'batches': []})
        print('nothing to analyse: skip steps 3-4. next: the merge (step 5), then glue assemble (step 7).')
    return 0


def carried_graph(prev, paths, todo, old_imports, imports):
    """The previous map's unchanged part. An unchanged file's import edge goes only when the import stopped resolving
    there (a file was added or deleted); the merge adds the new ones from the scan's import map."""
    keep = {n['id']: n for n in prev.get('nodes') or [] if carried(n, paths, todo)}
    edges = []
    for e in prev.get('edges') or []:
        src, tgt = keep.get(e.get('source')), keep.get(e.get('target'))
        if src is None or tgt is None:
            continue
        if e.get('type') == 'imports' and node_path(tgt) in set(old_imports.get(node_path(src)) or []) - set(
                imports.get(node_path(src)) or []):
            continue
        edges.append(e)
    return {'nodes': list(keep.values()), 'edges': edges}


def carry_over(g, prev, scan, notes):
    """Update mode: the merged graph with every unchanged file's nodes exactly as the previous map had them, plus the
    previous edges from those nodes into re-analysed files whose targets still exist."""
    paths = {f['path'] for f in scan['files']}
    todo = set(load(os.path.join(TMP, 'changed-files.json'), []))
    old = {n['id']: n for n in prev.get('nodes') or []}
    keep = {i for i, n in old.items() if carried(n, paths, todo)}
    nodes = [old[n['id']] if n.get('id') in keep else n for n in g['nodes']]
    ids = {n.get('id') for n in nodes}
    have = {(e.get('source'), e.get('target'), e.get('type')) for e in g['edges']}
    edges = list(g['edges'])
    for e in prev.get('edges') or []:
        key = (e.get('source'), e.get('target'), e.get('type'))
        if e.get('source') in keep and e.get('target') not in keep and e.get('target') in ids and key not in have:
            edges.append(e)
            have.add(key)
    gone = sorted({node_path(n) for i, n in old.items() if i not in keep and node_path(n) not in todo})
    notes.append('update: carried over %d nodes of %d unchanged files verbatim; re-analysed %d files; removed %d: %s' % (
        len(keep), len({node_path(old[i]) for i in keep}), len(todo), len(gone), listing(gone) or '-'))
    return dict(g, nodes=nodes, edges=edges), keep


def shared_dirs(a, b):
    da, db = a.split('/')[:-1], b.split('/')[:-1]
    k = 0
    while k < min(len(da), len(db)) and da[k] == db[k]:
        k += 1
    return k


def carried_layers_and_tour(prev, g, notes):
    """The previous layers and tour: ids that are gone are dropped, a new file joins the layer of its nearest folder
    (then the layer it has the most edges with, then the first)."""
    ids = {n['id'] for n in g['nodes']}
    path = {n['id']: node_path(n) for n in g['nodes']}
    layers = []
    for L in prev.get('layers') or []:
        members = [i for i in L.get('nodeIds') or [] if i in ids]
        dropped = [i for i in L.get('nodeIds') or [] if i not in ids]
        if dropped:
            notes.append('dropped from layer %s: %s' % (L.get('name'), listing(dropped)))
        if members:
            layers.append(dict(L, nodeIds=members))
        elif L.get('nodeIds'):
            notes.append('layer %s lost all its files and was removed' % L.get('name'))
    placed = {i for L in layers for i in L['nodeIds']}
    for n in file_nodes(g):
        if n['id'] in placed or not layers:
            continue
        linked = Counter(e.get('target') if e.get('source') == n['id'] else e.get('source') for e in g['edges']
                         if n['id'] in (e.get('source'), e.get('target')))
        best = max(layers, key=lambda L: (max((shared_dirs(path[n['id']], path.get(i, '')) for i in L['nodeIds']), default=-1),
                                          sum(linked[i] for i in L['nodeIds'])))
        best['nodeIds'].append(n['id'])
        notes.append('placed %s in layer %s' % (n['id'], best.get('name')))
    tour = []
    for s in prev.get('tour') or []:
        kept = [i for i in s.get('nodeIds') or [] if i in ids]
        if kept:
            tour.append(dict(s, order=len(tour) + 1, nodeIds=kept))
        else:
            notes.append('tour step "%s" lost all its nodes and was removed' % s.get('title'))
    return layers, tour


def record_state(kg, run):
    """What this finished map covers, for the next `status`: each scanned file's hash as scan-result took it, the
    ignore rules and exclusions, the skipped trees, the run that finished and digests of the two map files it checked."""
    scan, taken = load(os.path.join(INTER, 'scan-result.json'), {}), load(HASHES, {})
    hashes = taken.get('hashes') or {}
    files = {f['path']: hashes.get(f['path']) or file_hash(f['path']) for f in scan.get('files') or []}
    project = kg.get('project') or {}
    keep_finished({'version': 1, 'run': taken.get('run'), 'graphDigest': file_digest(KG), 'domainDigest': file_digest(DG),
                   'builtAt': project.get('analyzedAt'), 'gitCommitHash': project.get('gitCommitHash'), 'files': files,
                   'rules': taken.get('rules') or rules_digest(run.get('excludes') or [], run.get('include') or []),
                   'excludes': run.get('excludes') or [], 'include': run.get('include') or [],
                   'skipped': [t for t in run.get('trees') or [] if not t.get('kept')],
                   'byCategory': dict(Counter(f.get('fileCategory') for f in scan.get('files') or [])),
                   'nodes': len(kg.get('nodes') or []), 'edges': len(kg.get('edges') or []),
                   'layers': len(kg.get('layers') or []), 'tour': len(kg.get('tour') or [])},
                  kg, scan.get('importMap') or {})
    print('recorded .ua/map-state.json (%d files): the next status tells whether anything changed.' % len(files))


def print_folders(fnodes):
    """nodes for a map too big to list file by file: its folders, then the root files and the draft's key files."""
    for depth in (2, 1):
        groups, top = {}, []
        for n in fnodes:
            parts = node_path(n).split('/')
            if len(parts) == 1:
                top.append(n)
            else:
                groups.setdefault('/'.join(parts[:min(depth, len(parts) - 1)]) + '/', []).append(n)
        if len(groups) <= 40:
            break
    print('%d file-level nodes, too many to list one by one, so by folder. Put every file in exactly one layer: in '
          'layers.json "paths": ["<folder>/"] takes a whole folder (the longest matching path wins, "." takes the rest) '
          'and "nodeIds" single files.' % len(fnodes))
    for folder, members in sorted(groups.items()):
        print('  %s (%d files): %s' % (folder, len(members), listing([os.path.basename(node_path(m)) for m in members], 4)))
    for n in top[:20]:
        print('  %s -- %s' % (n['id'], flat(n.get('summary') or '', 90)))
    keys = load(RUN, {}).get('keyFiles') or []
    if keys:
        print('key files (tour candidates): ' + listing(['%s' % p for p in keys], 10))


# ---- DREAM-106: the big-repo path -- every batch drafted from the parser, real summaries for the key files ---------------
TEST_PATH = re.compile(r'(^|/)(tests?|__tests__|spec|specs)/|(^|/)test_[^/]*$|_test\.[^/.]+$|\.(test|spec)\.[^/.]+$')
ENTRY_NAMES = {'main.py', '__main__.py', 'cli.py', 'app.py', 'manage.py', 'wsgi.py', 'asgi.py', 'server.py', 'run.py',
               'index.js', 'index.mjs', 'index.cjs', 'index.ts', 'index.tsx', 'index.jsx', 'main.js', 'main.ts', 'main.go',
               'main.rs', 'lib.rs', 'main.c', 'main.cpp', 'Program.cs', 'Main.java', 'Main.kt', 'main.swift'}
HASH_COMMENTS = {'python', 'shell', 'ruby', 'yaml', 'toml', 'config', 'dockerfile', 'makefile', 'r', 'perl', 'elixir',
                 'powershell', 'terraform', 'graphql', 'properties', 'procfile', 'vagrantfile', 'protobuf'}
DASH_COMMENTS = {'lua', 'sql', 'haskell'}
LICENCE = re.compile(r'copyright|licen[cs]e|spdx|all rights reserved|\(c\) \d{4}', re.I)
LANG_NAMES = {'javascript': 'JavaScript', 'typescript': 'TypeScript', 'csharp': 'C#', 'cpp': 'C++', 'json': 'JSON',
              'jsonc': 'JSON', 'yaml': 'YAML', 'toml': 'TOML', 'html': 'HTML', 'css': 'CSS', 'sql': 'SQL', 'xml': 'XML',
              'php': 'PHP', 'c': 'C', 'go': 'Go', 'r': 'R'}
WHAT = {'code': 'source', 'script': 'script', 'markup': 'markup', 'config': 'configuration', 'docs': 'document',
        'infra': 'infrastructure file', 'data': 'data file'}


def node_type(entry):
    """The skill's category -> node type rule (step 4), as the model applies it."""
    cat, path = entry.get('fileCategory'), entry['path']
    if cat in ('config', 'docs'):
        return {'config': 'config', 'docs': 'document'}[cat]
    if cat == 'infra':
        return ('pipeline' if path.startswith(('.github/workflows/', '.circleci/')) or path.endswith(('.gitlab-ci.yml', 'Jenkinsfile'))
                else 'resource' if path.endswith(('.tf', '.tfvars')) else 'service')
    if cat == 'data':
        return 'schema' if path.endswith(('.graphql', '.gql', '.proto', '.prisma')) else 'table'
    return 'file'


def read_head(path, size=8192):
    try:
        with open(os.path.join(ROOT, path), 'rb') as f:
            return f.read(size)
    except OSError:
        return b''


def binary_kind(head):
    """What a binary file is, from its first bytes (a NUL, or an executable's magic), or '' for text."""
    for magic, kind in ((b'\x7fELF', 'ELF executable or library'), (b'\xca\xfe\xba\xbe', 'Mach-O or Java class'),
                        (b'\xfe\xed\xfa', 'Mach-O binary'), (b'\xce\xfa\xed\xfe', 'Mach-O binary'),
                        (b'\xcf\xfa\xed\xfe', 'Mach-O binary'), (b'MZ', 'Windows executable (PE)')):
        if head.startswith(magic) and (magic != b'MZ' or b'\0' in head):
            return kind
    return 'binary data' if b'\0' in head else ''


def read_text(path):
    """A file's text, at most MAX_READ bytes of it: its description is at the top, and a whole binary never loads."""
    try:
        with open(os.path.join(ROOT, path), 'rb') as f:
            return f.read(MAX_READ).decode('utf-8', 'replace')
    except OSError:
        return ''


def sentence(text, limit=220):
    """At most two sentences of flattened text, ending with a full stop."""
    text = ' '.join(str(text).split())
    m = re.match(r'(.+?[.!?])(?:\s+(.+?[.!?]))?(?=\s|$)', text)
    if m:
        text = m.group(1) + (' ' + m.group(2) if m.group(2) else '')
    text = flat(text, limit)
    return text if text.endswith(('.', '!', '?')) else text + '.'


def first_paragraph(text):
    return re.split(r'\n\s*\n', str(text).strip(), maxsplit=1)[0]


def lang_name(language, path):
    language = language if language and language != 'unknown' else os.path.splitext(path)[1][1:] or 'text'
    return LANG_NAMES.get(language, language[:1].upper() + language[1:])


def comment_prefix(language):
    return '#' if language in HASH_COMMENTS else '--' if language in DASH_COMMENTS else '//'


def comment_lines(lines, i, language):
    """The comment block starting at line i: (its text lines, the index after it)."""
    out = []
    if i < len(lines) and lines[i].lstrip().startswith(('/*', '<!--')):
        closer = '*/' if lines[i].lstrip().startswith('/*') else '-->'
        while i < len(lines):
            line, i = lines[i], i + 1
            out.append(re.sub(r'^\s*(/\*+|<!--|\*(?!/))\s?', '', line.split(closer)[0]).strip())
            if closer in line:
                break
        return [x for x in out if x], i
    prefix = comment_prefix(language)
    while i < len(lines) and lines[i].lstrip().startswith(prefix) and not lines[i].lstrip().startswith(('#!', '#include',
                                                                                                        '#define', '#pragma')):
        text = re.sub(r'^\s*(//+!?|#+!?|--+)\s?', '', lines[i]).strip()
        if not re.match(r'-\*-|vim?:|eslint|@ts-|prettier-ignore|type:', text):
            out.append(text)
        i += 1
    return [x for x in out if x], i


def leading_comment(language, text):
    """The first comment block at the top of a file, after a shebang, skipping a licence header."""
    lines = text.splitlines()[:150]
    i = 1 if lines and lines[0].startswith('#!') else 0
    for _ in range(3):
        while i < len(lines) and not lines[i].strip():
            i += 1
        block, i = comment_lines(lines, i, language)
        if not block:
            return ''
        joined = '\n'.join(block)
        if not LICENCE.search(joined):
            return joined
    return ''


def manifest_summary(path, text):
    base = os.path.basename(path)
    try:
        if base in ('package.json', 'composer.json'):
            data = json.loads(text)
        elif base in ('pyproject.toml', 'Cargo.toml'):
            import tomllib
            parsed = tomllib.loads(text)
            data = parsed.get('project') or parsed.get('package') or (parsed.get('tool') or {}).get('poetry') or {}
        else:
            return ''
    except (ValueError, ImportError):
        return ''
    desc, name = (data.get('description'), data.get('name')) if isinstance(data, dict) else (None, None)
    if isinstance(desc, str) and desc.strip():
        return ('%s: %s' % (name, desc.strip())) if isinstance(name, str) and name else desc.strip()
    return ''


def doc_summary(text):
    """A document's title and first paragraph (Markdown, reStructuredText or plain text), without heading marks; YAML
    front matter is skipped, its title and description used when the body has none."""
    lines = [line.rstrip() for line in text.splitlines()]
    front = {}
    if lines and lines[0].strip() == '---' and '---' in [x.strip() for x in lines[1:]]:
        end = 1 + [x.strip() for x in lines[1:]].index('---')
        front = dict(m.groups() for m in (re.match(r'(\w+):\s*["\']?(.*?)["\']?\s*$', x) for x in lines[1:end]) if m)
        lines = lines[end + 1:]
    title, para = '', []
    for k, line in enumerate(lines):
        s = line.strip()
        underline = k + 1 < len(lines) and re.fullmatch(r'[=\-~^*#]{3,}', lines[k + 1].strip() or 'x')
        if not s or s.startswith('#') or underline:
            if para:                                    # a blank line or the next heading ends the paragraph
                break
            if s and not title:
                title = s.lstrip('#').strip()
        elif not re.fullmatch(r'[=\-~^*#]{3,}', s) and not s.startswith(('<', '[![', '![', '---')):
            para.append(s)
    title, para = title or front.get('title', ''), ' '.join(para) or front.get('description', '')
    return '%s: %s' % (title, para) if title and para else title or para


def first_code_line(text):
    """The first line that is code: past comments, imports and a docstring block."""
    quote = None
    for line in text.splitlines():
        s = line.strip()
        if quote:
            quote = None if quote in s else quote
            continue
        if s[:3] in ('"""', "'''"):
            quote = s[:3] if s.count(s[:3]) == 1 else None      # a docstring that goes on past this line
            continue
        if s and not s.startswith(('#', '//', '/*', '*', '--', '<!--', 'import ', 'from ', 'require(', 'use ', 'using ',
                                   'package ', '"use strict"', "'use strict'")):
            return s
    return ''


def only_the_name(text, *names):
    """True when a description says nothing but a name: a docstring reading just utils.py is no summary of utils.py."""
    said = ' '.join(str(text).split()).strip(' `"\'.,:;-').lower()
    return said in {n.lower() for name in names for n in (name, os.path.basename(name), os.path.splitext(os.path.basename(name))[0])}


def structural(path, language, category, text, r):
    """What the parser found, when the file carries no description of itself."""
    what = '%s %s' % (lang_name(language, path), WHAT.get(category, 'file'))
    lines = r.get('totalLines') if isinstance(r.get('totalLines'), int) else text.count('\n')
    found = []
    for key, label in (('classes', 'class'), ('functions', 'function')):
        names = [str(s.get('name')) for s in r.get(key) or [] if s.get('name')]
        if names:
            found.append('%s%s %s' % (label, 'es' if label == 'class' and len(names) > 1 else 's' if len(names) > 1 else '',
                                      listing(names, 6)))
    for key in ('sections', 'definitions', 'services', 'endpoints', 'steps', 'resources'):
        names = [str(x.get('heading') or x.get('name') or ('%s %s' % (x.get('method') or '', x.get('path') or '')).strip())
                 for x in r.get(key) or []]
        if names and not found:
            found.append('%s %s' % ('keys' if key == 'sections' and category == 'config' else key, listing(names, 6)))
    count = '%d line%s' % (lines, '' if lines == 1 else 's')
    if found:
        return '%s defining %s (%s)' % (what, ' and '.join(found), count)
    if not text.strip():
        return 'An empty %s' % what
    return '%s of %s, starting with `%s`' % (what, count, flat(first_code_line(text) or text.strip().splitlines()[0], 80))


def draft_summary(path, language, category, text, r):
    """A draft summary from the file itself -- a manifest's description, the docstring or leading comment, a document's
    title and first paragraph, else what the parser found -- never from the file name alone."""
    def docstring():
        try:
            return ast.get_docstring(ast.parse(text)) or '' if language == 'python' else ''
        except (SyntaxError, ValueError):
            return ''
    for source in (lambda: manifest_summary(path, text), docstring,
                   lambda: doc_summary(text) if category == 'docs' else leading_comment(language, text)):
        found = first_paragraph(source())
        if found and not only_the_name(found, path):
            return sentence(found)
    return sentence(structural(path, language, category, text, r))


def symbol_docs(language, text):
    """Python docstrings of every function and class, by (name, line) and by name."""
    docs = {}
    if language != 'python':
        return docs
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return docs
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and ast.get_docstring(node):
            docs[(node.name, node.lineno)] = ast.get_docstring(node)
            docs.setdefault(node.name, ast.get_docstring(node))
    return docs


def comment_above(lines, start, language):
    """The comment block that ends right above line `start` (1-based), past decorators and annotations. A symbol past
    the part of the file the glue read (MAX_READ) has none."""
    if start - 2 >= len(lines):
        return ''
    i = start - 2
    while i >= 0 and lines[i].strip().startswith('@'):
        i -= 1
    end = i + 1
    if i >= 0 and lines[i].strip().endswith(('*/', '-->')):
        while i >= 0 and not lines[i].lstrip().startswith(('/*', '<!--')):
            i -= 1
    else:
        prefix = comment_prefix(language)
        while i >= 0 and lines[i].lstrip().startswith(prefix) and not lines[i].lstrip().startswith(('#!', '#include')):
            i -= 1
        i += 1
    return '\n'.join(comment_lines(lines[max(i, 0):end], 0, language)[0]) if 0 <= i < end else ''


def draft_tags(path, language, kind, roles=()):
    """3-5 lowercase-hyphenated tags from the language, the kind of file or symbol, its folder and its role."""
    def tag(x):
        return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', str(x).lower())).strip('-')
    parts = path.split('/')
    tags = [tag(language), tag(kind), tag(parts[0]) if len(parts) > 1 else 'project-root', *map(tag, roles)]
    if len(parts) > 2:
        tags.append(tag(parts[-2]))
    out = []
    for t in tags + ['source', 'project', 'map']:
        if t and t not in out and (len(out) < 3 or t not in ('source', 'project', 'map')):
            out.append(t)
    return out[:4]                                      # room for the merge's "tested": at most 5 in the map


def size_complexity(lines):
    return 'simple' if lines < 50 else 'moderate' if lines <= 200 else 'complex'


RE_EXPORTS = re.compile(r'''(?:\s*(?:export\s+(?:type\s+)?(?:\*(?:\s+as\s+\w+)?|\{[^}]*\})\s+from\s+['"][^'"]+['"]\s*;?'''
                        r'''|from\s+[\w.]+\s+import\s+(?:\([^)]*\)|[^\n]+)|import\s+[^\n]+'''
                        r'''|__all__\s*=\s*(?:\[[^\]]*\]|\([^)]*\))))+\s*''')


def is_barrel(entry, text):
    """A small file that only re-exports (an index.ts of `export ... from`, an __init__.py of imports): no key file."""
    if (entry.get('sizeLines') or 0) >= 15:
        return False
    code = re.sub(r'/\*.*?\*/|^\s*("""|\'\'\').*?\1', '', text, flags=re.S | re.M)     # block comments, docstrings
    code = re.sub(r'^\s*(?://|#).*$', '', code, flags=re.M)                              # line comments
    return bool(code.strip()) and bool(RE_EXPORTS.fullmatch(code))


# licence / notice texts that the upstream scanner files as `code` when their extension is unknown
NOT_CODE = re.compile(r'^(?:licen[cs]e|copying|notice|authors|patents|third[_-]?party[_-]?notices?)(?:[._-].*)?$'
                      r'|\.licen[cs]e(?:\.txt)?$|^(?:licen[cs]e|copying)', re.I)


def key_files(files, texts, importers):
    """The files that deserve a summary written from their code, 30 at most: up to 12 most-imported, up to 10 entry
    points (the biggest; barrels that only re-export left out), then the biggest code no other file imports. Tests,
    barrels and binaries are never key files."""
    code = [f for f in files if f['fileCategory'] in ('code', 'script') and not TEST_PATH.search(f['path'])
            and f['path'] in texts and not is_barrel(f, texts[f['path']]) and not NOT_CODE.search(os.path.basename(f['path']))
            and re.fullmatch(r'(?:[a-z][a-z0-9+#._-]*)?', str(f.get('language') or ''), re.I)]   # not '1'
    size = {f['path']: f.get('sizeLines') or 0 for f in code}
    picked = {}
    for f in sorted((f for f in code if importers[f['path']] >= 2), key=lambda f: (-importers[f['path']], f['path']))[:12]:
        picked[f['path']] = 'imported by %d files' % importers[f['path']]
    entries = [f for f in code if os.path.basename(f['path']) in ENTRY_NAMES or texts[f['path']].startswith('#!')
               or re.search(r'''^if\s+__name__\s*==\s*['"]__main__['"]''', texts[f['path']], re.M)]
    for f in sorted(entries, key=lambda f: (-size[f['path']], f['path']))[:10]:
        picked.setdefault(f['path'], 'entry point')
    for f in sorted((f for f in code if not importers[f['path']] and size[f['path']] >= 20),
                    key=lambda f: (-size[f['path']], f['path'])):
        picked.setdefault(f['path'], 'no other file imports it')
    return list(picked.items())[:KEY_MAX]


def draft_file(f, r, text, ids, imports, roles):
    """A file's node, its significant symbols (exported, functions of 10+ lines, classes with 2+ methods) with line
    ranges, and its contains / exports / imports edges."""
    path, language, category = f['path'], f.get('language') or '', f.get('fileCategory') or 'code'
    fid = ids[path]
    nodes = [{'id': fid, 'type': fid.split(':', 1)[0], 'name': os.path.basename(path), 'filePath': path,
              'summary': draft_summary(path, language, category, text, r),
              'tags': draft_tags(path, language, category, roles), 'complexity': size_complexity(f.get('sizeLines') or 0)}]
    edges, lines, docs = [], text.splitlines(), symbol_docs(language, text)
    exported = {e.get('name') for e in r.get('exports') or []}

    def edge(target, kind, weight):
        edges.append({'source': fid, 'target': target, 'type': kind, 'direction': 'forward', 'weight': weight})
    for kind, items in (('function', r.get('functions') or []), ('class', r.get('classes') or [])):
        for s in items:
            name, a, z = s.get('name'), s.get('startLine'), s.get('endLine')
            if not name or not isinstance(a, int) or not isinstance(z, int) or not 1 <= a <= z:
                continue
            if not (name in exported or (kind == 'function' and z - a >= 9) or (kind == 'class' and len(s.get('methods') or []) >= 2)):
                continue
            sid = '%s:%s:%s' % (kind, path, name)
            if any(n['id'] == sid for n in nodes):
                continue
            doc = first_paragraph(docs.get((name, a)) or docs.get(name) or comment_above(lines, a, language))
            doc = '' if only_the_name(doc, name) else doc
            what =('Class %s%s' % (name, ' with methods ' + listing(s.get('methods') or [], 6) if s.get('methods') else '')
                    if kind == 'class' else 'Function %s(%s)' % (name, ', '.join(s.get('params') or [])))
            nodes.append({'id': sid, 'type': kind, 'name': name, 'filePath': path, 'lineRange': [a, z],
                          'summary': sentence(doc or '%s, lines %d-%d' % (what, a, z)),
                          'tags': draft_tags(path, language, kind), 'complexity': size_complexity(z - a + 1)})
            edge(sid, 'contains', 1.0)
            if name in exported:
                edge(sid, 'exports', 0.8)
    for target in dict.fromkeys(imports.get(path) or []):
        if target in ids and target != path:
            edge(ids[target], 'imports', 0.7)
    return nodes, edges


def draft():
    """Complete draft batch files for every batch, from the one parser run over all of them; then the key files."""
    batches, scan = load(os.path.join(INTER, 'batches.json')), load(os.path.join(INTER, 'scan-result.json'))
    out_path, in_path = os.path.join(TMP, 'extract-out-all.json'), os.path.join(TMP, 'extract-in-all.json')
    if os.path.isfile(out_path) and os.path.isfile(in_path) and os.path.getmtime(out_path) < os.path.getmtime(in_path):
        raise Problem('.ua/tmp/extract-out-all.json is older than the input glue batch-inputs wrote -- run the '
                      'extract-structure.mjs command it printed (step 3), then glue draft')
    out = load(out_path)
    results = {r.get('path'): r for r in out.get('results') or []}
    ids = {f['path']: '%s:%s' % (node_type(f), f['path']) for f in scan['files']}
    imports = scan.get('importMap') or {}
    importers = Counter(t for targets in imports.values() for t in set(targets or []))
    files = [f for b in batches['batches'] for f in b['files']]
    binary = {f['path']: binary_kind(read_head(f['path'])) for f in files}
    texts = {}                                          # what key_files needs: the start (a shebang) and the end (a
    for f in files:                                     # __main__ guard) of each text file, never a whole big file
        if not binary[f['path']]:
            text = read_text(f['path'])
            texts[f['path']] = text if len(text) <= 8192 else text[:4096] + '\n' + text[-4096:]
    keys = key_files(files, texts, importers)
    roles = {p: ('entry-point',) if why == 'entry point' else ('most-imported',) if why.startswith('imported') else ()
             for p, why in keys}
    real = load(os.path.join(INTER, 'summaries.json'), {})
    if not isinstance(real, dict):
        raise Problem('.ua/intermediate/summaries.json must be {"<path or node id>": "<summary>", ...} -- rewrite it whole')
    used, count, links, summary = set(), Counter(), 0, {}
    for b in batches['batches']:
        nodes, edges = [], []
        for f in b['files']:
            if binary[f['path']]:                       # a type-and-size summary; never read, never parsed
                size = os.path.getsize(os.path.join(ROOT, f['path']))
                nodes.append({'id': ids[f['path']], 'type': ids[f['path']].split(':', 1)[0],
                              'name': os.path.basename(f['path']), 'filePath': f['path'],
                              'summary': 'Binary file (%s, %s); not read.' % (binary[f['path']], '%.1f KB' % (size / 1024)
                                                                              if size < 1 << 20 else '%.1f MB' % (size / (1 << 20))),
                              'tags': draft_tags(f['path'], 'binary', f.get('fileCategory') or 'file'), 'complexity': 'simple'})
                continue
            n, e = draft_file(f, results.get(f['path']) or {}, read_text(f['path']), ids, imports, roles.get(f['path'], ()))
            nodes += n
            edges += e
        for n in nodes:
            key = n['id'] if n['id'] in real else n['filePath'] if n['type'] in FILE_TYPES and n['filePath'] in real else None
            if key and isinstance(real[key], str) and real[key].strip():
                n['summary'] = ' '.join(real[key].split())
                used.add(key)
            if n['type'] in FILE_TYPES:
                summary[n['filePath']] = (n['summary'], key is not None and key in used)
            count[n['type']] += 1
        links += len(edges)
        save(os.path.join(INTER, 'batch-%d.json' % b['batchIndex']), {'nodes': nodes, 'edges': edges})
    run = load(RUN, {})
    run['keyFiles'] = [p for p, _ in keys]
    save(RUN, run)
    print('drafted %d batch files: %d nodes (%s), %d edges; summaries come from docstrings, comments, manifests, '
          'document titles or the parser.' % (batches['totalBatches'], sum(count.values()),
                                               ', '.join('%s %d' % kv for kv in count.most_common()), links))
    unknown = sorted(k for k in real if k not in used)
    if unknown:
        print('summaries.json names no node for: %s -- use a file path or a node id' % listing(unknown))
    print('key files -- read each and write a real summary from its code; every other file keeps its draft:')
    for path, why in keys:
        text, done = summary[path]
        print('  %s (%s) -- %s: %s' % (path, why, 'real' if done else 'draft', flat(text, 110)))
    todo = sum(1 for p, _ in keys if not summary[p][1])
    after = (UPDATE_TAIL.lstrip('; ') if run.get('mode') == 'update' and os.path.isfile(os.path.join(INTER, 'batch-existing.json'))
             else 'then the merge (step 5) and step 6 (glue nodes lists the folders)')
    if todo:
        print('next: write_file .ua/intermediate/summaries.json = {"<path>": "<1-2 sentences from the code>", ...} for '
              'the %d key files on drafts, then rerun glue draft (it applies them); %s.' % (todo, after))
    else:
        print('every key file has its real summary. next: %s.' % re.sub(r'^then ', '', after))
    return 0


def main(argv):
    commands = {'imports-input': imports_input, 'scan-result': scan_result, 'batch-inputs': batch_inputs,
                'draft': draft, 'nodes': nodes, 'assemble': assemble, 'finish': finish,
                'domain-context': domain_context, 'domain': domain}
    if argv[:1] != ['status'] and not (len(argv) == 1 and argv[0] in commands) and not (
            len(argv) == 2 and argv[0] == 'structure'):
        print(__doc__ if argv != ['structure'] else 'structure needs a batch index: python3 .ua/tmp/ua_glue.py structure <i>')
        return 2
    if os.path.isdir(LEGACY):
        print('problem: this project has a legacy .understand-anything/ data folder; the plugin scripts and the Understand '
              'panel use it instead of .ua/, so the map would land in two places. Rename it first '
              '(run_bash `mv .understand-anything .understand-anything.old`), then rerun from step 2.')
        return 1
    try:
        if argv[0] == 'status':
            return status(argv[1:])
        if argv[0] == 'structure':
            return structure(argv[1])
        return commands[argv[0]]() or 0
    except FileNotFoundError as exc:
        name = os.path.basename(str(exc.filename or ''))
        print('problem: missing %s -- run %s first' % (rel(str(exc.filename)), STEP_OF.get(name, 'the step that writes it')))
        return 1
    except json.JSONDecodeError as exc:
        print('problem: a JSON file could not be read (%s) -- rewrite the file named in the error whole, then rerun' % exc)
        return 1
    except ValueError as exc:
        print('problem: %s' % exc)
        return 1
    except KeyError as exc:
        print('problem: a file lacks the field %s -- rerun the step that writes it' % exc)
        return 1
    except Problem as exc:
        print('problem: %s' % exc)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
