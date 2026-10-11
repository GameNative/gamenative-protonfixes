#!/usr/bin/env python3
"""Convert the per-app tables of Valve's `proton` script into GameNative protonfix documents.

The script is parsed with `ast`. Nothing from it is imported or executed.
Output: `<out>/<appid>.json` (one document per Steam app id) and `<out>/index.json`.
"""

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

SCHEMA = 1
STORE = 'steam'
UPSTREAM = 'ValveSoftware/Proton'
MAX_SOURCE = 400

APPID_ENV_NAMES = {'SteamGameId', 'SteamAppId', 'STEAM_COMPAT_APP_ID'}
APPID_LOCAL_NAMES = {'appid', 'sgi'}

DESKTOP_ONLY_FLAGS = {
    'noopwr': 'Vulkan OPWR presentation path of desktop winex11',
    'noxalia': 'Xalia accessibility layer is not shipped',
    'xalia': 'Xalia accessibility layer is not shipped',
    'hidenvgpu': 'GPU vendor hiding has no meaning on Android GPUs',
    'hidevggpu': 'GPU vendor hiding has no meaning on Android GPUs',
    'hideintelgpu': 'GPU vendor hiding has no meaning on Android GPUs',
    'hideapu': 'GPU vendor hiding has no meaning on Android GPUs',
    'disablenvapi': 'dxvk-nvapi is not shipped',
    'forcenvapi': 'dxvk-nvapi is not shipped',
    'gamedrive': 'Steam library drive mapping (s:) is desktop Steam layout',
    'steamdrive': 'Steam client drive mapping (t:) is desktop Steam layout',
    'noxim': 'X input method handling of desktop hosts',
    'wined3d': 'the graphics wrapper is chosen by the container',
    'wined3d11': 'the graphics wrapper is chosen by the container',
    'dxvkd3d8': 'the graphics wrapper is chosen by the container',
}

DESKTOP_ONLY_ENV = {
    'WINE_HIDE_AMD_GPU': 'GPU vendor hiding has no meaning on Android GPUs',
    'WINE_HIDE_NVIDIA_GPU': 'GPU vendor hiding has no meaning on Android GPUs',
    'WINE_HIDE_INTEL_GPU': 'GPU vendor hiding has no meaning on Android GPUs',
    'WINE_HIDE_VANGOGH_GPU': 'GPU vendor hiding has no meaning on Android GPUs',
    'WINE_HIDE_APU': 'GPU vendor hiding has no meaning on Android GPUs',
    'WINE_DISABLE_GAMESCOPE_MAX_SIZE_HACK': 'gamescope is not used',
    'PROTON_DISABLE_HIDRAW': 'Steam Deck hidraw policy',
    'PROTON_MEDIACONV_NO_VIDEO': 'Proton media converter blank.mka is not shipped',
}

DESKTOP_ONLY_DLLS = {
    'steam.exe': 'Proton steam.exe launcher shim',
}


def is_os_environ(node):
    return (isinstance(node, ast.Attribute) and node.attr == 'environ'
            and isinstance(node.value, ast.Name) and node.value.id == 'os')


def const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def is_appid_expr(node):
    if isinstance(node, ast.Name) and node.id in APPID_LOCAL_NAMES:
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get':
        return is_os_environ(node.func.value) and bool(node.args) and const_str(node.args[0]) in APPID_ENV_NAMES
    if isinstance(node, ast.Subscript) and is_os_environ(node.value):
        return const_str(node.slice) in APPID_ENV_NAMES
    return False


def str_items(node):
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        if all(const_str(e) is not None for e in node.elts):
            return list(node.elts)
    return None


def target_kind(node):
    if isinstance(node, ast.Attribute) and node.attr in ('env', 'dlloverrides'):
        if isinstance(node.value, ast.Name) and node.value.id in ('self', 'g_session'):
            return node.attr
    return None


def is_env_absent_check(node):
    return (isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.NotIn)
            and const_str(node.left) is not None and target_kind(node.comparators[0]) == 'env')


def is_appid_presence(node):
    return (isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.In)
            and const_str(node.left) in APPID_ENV_NAMES and is_os_environ(node.comparators[0]))


def checks_only_env(node):
    compares = [n for n in ast.walk(node) if isinstance(n, ast.Compare)]
    return bool(compares) and all(
        all(target_kind(c) == 'env' or (isinstance(c, ast.Subscript) and target_kind(c.value) == 'env')
            for c in cmp.comparators)
        for cmp in compares)


def is_trivial_guard(node):
    if is_env_absent_check(node) or is_appid_presence(node):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not) and checks_only_env(node.operand):
        return True
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        return all(is_trivial_guard(v) for v in node.values)
    return False


def appid_predicate(test):
    """Returns (id nodes, negated, other conditions), or None when the test does not compare app ids."""
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and is_appid_expr(test.left):
        op, right = test.ops[0], test.comparators[0]
        if isinstance(op, (ast.In, ast.NotIn)):
            items = str_items(right)
            if items is not None:
                return items, isinstance(op, ast.NotIn), []
        if isinstance(op, (ast.Eq, ast.NotEq)) and const_str(right) is not None:
            return [right], isinstance(op, ast.NotEq), []
        return None
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        found, others = None, []
        for v in test.values:
            p = appid_predicate(v) if found is None else None
            if p is not None:
                found = p
            else:
                others.append(v)
        if found is not None:
            return found[0], found[1], found[2] + others
    return None


def is_ret_add(node):
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == 'add'
            and isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == 'ret'
            and len(node.value.args) == 1 and const_str(node.value.args[0]) is not None)


class Collector:
    def __init__(self, source):
        self.source = source
        self.lines = source.splitlines()
        self.apps = {}
        self.names = {}
        self.unparsed = []

    def app(self, appid):
        return self.apps.setdefault(appid, {'flags': {}, 'steps': []})

    def segment(self, node):
        text = ' '.join((ast.get_source_segment(self.source, node) or '').split())
        return text if len(text) <= MAX_SOURCE else text[:MAX_SOURCE] + '...'

    def remember_name(self, node):
        line = self.lines[node.lineno - 1] if 0 < node.lineno <= len(self.lines) else ''
        m = re.search(r'#\s*(.+?)\s*$', line)
        appid = const_str(node)
        if not m or appid is None or appid in self.names:
            return
        name = re.sub(r'\s*-\s*CW[- ]bug.*$', '', m.group(1), flags=re.I).strip()
        name = re.sub(r'\s*\(\d+\)\s*$', '', name).strip()
        if name and not re.match(r'(affected by|cw[- ]bug|https?:)', name, re.I):
            self.names[appid] = name


class Walker:
    def __init__(self, col):
        self.col = col

    def walk_body(self, body, ctx):
        for stmt in body:
            self.walk_stmt(stmt, ctx)

    def walk_stmt(self, stmt, ctx):
        col = self.col
        if isinstance(stmt, ast.If):
            pred = appid_predicate(stmt.test)
            if pred is not None:
                items, negated, others = pred
                for it in items:
                    col.remember_name(it)
                if negated:
                    col.unparsed.append({'kind': 'all-except', 'ids': [const_str(i) for i in items],
                                         'source': col.segment(stmt)})
                else:
                    conds = [col.segment(c) for c in others if not is_trivial_guard(c)]
                    sub = dict(ctx, when=ctx.get('when', []) + conds)
                    for it in items:
                        self.collect(const_str(it), stmt.body, sub, stmt)
                if len(stmt.orelse) == 1 and isinstance(stmt.orelse[0], ast.If):
                    self.walk_stmt(stmt.orelse[0], ctx)
                return
            for node in ast.walk(stmt.test):
                if isinstance(node, ast.IfExp):
                    self.walk_ifexp(stmt.test, node, ctx)
            guard = [] if is_trivial_guard(stmt.test) else [col.segment(stmt.test)]
            self.walk_body(stmt.body, dict(ctx, outer=ctx.get('outer', []) + guard))
            self.walk_body(stmt.orelse, ctx)
            return
        if isinstance(stmt, (ast.For, ast.While, ast.With, ast.Try)):
            for field in ('body', 'orelse', 'finalbody'):
                self.walk_body(getattr(stmt, field, None) or [], ctx)
            for h in getattr(stmt, 'handlers', None) or []:
                self.walk_body(h.body, ctx)
            return
        if isinstance(stmt, (ast.FunctionDef, ast.ClassDef)):
            self.walk_body(stmt.body, {'function': stmt.name})
            return
        for node in ast.walk(stmt):
            if isinstance(node, ast.IfExp):
                self.walk_ifexp(stmt, node, ctx)

    def walk_ifexp(self, stmt, node, ctx):
        pred = appid_predicate(node.test)
        if pred is None or pred[1]:
            return
        parent = next((c for c in ast.walk(stmt) if isinstance(c, ast.Call) and node in c.args), None)
        source = self.col.segment(stmt)
        name = None
        if (parent is not None and isinstance(parent.func, ast.Attribute) and parent.func.attr == 'get'
                and is_os_environ(parent.func.value) and parent.args):
            name = const_str(parent.args[0])
        value = const_str(node.body)
        if name is None or value is None:
            self.col.unparsed.append({'kind': 'ifexp', 'source': self.col.segment(node)})
            return
        for it in pred[0]:
            self.col.remember_name(it)
            self.add_env(const_str(it), name, value, ctx, source)

    def collect(self, appid, body, ctx, owner):
        leftovers = []
        for stmt in body:
            if not self.collect_stmt(appid, stmt, ctx):
                leftovers.append(stmt)
        if leftovers:
            if all(isinstance(s, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'argv' for t in s.targets)
                   for s in leftovers):
                reason = "desktop-only: changes Proton's steam.exe launch command"
            else:
                reason = 'not convertible'
            source = ' '.join(self.col.segment(s) for s in leftovers)
            self.add_unsupported(appid, reason, source[:MAX_SOURCE], None)

    def collect_stmt(self, appid, stmt, ctx):
        col = self.col
        if is_ret_add(stmt):
            note = 'conditional upstream: ' + '; '.join(ctx['nested']) if ctx.get('nested') else None
            col.app(appid)['flags'].setdefault(const_str(stmt.value.args[0]), note)
            return True
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            f = stmt.value.func
            if isinstance(f, ast.Attribute) and f.attr == 'setdefault' and target_kind(f.value) == 'env':
                args = [const_str(a) for a in stmt.value.args[:2]]
                if len(args) == 2 and None not in args:
                    self.add_env(appid, args[0], args[1], ctx, col.segment(stmt))
                    return True
            return False
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Subscript):
            t = stmt.targets[0]
            kind, key, value = target_kind(t.value), const_str(t.slice), const_str(stmt.value)
            if kind and key is not None and value is not None:
                if kind == 'env':
                    self.add_env(appid, key, value, ctx, col.segment(stmt))
                else:
                    self.add_dll(appid, key, value, ctx, col.segment(stmt))
                return True
            return False
        if isinstance(stmt, ast.Delete) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Subscript):
            t = stmt.targets[0]
            key = const_str(t.slice)
            if target_kind(t.value) == 'dlloverrides' and key is not None:
                reason = DESKTOP_ONLY_DLLS.get(key)
                self.add_unsupported(appid, 'desktop-only: ' + reason if reason else 'removes a default Proton DLL override',
                                     col.segment(stmt), None)
                return True
            return False
        if isinstance(stmt, (ast.Try, ast.With, ast.If)):
            if isinstance(stmt, ast.Try):
                label = 'try'
                bodies = [stmt.body] + [h.body for h in stmt.handlers] + [stmt.orelse, stmt.finalbody]
            elif isinstance(stmt, ast.With):
                label = col.segment(stmt.items[0].context_expr)
                bodies = [stmt.body]
            else:
                label = col.segment(stmt.test)
                bodies = [stmt.body, stmt.orelse]
            inner = [s for b in bodies for s in b]
            if not all(is_ret_add(s) or isinstance(s, (ast.Try, ast.With, ast.If, ast.Pass, ast.Assign, ast.Expr))
                       for s in inner):
                return False
            sub = dict(ctx, nested=ctx.get('nested', []) + [label])
            handled = False
            for s in inner:
                if is_ret_add(s) or isinstance(s, (ast.Try, ast.With, ast.If)):
                    handled = self.collect_stmt(appid, s, sub) or handled
            return handled
        return isinstance(stmt, ast.Pass)

    def note_for(self, ctx):
        notes = []
        outer = ctx.get('outer', [])
        if any('aarch64' in o for o in outer):
            notes.append('aarch64 hosts only')
        others = [o for o in outer if 'aarch64' not in o]
        if others:
            notes.append('upstream guard: ' + '; '.join(others))
        return ', '.join(notes) or None

    def add_step(self, appid, step, ctx, source, desktop_reason):
        if ctx.get('when'):
            step['note'] = 'upstream condition: ' + '; '.join(ctx['when'])
            self.add_unsupported(appid, 'condition not expressible: ' + '; '.join(ctx['when']), source, step)
            return
        if desktop_reason:
            self.add_unsupported(appid, 'desktop-only: ' + desktop_reason, source, step)
            return
        note = self.note_for(ctx)
        if note:
            step['note'] = note
        self.col.app(appid)['steps'].append(step)

    def add_env(self, appid, name, value, ctx, source):
        self.add_step(appid, {'op': 'env', 'set': {name: value}}, ctx, source, DESKTOP_ONLY_ENV.get(name))

    def add_dll(self, appid, dll, value, ctx, source):
        self.add_step(appid, {'op': 'dll_override', 'overrides': {dll: value or 'd'}}, ctx, source,
                      DESKTOP_ONLY_DLLS.get(dll))

    def add_unsupported(self, appid, reason, source, step):
        st = {'op': 'unsupported', 'reason': reason, 'source': source}
        if step is not None:
            st['step'] = step
        self.col.app(appid)['steps'].append(st)


def module_dicts(tree):
    out = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Dict)):
            out[node.targets[0].id] = node.value
    return out


def find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def find_local_assign(fn, name):
    if fn is None:
        return None
    for node in ast.walk(fn):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name):
            return node.value
    return None


def session_default_dlls(tree):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'Session':
            init = find_function(node, '__init__')
            for stmt in (init.body if init else []):
                if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                        and target_kind(stmt.targets[0]) == 'dlloverrides' and isinstance(stmt.value, ast.Dict)):
                    try:
                        return ast.literal_eval(stmt.value)
                    except ValueError:
                        return None
    return None


def collect(source):
    tree = ast.parse(source)
    col = Collector(source)
    walker = Walker(col)

    dcc = find_function(tree, 'default_compat_config')
    global_flags = sorted({const_str(s.value.args[0]) for s in (dcc.body if dcc else []) if is_ret_add(s)})
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            walker.walk_body(node.body, {'function': node.name})

    tables = module_dicts(tree)
    known_tables = {'default_cpu_limit', 'fex_application_profiles'}
    for name, node in tables.items():
        if name not in known_tables and node.keys and all(const_str(k) and const_str(k).isdigit() for k in node.keys):
            col.unparsed.append({'kind': 'table', 'name': name, 'source': col.segment(node)})

    cpu = tables.get('default_cpu_limit')
    for k, v in zip(cpu.keys if cpu else [], cpu.values if cpu else []):
        appid, value = const_str(k), const_str(v)
        if appid is None or value is None:
            col.unparsed.append({'kind': 'default_cpu_limit', 'source': col.segment(v)})
            continue
        col.remember_name(k)
        step = {'op': 'cpu_topology'}
        if value.isdigit():
            step['limit'] = int(value)
        else:
            step['value'] = value
            step['note'] = 'WINE_CPU_TOPOLOGY "P": performance cores only'
        col.app(appid)['steps'].append(step)

    fex = tables.get('fex_application_profiles')
    for k, v in zip(fex.keys if fex else [], fex.values if fex else []):
        appid = const_str(k)
        try:
            overrides = ast.literal_eval(v)
        except ValueError:
            overrides = None
        if appid is None or overrides is None:
            col.unparsed.append({'kind': 'fex_application_profiles', 'source': col.segment(v)})
            continue
        col.remember_name(k)
        col.app(appid)['steps'].append({
            'op': 'fex_app_config', 'appOverrides': overrides,
            'note': 'FEX AppOverrides keyed by executable glob, aarch64 hosts only',
        })

    alts = find_local_assign(find_function(tree, 'create_fonts_symlinks'), 'ALTERNATIVES')
    if alts is not None:
        try:
            pairs = sorted(ast.literal_eval(alts))
        except ValueError:
            pairs = []
            col.unparsed.append({'kind': 'ALTERNATIVES', 'source': col.segment(alts)})
        for elt in getattr(alts, 'elts', []):
            if isinstance(elt, ast.Tuple) and elt.elts:
                col.remember_name(elt.elts[0])
        for appid, font in pairs:
            col.app(appid)['steps'].append({
                'op': 'unsupported', 'reason': f'desktop-only: uses the alternative {font} shipped with Proton',
                'source': 'create_fonts_symlinks ALTERNATIVES',
            })

    defaults = {
        'compatFlags': global_flags,
        'dllOverrides': session_default_dlls(tree) or {},
        'forcelgaddByDefault': 'forcelgadd' in source and 'noforcelgadd' in source,
    }
    return col, defaults


def build_docs(col):
    docs = {}
    for appid, data in col.apps.items():
        steps = []
        flags = sorted(data['flags'])
        if flags:
            step = {'op': 'compat_config', 'flags': flags}
            desktop = [f for f in flags if f in DESKTOP_ONLY_FLAGS]
            if desktop:
                step['desktopOnly'] = desktop
            notes = {f: n for f, n in data['flags'].items() if n}
            if notes:
                step['flagNotes'] = notes
            steps.append(step)
        seen = set()
        for st in data['steps']:
            key = json.dumps(st, sort_keys=True)
            if key not in seen:
                seen.add(key)
                steps.append(st)
        unsupported = [s for s in steps if s['op'] == 'unsupported']
        android = [s for s in steps if s['op'] != 'unsupported'
                   and not (s['op'] == 'compat_config' and set(s['flags']) <= set(s.get('desktopOnly', [])))]
        if not android:
            coverage = 'none'
        elif any(not s['reason'].startswith('desktop-only') for s in unsupported):
            coverage = 'partial'
        else:
            coverage = 'full'
        docs[appid] = {
            'schema': SCHEMA,
            'store': STORE,
            'id': appid,
            'name': col.names.get(appid),
            'upstream': 'proton',
            'coverage': coverage,
            'steps': steps,
        }
    return docs


def dump_json(obj):
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + '\n'


def git_info(path):
    try:
        r = subprocess.run(['git', '-C', path, 'log', '-1', '--format=%h%n%cI'],
                           capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None, None
    lines = r.stdout.strip().split('\n')
    if r.returncode != 0 or len(lines) < 2 or not lines[0]:
        return None, None
    return lines[0], lines[1]


def generate(source, out_dir, branch, commit, date):
    col, defaults = collect(source)
    docs = build_docs(col)
    os.makedirs(out_dir, exist_ok=True)
    prefix = os.path.basename(os.path.normpath(out_dir))
    written = set()
    entries = {}
    for appid in sorted(docs, key=lambda x: (len(x), x)):
        doc = docs[appid]
        data = dump_json(doc)
        name = f'{appid}.json'
        with open(os.path.join(out_dir, name), 'w', encoding='utf-8', newline='\n') as f:
            f.write(data)
        written.add(name)
        entries[f'{STORE}/{appid}'] = {
            'file': f'{prefix}/{name}',
            'sha256': hashlib.sha256(data.encode('utf-8')).hexdigest(),
            'name': doc['name'],
            'coverage': doc['coverage'],
        }
    for f in os.listdir(out_dir):
        if f.endswith('.json') and f != 'index.json' and f not in written:
            os.remove(os.path.join(out_dir, f))
    index = {
        'schema': SCHEMA,
        'upstream': UPSTREAM,
        'upstreamBranch': branch,
        'upstreamCommit': commit or 'unknown',
        'upstreamDate': date,
        'desktopOnlyFlags': DESKTOP_ONLY_FLAGS,
        'defaults': defaults,
        'unparsed': col.unparsed,
        'entries': entries,
    }
    index_path = os.path.join(out_dir, 'index.json')
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    if os.path.isfile(index_path):
        try:
            with open(index_path, encoding='utf-8') as f:
                old = json.load(f)
            if {k: v for k, v in old.items() if k != 'generatedAt'} == index and old.get('generatedAt'):
                generated_at = old['generatedAt']
        except (OSError, ValueError):
            pass
    index['generatedAt'] = generated_at
    with open(index_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(dump_json(index))
    return docs, index


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--proton', required=True, help="path to Valve's proton script")
    ap.add_argument('--out', required=True, help='output directory (data/valve)')
    ap.add_argument('--branch', default='proton_11.0')
    ap.add_argument('--upstream-commit')
    ap.add_argument('--upstream-date')
    a = ap.parse_args(argv)
    with open(a.proton, encoding='utf-8') as f:
        source = f.read()
    commit, date = a.upstream_commit, a.upstream_date
    if not commit:
        commit, git_date = git_info(os.path.dirname(os.path.abspath(a.proton)))
        date = date or git_date
    docs, index = generate(source, a.out, a.branch, commit, date)
    cov = {}
    for d in docs.values():
        cov[d['coverage']] = cov.get(d['coverage'], 0) + 1
    print(f'documents: {len(docs)}  ' + '  '.join(f'{k}: {v}' for k, v in sorted(cov.items()))
          + f'  unparsed: {len(index["unparsed"])}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
