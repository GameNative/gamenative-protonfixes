#!/usr/bin/env python3
"""Convert umu-protonfixes gamefixes and winetricks verbs into GameNative JSON data.

Fix files are parsed with `ast` and interpreted symbolically. Nothing from the
inputs is imported or executed.
"""

import argparse
import ast
import configparser
import copy
import csv
import fnmatch
import hashlib
import io
import json
import os
import posixpath
import re
import shlex
import stat as stat_module
import subprocess
import sys
import textwrap
from collections import Counter, OrderedDict
from datetime import datetime, timezone

SCHEMA = 1
DB_STORE = {'umu': 'none'}
KNOWN_STORES = (
    'steam', 'gog', 'egs', 'umu', 'ubisoft', 'zoomplatform', 'humble', 'itchio',
    'amazon', 'battlenet', 'ea',
)
OVERRIDE_ORDER = {
    'DISABLED': 'd', 'NATIVE': 'n', 'BUILTIN': 'b',
    'NATIVE_BUILTIN': 'n,b', 'BUILTIN_NATIVE': 'b,n',
}
DOS_DEVICE = {'NETWORK': 'network', 'FLOPPY': 'floppy', 'CD_ROM': 'cdrom', 'HD': 'hd'}
BASE_PATH = {'USER': 'user', 'GAME': 'game', 'APPDATA_LOCAL': 'appdata_local'}
STR_METHODS = {
    'lower', 'upper', 'strip', 'lstrip', 'rstrip', 'split', 'rsplit', 'replace',
    'startswith', 'endswith', 'isnumeric', 'isdigit', 'format', 'join', 'casefold',
    'removeprefix', 'removesuffix', 'title', 'capitalize',
}
DEFAULT_EXTRA_VERBS = [
    'd3dcompiler_42', 'd3dcompiler_43', 'd3dcompiler_46', 'd3dcompiler_47',
    'd3dx9', 'd3dx9_24', 'd3dx9_25', 'd3dx9_26', 'd3dx9_27', 'd3dx9_28', 'd3dx9_29',
    'd3dx9_30', 'd3dx9_31', 'd3dx9_32', 'd3dx9_33', 'd3dx9_34', 'd3dx9_35', 'd3dx9_36',
    'd3dx9_37', 'd3dx9_38', 'd3dx9_39', 'd3dx9_40', 'd3dx9_41', 'd3dx9_42', 'd3dx9_43',
    'd3dx10', 'd3dx10_43', 'd3dx11_42', 'd3dx11_43', 'd3dxof',
    'directplay', 'directmusic', 'dsound', 'dmusic', 'dmime', 'dmloader', 'dmsynth',
    'dswave', 'dmstyle', 'dmband', 'dmcompos', 'dmscript', 'xact', 'xact_x64', 'xaudio29',
    'openal', 'mfc42', 'mfc80', 'mfc90', 'mfc100', 'mfc110', 'mfc120', 'mfc140',
    'msxml3', 'msxml4', 'msxml6', 'usp10', 'gdiplus', 'icodecs', 'l3codecx', 'quartz',
    'corefonts', 'arial', 'tahoma', 'verdana', 'times', 'courier', 'trebuchet',
    'georgia', 'impact', 'comicsans', 'webdings', 'andale', 'lucida', 'allfonts',
    'cjkfonts', 'fakejapanese', 'fakechinese', 'fakekorean', 'sourcehansans',
    'vcrun6', 'vcrun2005', 'vcrun2008', 'vcrun2010', 'vcrun2012', 'vcrun2013',
    'vcrun2015', 'vcrun2017', 'vcrun2019', 'vcrun2022', 'ucrtbase2019',
    'physx', 'directx9', 'dotnet20', 'dotnet35', 'dotnet35sp1', 'dotnet40', 'dotnet45',
    'dotnet452', 'dotnet462', 'dotnet472', 'dotnet48', 'dotnetcore3', 'dotnet6',
    'dotnet7', 'dotnet8', 'dotnet9', 'dotnetdesktop6', 'dotnetdesktop8', 'dotnetdesktop9',
    'dotnetdesktop10', 'xna31', 'xna40', 'webview2', 'ubisoftconnect', 'ie8',
    'win7', 'winxp', 'win10', 'win11', 'win81', 'win8', 'win2k', 'win98', 'vista',
    'sound=alsa', 'sound=pulse', 'sound=disabled', 'renderer=gl', 'renderer=vulkan',
    'renderer=gdi', 'renderer=no3d', 'videomemorysize=512', 'videomemorysize=1024',
    'videomemorysize=2048', 'videomemorysize=default', 'hidewineexports=enable',
    'hidewineexports=disable',
]


LEGACY_USER_DIRS = (
    ('Local Settings/Application Data', 'AppData/Local'),
    ('Application Data', 'AppData/Roaming'),
    ('My Documents', 'Documents'),
)
WAYLAND_ENV = ('PROTON_USE_WAYLAND', 'PROTON_ENABLE_WAYLAND')
RUNTIME_APPS = {'install_eac_runtime': 1826330, 'install_battleye_runtime': 1161040}
OVERRIDES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'overrides')


class Unsupported(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class EffectUnsupported(Unsupported):
    pass


class Opaque:
    def __init__(self, reason, source=''):
        self.reason = reason
        self.source = source


class PathV(str):
    pass


class Module:
    def __init__(self, name):
        self.name = name


class LocalFunc:
    def __init__(self, node):
        self.node = node


class EnvRead:
    def __init__(self, name, default=None, lower=False):
        self.name = name
        self.default = default
        self.lower = lower


class Token:
    def __init__(self, n, url):
        self.n = n
        self.url = url
        self.id = f'download{n}'


class Hasher:
    def __init__(self):
        self.token = None


class Digest:
    def __init__(self, token):
        self.token = token


class FileDigest:
    def __init__(self, path):
        self.path = path


class FileContent:
    def __init__(self, path):
        self.path = path


class TokenContent:
    def __init__(self, token):
        self.token = token


class JsonDoc:
    def __init__(self, path):
        self.path = path


class ZipHandle:
    def __init__(self, token):
        self.token = token


class UrlResp:
    def __init__(self, url):
        self.url = url


class OpenFile:
    def __init__(self, path, mode, encoding, token=None):
        self.path = path
        self.mode = mode
        self.encoding = encoding
        self.token = token
        self.step = None
        self.step_guard = None
        self.handled = False


class StatObj:
    def __init__(self, path):
        self.path = path


class StatMode:
    def __init__(self, path):
        self.path = path


class ReadonlyMode:
    def __init__(self, path):
        self.path = path


class RandomBytes:
    pass


class Namespace:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Frame:
    def __init__(self):
        self.returns = []


TRUE = (frozenset(),)
FALSE = ()


def lit(kind, *args):
    return (True, kind) + tuple(args)


def neg(literal):
    return (not literal[0],) + literal[1:]


def _lit_key(literal):
    return json.dumps([str(x) for x in literal])


def implies(x, y):
    if x == y:
        return True
    if x[1] == 'fileExists' and y[1] == 'fileExists' and x[0] == y[0]:
        if x[0]:
            return x[2].startswith(y[2] + '/')
        return y[2].startswith(x[2] + '/')
    return False


def _reduce(c):
    return frozenset(y for y in c if not any(x != y and implies(x, y) for x in c))


def _absorbs(k, c):
    return all(any(implies(x, y) for x in c) for y in k)


def simplify(dnf):
    conj = []
    for c in dnf:
        c = _reduce(c)
        if any(neg(x) in c for x in c):
            continue
        if any(x[1] == 'fileExists' and y[1] == 'fileExists' and x[0] and not y[0] and x[2].startswith(y[2] + '/')
               for x in c for y in c):
            continue
        if c not in conj:
            conj.append(c)
    changed = True
    while changed:
        changed = False
        conj.sort(key=len)
        kept = []
        for c in conj:
            if any(_absorbs(k, c) for k in kept):
                continue
            kept.append(c)
        conj = kept
        for i in range(len(conj)):
            for j in range(i + 1, len(conj)):
                diff = conj[i] ^ conj[j]
                if len(diff) == 2:
                    x, y = tuple(diff)
                    if neg(x) == y:
                        merged = _reduce(conj[i] & conj[j])
                        conj = [c for k, c in enumerate(conj) if k not in (i, j)]
                        conj.append(merged)
                        changed = True
                        break
            if changed:
                break
    return tuple(sorted(conj, key=lambda c: (len(c), sorted(_lit_key(x) for x in c))))


def d_and(a, b):
    return simplify(tuple(x | y for x in a for y in b))


def d_or(a, b):
    return simplify(tuple(a) + tuple(b))


def d_not(a):
    res = TRUE
    for c in a:
        res = d_and(res, tuple(frozenset([neg(x)]) for x in c))
    return res


class Cond:
    def __init__(self, dnf):
        self.dnf = dnf


def mk_cond(dnf):
    if dnf == TRUE:
        return True
    if dnf == FALSE:
        return False
    return Cond(dnf)


def to_dnf(value):
    if value is True:
        return TRUE
    if value is False:
        return FALSE
    return value.dnf


def is_placeholder_value(value):
    return isinstance(value, str) and '${screen_' in value


def norm_path(p):
    if isinstance(p, Opaque):
        raise Unsupported(p.reason)
    if isinstance(p, Token):
        raise Unsupported('path refers to a downloaded temporary file')
    if not isinstance(p, str):
        raise Unsupported(f'path is not a string ({type(p).__name__})')
    s = str(p)
    if not s:
        raise Unsupported('empty path')
    if not (s.startswith('${') or s.startswith('/')):
        s = '${install}/' + s
    s = posixpath.normpath(s)
    if s.startswith('${prefix}/drive_c'):
        s = '${drive_c}' + s[len('${prefix}/drive_c'):]
    s = re.sub(r'^\$\{drive_c\}/users/steamuser(?=/|$)', '${drive_c}/users/${user}', s)
    home = '${drive_c}/users/${user}/'
    if s.startswith(home):
        s = home + map_legacy_user_dir(s[len(home):])
    return s


def map_legacy_user_dir(rel):
    for old, new in LEGACY_USER_DIRS:
        if rel == old or rel.startswith(old + '/'):
            return new + rel[len(old):]
    return rel


def env_literal(name, value=None):
    if name in WAYLAND_ENV and value in (None, '1'):
        return lit('host', 'wayland')
    if name == 'NOSTEAM' and value is None:
        return lit('host', 'nosteam')
    if name == 'GAMESCOPE_WAYLAND_DISPLAY' and value is None:
        return lit('host', 'gamescope')
    if name == 'XDG_CURRENT_DESKTOP' and value == 'gamescope':
        return lit('host', 'gamescope')
    if value is None:
        return lit('env', name)
    return lit('envEq', name, value)


def is_host_path(p):
    return not p.startswith('${')


def win_to_host(p):
    m = re.match(r'^[A-Za-z]:[\\/](.*)$', p)
    if not m:
        raise Unsupported(f'unrecognised Windows path {p}')
    return norm_path('${drive_c}/' + m.group(1).replace('\\', '/'))


def parse_reg_string(s, i):
    assert s[i] == '"'
    i += 1
    out = []
    while i < len(s):
        ch = s[i]
        if ch == '\\' and i + 1 < len(s):
            out.append(s[i + 1])
            i += 2
            continue
        if ch == '"':
            return ''.join(out), i + 1
        out.append(ch)
        i += 1
    raise ValueError('unterminated string')


def reg_hive(path):
    parts = path.split('\\', 1)
    root = parts[0].upper()
    rest = parts[1] if len(parts) > 1 else ''
    if root in ('HKEY_CURRENT_USER', 'HKCU'):
        return 'HKCU', rest
    if root in ('HKEY_LOCAL_MACHINE', 'HKLM'):
        return 'HKLM', rest
    if root in ('HKEY_CLASSES_ROOT', 'HKCR'):
        return 'HKLM', 'Software\\Classes' + ('\\' + rest if rest else '')
    raise Unsupported(f'registry hive {parts[0]} is not supported')


def parse_reg_value(raw):
    raw = raw.strip()
    if raw.startswith('"'):
        val, _ = parse_reg_string(raw, 0)
        return {'type': 'REG_SZ', 'value': val}
    low = raw.lower()
    if low.startswith('dword:'):
        return {'type': 'REG_DWORD', 'value': str(int(raw[6:], 16))}
    if low.startswith('hex(2):'):
        data = bytes(int(x, 16) for x in raw[7:].replace(' ', '').split(',') if x)
        return {'type': 'REG_EXPAND_SZ', 'value': data.decode('utf-16-le').rstrip('\x00')}
    if low.startswith('hex:'):
        data = [x for x in raw[4:].replace(' ', '').split(',') if x]
        return {'type': 'REG_BINARY', 'value': ''.join(x.lower().zfill(2) for x in data)}
    if raw == '-':
        raise Unsupported('registry value deletion')
    raise Unsupported(f'registry value type not supported: {raw[:20]}')


def parse_reg(text):
    lines = []
    buf = ''
    for ln in text.replace('\r\n', '\n').split('\n'):
        s = ln.rstrip()
        if buf:
            s = buf + s.strip()
            buf = ''
        if s.endswith('\\') and re.search(r'=\s*hex', s, re.I):
            buf = s[:-1]
            continue
        lines.append(s)
    entries = OrderedDict()
    current = None
    for s in lines:
        st = s.strip()
        if not st or st.startswith(';') or st.startswith('#'):
            continue
        if st == 'REGEDIT4' or st.startswith('Windows Registry Editor'):
            continue
        if st.startswith('['):
            name = st[1:st.rindex(']')]
            if name.startswith('-'):
                raise Unsupported('registry key deletion')
            hive, key = reg_hive(name)
            current = (hive, key)
            entries.setdefault(current, OrderedDict())
            continue
        if current is None:
            raise Unsupported('registry value outside a key')
        if st.startswith('@'):
            vname, rest = '', st[1:].lstrip()
        elif st.startswith('"'):
            vname, j = parse_reg_string(st, 0)
            rest = st[j:].lstrip()
        else:
            raise Unsupported(f'cannot parse registry line {st[:40]}')
        if not rest.startswith('='):
            raise Unsupported(f'cannot parse registry line {st[:40]}')
        entries[current][vname] = parse_reg_value(rest[1:])
    return [{'hive': h, 'key': k, 'values': dict(v)} for (h, k), v in entries.items()]


def ini_sections(ini_text):
    conf = configparser.ConfigParser(empty_lines_in_values=True, allow_no_value=True, strict=False)
    conf.optionxform = lambda optionstr: optionstr
    conf.read_string(ini_text)
    out = []
    defaults = dict(conf.defaults())
    if defaults:
        out.append((None, defaults))
    for sec in conf.sections():
        vals = OrderedDict()
        for k in conf._sections[sec]:
            v = conf._sections[sec][k]
            vals[k] = '' if v is None else v
        out.append((sec, dict(vals)))
    return out


def dosbox_conf_text(conf_dict):
    conf = configparser.ConfigParser()
    conf.read_dict(conf_dict)
    buf = io.StringIO()
    conf.write(buf)
    return buf.getvalue()


def literal_to_json(literal):
    pos, kind = literal[0], literal[1]
    if kind == 'fileExists':
        return {'fileExists': literal[2]} if pos else {'fileMissing': literal[2]}
    if kind == 'env':
        return {'env': literal[2]} if pos else {'envMissing': literal[2]}
    if kind == 'envEq':
        c = {'env': literal[2], 'equals': literal[3]}
        return c if pos else {'not': c}
    if kind == 'store':
        return {'store': literal[2]} if pos else {'storeNot': literal[2]}
    if kind == 'host':
        return {literal[2]: pos}
    if kind == 'verbInstalled':
        c = {'verbInstalled': literal[2]}
        return c if pos else {'not': c}
    raise ValueError(kind)


class FixInterp:
    MAX_DEPTH = 10
    MAX_UNROLL = 128

    def __init__(self, source, game_id, store, umu_id=None):
        self.src = source
        self.tree = ast.parse(source)
        self.game_id = game_id
        self.store = store
        self.umu_id = umu_id
        self.raw = []
        self.phase = None
        self.tokens = {}
        self.token_count = 0
        self.sink_stack = []
        self.depth = 0
        self.globals = {}
        self.notes = []

    def seg(self, node):
        try:
            s = ast.get_source_segment(self.src, node, padded=True)
        except Exception:
            s = None
        if s is None:
            return ''
        return textwrap.dedent(s).strip('\n')

    def emit(self, step, guard):
        if guard == FALSE:
            return step
        self.raw.append((step, guard, self.phase))
        return step

    def emit_unsupported(self, reason, node, guard):
        self.emit({'op': 'unsupported', 'reason': reason, 'source': self.seg(node)}, guard)

    def bind_import(self, node, env):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    name = 'protonmain' if a.name == '__main__' else a.name
                    env[a.asname] = Module(name)
                else:
                    top = a.name.split('.')[0]
                    env[top] = Module(top)
        else:
            mod = node.module or ''
            for a in node.names:
                full = f'{mod}.{a.name}' if mod else a.name
                if full == 'protonfixes.util':
                    qual = 'util'
                elif full == 'protonfixes.logger.log':
                    qual = 'log'
                elif mod.startswith('protonfixes'):
                    qual = full[len('protonfixes.'):]
                else:
                    qual = full
                env[a.asname or a.name] = Module(qual)

    def load_module(self):
        g = self.globals
        for node in self.tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self.bind_import(node, g)
            elif isinstance(node, ast.FunctionDef):
                g[node.name] = LocalFunc(node)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                value_node = node.value
                if value_node is None:
                    continue
                try:
                    val = self.ev(value_node, g, TRUE)
                except Unsupported as e:
                    val = Opaque(e.reason, self.seg(value_node))
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        g[t.id] = val

    def run(self):
        self.load_module()
        funcs = {n.name: n for n in self.tree.body if isinstance(n, ast.FunctionDef)}
        has_main = False
        for early, main in ((('early_with_id', True), ('early', False)), (('main_with_id', True), ('main', False))):
            for name, with_id in (early, main):
                if name in funcs:
                    self.phase = 'early' if name.startswith('early') else None
                    has_main = has_main or not name.startswith('early')
                    args = [self.game_id] if with_id else []
                    self.invoke(funcs[name], args, {}, TRUE)
                    break
        self.phase = None
        if not has_main:
            self.notes.append('upstream fix has no main()')
        return self.raw

    def invoke(self, fnode, args, kwargs, guard):
        if self.depth >= self.MAX_DEPTH:
            raise Unsupported('call depth limit')
        env = dict(self.globals)
        params = fnode.args.args
        defaults = fnode.args.defaults
        first_default = len(params) - len(defaults)
        for i, p in enumerate(params):
            if i < len(args):
                env[p.arg] = args[i]
            elif p.arg in kwargs:
                env[p.arg] = kwargs[p.arg]
            elif i >= first_default:
                env[p.arg] = self.ev(defaults[i - first_default], self.globals, TRUE)
            else:
                raise Unsupported(f'missing argument {p.arg} for {fnode.name}')
        frame = Frame()
        self.depth += 1
        try:
            cont = self.exec_block(fnode.body, env, guard, frame)
        finally:
            self.depth -= 1
        return frame, cont

    def call_local_value(self, fnode, args, kwargs, guard):
        frame, cont = self.invoke(fnode, args, kwargs, guard)
        rets = [(g, v) for g, v in frame.returns if g != FALSE]
        if cont != FALSE:
            rets.append((cont, None))
        if not rets:
            return None
        opaques = [v for _, v in rets if isinstance(v, Opaque)]
        if opaques and len(opaques) == len(rets):
            return opaques[0]
        boolish = all(v is None or isinstance(v, (bool, Cond, EnvRead, Opaque)) for _, v in rets)
        if boolish and any(isinstance(v, (bool, Cond, EnvRead)) for _, v in rets):
            acc = FALSE
            for g, v in rets:
                acc = d_or(acc, d_and(g, self.truth(v)))
            return mk_cond(acc)
        values = [v for _, v in rets]
        first = values[0]
        for v in values[1:]:
            if not self.same_value(first, v):
                raise Unsupported(f'{fnode.name}() returns different values on different paths')
        return first

    @staticmethod
    def same_value(a, b):
        if type(a) is not type(b):
            return False
        if isinstance(a, (str, int, float, bool, type(None), tuple, list, dict)):
            return a == b
        if isinstance(a, Namespace):
            return a.__dict__ == b.__dict__
        return a is b

    @staticmethod
    def assigns_names(node):
        for sub in ast.walk(node):
            if isinstance(sub, ast.FunctionDef) and sub is not node:
                continue
            if isinstance(sub, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                for t in targets:
                    if isinstance(t, (ast.Name, ast.Tuple)):
                        return True
            if isinstance(sub, ast.NamedExpr):
                return True
            if isinstance(sub, ast.withitem) and sub.optional_vars is not None:
                return True
        return False

    def exec_block(self, stmts, env, guard, frame):
        i = 0
        while i < len(stmts):
            if guard == FALSE:
                return FALSE
            s = stmts[i]
            if isinstance(s, ast.If) and any(self.assigns_names(b) for b in s.body + s.orelse):
                test = self.cond_safe(s.test, env, guard)
                if test is True or test is False:
                    branch = s.body if test else s.orelse
                    stmts = list(branch) + list(stmts[i + 1:])
                    i = 0
                    continue
                conts = FALSE
                envs = []
                for bguard, body in ((d_and(guard, test.dnf), s.body), (d_and(guard, d_not(test.dnf)), s.orelse)):
                    if bguard == FALSE:
                        continue
                    e2 = dict(env)
                    conts = d_or(conts, self.exec_block(list(body) + list(stmts[i + 1:]), e2, bguard, frame))
                    envs.append(e2)
                self.merge_envs(env, envs)
                return conts
            guard = self.exec_stmt(s, env, guard, frame)
            i += 1
        return guard

    def merge_envs(self, env, envs):
        if not envs:
            return
        keys = set()
        for e in envs:
            keys.update(e)
        for k in keys:
            vals = [e.get(k, None) for e in envs]
            if all(self.same_value(vals[0], v) for v in vals[1:]):
                env[k] = vals[0]
            else:
                env[k] = Opaque(f'value of {k} depends on a branch')

    def exec_stmt(self, s, env, guard, frame):
        try:
            return self._exec_stmt(s, env, guard, frame)
        except Unsupported as e:
            self.emit_unsupported(e.reason, s, guard)
            return guard

    def _exec_stmt(self, s, env, guard, frame):
        if isinstance(s, ast.Expr):
            if isinstance(s.value, ast.Constant):
                return guard
            if isinstance(s.value, ast.Call):
                self.call(s.value, env, guard, stmt=True)
                return guard
            self.ev(s.value, env, guard)
            return guard
        if isinstance(s, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            self.exec_assign(s, env, guard)
            return guard
        if isinstance(s, ast.If):
            test = self.cond_safe(s.test, env, guard)
            if test is True:
                return self.exec_block(s.body, env, guard, frame)
            if test is False:
                return self.exec_block(s.orelse, env, guard, frame)
            c1 = self.exec_block(s.body, env, d_and(guard, test.dnf), frame)
            c2 = self.exec_block(s.orelse, env, d_and(guard, d_not(test.dnf)), frame)
            return d_or(c1, c2)
        if isinstance(s, ast.Return):
            if s.value is None:
                val = None
            else:
                try:
                    val = self.ev_raw(s.value, env, guard)
                except EffectUnsupported as e:
                    self.emit_unsupported(e.reason, s, guard)
                    val = Opaque(e.reason, self.seg(s.value))
                except Unsupported as e:
                    val = Opaque(e.reason, self.seg(s.value))
            frame.returns.append((guard, val))
            return FALSE
        if isinstance(s, ast.For):
            return self.exec_for(s, env, guard, frame)
        if isinstance(s, ast.While):
            return self.exec_while(s, env, guard, frame)
        if isinstance(s, ast.With):
            return self.exec_with(s, env, guard, frame)
        if isinstance(s, ast.Try):
            cont = self.exec_block(s.body, env, guard, frame)
            cont = self.exec_block(s.orelse, env, cont, frame)
            if s.finalbody:
                cont = self.exec_block(s.finalbody, env, cont, frame)
            return cont
        if isinstance(s, ast.FunctionDef):
            env[s.name] = LocalFunc(s)
            return guard
        if isinstance(s, (ast.Import, ast.ImportFrom)):
            self.bind_import(s, env)
            return guard
        if isinstance(s, (ast.Pass, ast.Global, ast.Nonlocal)):
            return guard
        if isinstance(s, ast.Delete):
            for t in s.targets:
                if isinstance(t, ast.Subscript) and self.is_environ(t.value, env):
                    key = self.ev(t.slice, env, guard)
                    self.emit({'op': 'env', 'unset': [key]}, guard)
            return guard
        raise Unsupported(f'{type(s).__name__} statement')

    def is_environ(self, node, env):
        try:
            v = self.ev_raw(node, env, TRUE)
        except Unsupported:
            return False
        return isinstance(v, Module) and v.name == 'os.environ'

    def exec_assign(self, s, env, guard):
        if isinstance(s, ast.AugAssign):
            if not isinstance(s.target, ast.Name):
                raise Unsupported('augmented assignment to a non-name')
            cur = env.get(s.target.id)
            try:
                rhs = self.ev(s.value, env, guard)
                if isinstance(cur, Opaque):
                    raise Unsupported(cur.reason)
                val = self.binop(s.op, cur, rhs)
            except Unsupported as e:
                val = Opaque(e.reason, self.seg(s))
            env[s.target.id] = val
            return
        targets = s.targets if isinstance(s, ast.Assign) else [s.target]
        if s.value is None:
            return
        for t in targets:
            if isinstance(t, ast.Subscript):
                base = self.ev_raw(t.value, env, guard)
                if isinstance(base, Module) and base.name == 'os.environ':
                    key = self.ev(t.slice, env, guard)
                    val = self.ev(s.value, env, guard)
                    if not isinstance(key, str) or not isinstance(val, str):
                        raise Unsupported('non-string environment assignment')
                    self.emit({'op': 'env', 'set': {key: val}}, guard)
                    continue
                if isinstance(base, JsonDoc):
                    key = self.ev(t.slice, env, guard)
                    val = self.ev(s.value, env, guard)
                    if not isinstance(key, str) or not isinstance(val, (str, int, float, bool, type(None), list, dict)):
                        raise Unsupported('unsupported JSON assignment')
                    self.emit({'op': 'json_set', 'path': norm_path(base.path), 'key': key, 'value': val}, guard)
                    continue
                if isinstance(base, (dict, list)):
                    try:
                        key = self.ev(t.slice, env, guard)
                        base[key] = self.ev(s.value, env, guard)
                    except (Unsupported, TypeError, IndexError):
                        pass
                    continue
                if isinstance(base, Opaque):
                    continue
                raise Unsupported('assignment to a subscript')
            if isinstance(t, ast.Attribute):
                raise Unsupported('assignment to an attribute')
        try:
            val = self.ev_raw(s.value, env, guard)
        except EffectUnsupported as e:
            self.emit_unsupported(e.reason, s, guard)
            val = Opaque(e.reason, self.seg(s.value))
        except Unsupported as e:
            val = Opaque(e.reason, self.seg(s.value))
        for t in targets:
            if isinstance(t, ast.Name):
                env[t.id] = val
            elif isinstance(t, (ast.Tuple, ast.List)):
                if isinstance(val, (tuple, list)) and len(val) == len(t.elts):
                    for sub, v in zip(t.elts, val):
                        if isinstance(sub, ast.Name):
                            env[sub.id] = v
                else:
                    for sub in t.elts:
                        if isinstance(sub, ast.Name):
                            env[sub.id] = val if isinstance(val, Opaque) else Opaque('tuple unpacking of a non-static value', self.seg(s.value))

    def exec_for(self, s, env, guard, frame):
        if s.orelse:
            raise Unsupported('for-else')
        it = self.ev_raw(s.iter, env, guard)
        if isinstance(it, OpenFile):
            return self.exec_line_scan(s, it, env, guard)
        if isinstance(it, Opaque):
            raise Unsupported(it.reason)
        if isinstance(it, dict):
            items = list(it.keys())
        elif isinstance(it, (list, tuple, str)):
            items = list(it)
        else:
            raise Unsupported(f'loop over {type(it).__name__}')
        if len(items) > self.MAX_UNROLL:
            raise Unsupported('loop too long to unroll')
        for sub in ast.walk(s):
            if isinstance(sub, (ast.Break, ast.Continue)):
                raise Unsupported('break/continue in loop')
        start = len(self.raw)
        entry_guard = guard
        for item in items:
            self.bind_target(s.target, item, env)
            guard = self.exec_block(s.body, env, guard, frame)
            if guard == FALSE:
                break
        bad = [st for st, g, p in self.raw[start:] if st['op'] == 'unsupported']
        if bad:
            del self.raw[start:]
            self.emit({'op': 'unsupported', 'reason': bad[0]['reason'], 'source': self.seg(s)}, entry_guard)
            return entry_guard
        return guard

    def bind_target(self, target, value, env):
        if isinstance(target, ast.Name):
            env[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (tuple, list)) and len(value) == len(target.elts):
            for t, v in zip(target.elts, value):
                self.bind_target(t, v, env)
        else:
            raise Unsupported('unsupported loop target')

    def exec_line_scan(self, s, f, env, guard):
        if not isinstance(s.target, ast.Name):
            raise Unsupported('unsupported loop over a file')
        line_name = s.target.id
        body = s.body
        if (
            len(body) == 1 and isinstance(body[0], ast.If) and not body[0].orelse
            and isinstance(body[0].test, ast.Compare) and len(body[0].test.ops) == 1
            and isinstance(body[0].test.ops[0], ast.In)
            and isinstance(body[0].test.left, ast.Constant) and isinstance(body[0].test.left.value, str)
            and isinstance(body[0].test.comparators[0], ast.Name)
            and body[0].test.comparators[0].id == line_name
        ):
            inner = [x for x in body[0].body if not isinstance(x, ast.Break)]
            if (
                len(inner) == 1 and isinstance(inner[0], ast.Assign) and len(inner[0].targets) == 1
                and isinstance(inner[0].targets[0], ast.Name)
                and isinstance(inner[0].value, ast.Constant) and inner[0].value.value is True
            ):
                var = inner[0].targets[0].id
                prev = env.get(var, False)
                prev_dnf = self.truth(prev) if not isinstance(prev, Opaque) else FALSE
                literal = lit('fileContains', norm_path(f.path), body[0].test.left.value)
                env[var] = mk_cond(d_or(prev_dnf, (frozenset([literal]),)))
                return guard
        raise Unsupported('loop over the lines of a file')

    def exec_while(self, s, env, guard, frame):
        if self.sink_stack:
            uses_readinto = any(
                isinstance(n, ast.Attribute) and n.attr in ('readinto', 'read') for n in ast.walk(s.test)
            )
            if uses_readinto:
                tok = self.sink_stack[-1]
                for n in ast.walk(s):
                    if (
                        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == 'update' and isinstance(n.func.value, ast.Name)
                    ):
                        h = env.get(n.func.value.id)
                        if isinstance(h, Hasher):
                            h.token = tok
                return guard
        raise Unsupported('while loop')

    def exec_with(self, s, env, guard, frame):
        handles = []
        pushed = 0
        for item in s.items:
            ctx = item.context_expr
            val = self.ev_raw(ctx, env, guard)
            if isinstance(val, Opaque):
                raise Unsupported(val.reason)
            if isinstance(val, OpenFile) and self.sink_stack and ('w' in val.mode) and 'b' in val.mode:
                tok = self.sink_stack[-1]
                val.token = tok
                if isinstance(ctx, ast.Call) and ctx.args and isinstance(ctx.args[0], ast.Name):
                    path_val = env.get(ctx.args[0].id)
                    if isinstance(path_val, str):
                        self.tokens[path_val] = tok
            elif isinstance(val, UrlResp):
                self.token_count += 1
                tok = Token(self.token_count, val.url)
                self.sink_stack.append(tok)
                pushed += 1
            elif not isinstance(val, (OpenFile, ZipHandle)):
                raise Unsupported(f'with-statement over {type(val).__name__}')
            if item.optional_vars is not None:
                if not isinstance(item.optional_vars, ast.Name):
                    raise Unsupported('with-target is not a name')
                env[item.optional_vars.id] = val
            handles.append(val)
        try:
            cont = self.exec_block(s.body, env, guard, frame)
        finally:
            for _ in range(pushed):
                self.sink_stack.pop()
        body_writes = set()
        for n in ast.walk(ast.Module(body=list(s.body), type_ignores=[])):
            if isinstance(n, ast.Call):
                if isinstance(n.func, ast.Attribute) and n.func.attr in ('write', 'writelines') \
                        and isinstance(n.func.value, ast.Name):
                    body_writes.add(n.func.value.id)
                for a in n.args:
                    if isinstance(a, ast.Name):
                        body_writes.add(a.id)
        names = [it.optional_vars.id if isinstance(it.optional_vars, ast.Name) else None for it in s.items]
        for h, hname in zip(handles, names):
            if hname in body_writes:
                continue
            if isinstance(h, OpenFile) and h.token is None and not h.handled and h.step is None:
                if 'w' in h.mode:
                    self.emit({'op': 'write_file', 'path': norm_path(h.path), 'content': '', 'ifMissing': False,
                               'encoding': self.encoding(h.encoding)}, guard)
                elif 'a' in h.mode:
                    self.emit({'op': 'file', 'action': 'touch', 'to': norm_path(h.path)}, guard)
        return cont

    @staticmethod
    def encoding(enc):
        if enc in (None, 'utf-8', 'utf8', 'ascii', 'UTF-8'):
            return 'utf-8'
        return enc

    def truth(self, v):
        if isinstance(v, bool):
            return TRUE if v else FALSE
        if v is None:
            return FALSE
        if isinstance(v, Cond):
            return v.dnf
        if isinstance(v, EnvRead):
            if v.default:
                return TRUE
            return (frozenset([env_literal(v.name)]),)
        if isinstance(v, Opaque):
            return (frozenset([lit('opaque', v.reason, v.source)]),)
        if isinstance(v, (str, int, float, list, tuple, dict)):
            return TRUE if v else FALSE
        return TRUE

    def cond_safe(self, node, env, guard):
        try:
            c = self.cond(node, env, guard)
        except Unsupported as e:
            return Cond((frozenset([lit('opaque', e.reason, self.seg(node))]),))
        return c

    def cond(self, node, env, guard):
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                acc = TRUE
                for v in node.values:
                    c = self.cond_safe(v, env, d_and(guard, acc))
                    acc = d_and(acc, to_dnf(c))
                    if acc == FALSE:
                        break
            else:
                acc = FALSE
                for v in node.values:
                    c = self.cond_safe(v, env, d_and(guard, d_not(acc)))
                    acc = d_or(acc, to_dnf(c))
                    if acc == TRUE:
                        break
            return mk_cond(acc)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            c = self.cond_safe(node.operand, env, guard)
            return mk_cond(d_not(to_dnf(c)))
        if isinstance(node, ast.Compare):
            return self.compare(node, env, guard)
        v = self.ev_raw(node, env, guard)
        return mk_cond(self.truth(v))

    def compare(self, node, env, guard):
        if len(node.ops) != 1:
            raise Unsupported('chained comparison')
        op = node.ops[0]
        left_node, right_node = node.left, node.comparators[0]
        left = self.ev_raw(left_node, env, guard)
        right = self.ev_raw(right_node, env, guard)
        if isinstance(op, (ast.In, ast.NotIn)):
            positive = isinstance(op, ast.In)
            if isinstance(right, Module) and right.name == 'os.environ' and isinstance(left, str):
                c = self.env_literal_value(left)
                return c if positive else mk_cond(d_not(to_dnf(c)))
            if isinstance(right, JsonDoc) and isinstance(left, str):
                literal = lit('jsonHas', norm_path(right.path), left)
                d = (frozenset([literal if positive else neg(literal)]),)
                return mk_cond(d)
            if isinstance(left, EnvRead) and isinstance(right, (list, tuple)) and all(isinstance(x, str) for x in right):
                d = FALSE
                for x in right:
                    d = d_or(d, self.env_equals(left, x))
                return mk_cond(d if positive else d_not(d))
        if isinstance(op, (ast.Is, ast.IsNot)):
            if isinstance(left, Opaque) or isinstance(right, Opaque):
                o = left if isinstance(left, Opaque) else right
                raise Unsupported(o.reason)
            res = (left is right) or (left is None and right is None)
            if isinstance(left, (EnvRead,)) or isinstance(right, (EnvRead,)):
                er = left if isinstance(left, EnvRead) else right
                d = (frozenset([env_literal(er.name)]),)
                return mk_cond(d_not(d) if isinstance(op, ast.Is) else d)
            return res if isinstance(op, ast.Is) else not res
        if isinstance(op, (ast.Eq, ast.NotEq)):
            eq = isinstance(op, ast.Eq)
            for a, b in ((left, right), (right, left)):
                if isinstance(a, EnvRead) and isinstance(b, str):
                    d = self.env_equals(a, b)
                    return mk_cond(d if eq else d_not(d))
                if isinstance(a, Digest) and isinstance(b, str):
                    d = (frozenset([lit('sha256ok', a.token.id, b.lower())]),)
                    return mk_cond(d if eq else d_not(d))
                if isinstance(a, FileDigest) and isinstance(b, str):
                    d = (frozenset([lit('fileHash', norm_path(a.path), b.lower())]),)
                    return mk_cond(d if eq else d_not(d))
                if isinstance(a, FileContent) and isinstance(b, str):
                    d = (frozenset([lit('contentDiffers', norm_path(a.path), b)]),)
                    return mk_cond(d_not(d) if eq else d)
                if isinstance(a, StatMode) and isinstance(b, ReadonlyMode):
                    return not eq
        for v in (left, right):
            if isinstance(v, Opaque):
                raise Unsupported(v.reason)
            if not isinstance(v, (str, int, float, bool, type(None), tuple, list, dict, set)):
                raise Unsupported(f'comparison involving {type(v).__name__}')
            if is_placeholder_value(v):
                raise Unsupported('comparison on the screen resolution')
        try:
            return bool(self.py_compare(op, left, right))
        except TypeError as e:
            raise Unsupported(f'comparison failed: {e}')

    @staticmethod
    def env_equals(a, b):
        if a.lower and b != b.lower():
            return FALSE
        if b == '' and a.default in (None, ''):
            return d_not((frozenset([env_literal(a.name)]),))
        if a.default not in (None, '') and b == a.default:
            raise Unsupported('environment comparison against its default')
        return (frozenset([env_literal(a.name, b)]),)

    @staticmethod
    def py_compare(op, a, b):
        return {
            ast.Eq: lambda: a == b, ast.NotEq: lambda: a != b, ast.Lt: lambda: a < b,
            ast.LtE: lambda: a <= b, ast.Gt: lambda: a > b, ast.GtE: lambda: a >= b,
            ast.In: lambda: a in b, ast.NotIn: lambda: a not in b,
            ast.Is: lambda: a is b, ast.IsNot: lambda: a is not b,
        }[type(op)]()

    def env_static(self, name):
        if name == 'UMU_ID':
            return True, self.umu_id
        if name == 'STORE':
            return True, (None if self.store in ('steam', 'umu') else self.store)
        if name in ('SteamAppId', 'SteamGameId'):
            return True, (self.game_id if self.store == 'steam' else None)
        return False, None

    def env_literal_value(self, name):
        known, val = self.env_static(name)
        if known:
            return val is not None
        return Cond((frozenset([env_literal(name)]),))

    def ev(self, node, env, guard):
        v = self.ev_raw(node, env, guard)
        if isinstance(v, Opaque):
            raise Unsupported(v.reason)
        return v

    def ev_raw(self, node, env, guard):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            if node.id in self.globals:
                return self.globals[node.id]
            if node.id in ('str', 'int', 'hex', 'len', 'range', 'any', 'all', 'open', 'print', 'dict',
                           'list', 'tuple', 'bool', 'isinstance', 'sorted', 'bytearray', 'memoryview', 'float'):
                return Module('builtins.' + node.id)
            raise Unsupported(f'unknown name {node.id}')
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant):
                    parts.append(str(v.value))
                else:
                    x = self.ev(v.value, env, guard)
                    if v.format_spec is not None or v.conversion not in (-1, None):
                        if is_placeholder_value(x):
                            raise Unsupported('formatted screen resolution')
                    parts.append(self.to_str(x))
            return ''.join(parts)
        if isinstance(node, ast.Attribute):
            base = self.ev_raw(node.value, env, guard)
            return self.attr(base, node.attr)
        if isinstance(node, ast.Subscript):
            base = self.ev(node.value, env, guard)
            key = self.ev(node.slice, env, guard)
            if isinstance(base, Module) and base.name == 'os.environ':
                known, val = self.env_static(key)
                if known and val is not None:
                    return val
                raise Unsupported(f'reads the host environment variable {key}')
            if isinstance(base, (dict, list, tuple, str)) and not isinstance(key, slice):
                try:
                    return base[key]
                except (KeyError, IndexError, TypeError):
                    raise Unsupported('static subscript failed')
            raise Unsupported(f'subscript of {type(base).__name__}')
        if isinstance(node, ast.Slice):
            lo = self.ev(node.lower, env, guard) if node.lower else None
            hi = self.ev(node.upper, env, guard) if node.upper else None
            return slice(lo, hi)
        if isinstance(node, ast.BinOp):
            a = self.ev(node.left, env, guard)
            b = self.ev(node.right, env, guard)
            return self.binop(node.op, a, b)
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return self.cond(node, env, guard)
            v = self.ev(node.operand, env, guard)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if isinstance(node.op, ast.USub):
                    return -v
                if isinstance(node.op, ast.Invert) and isinstance(v, int):
                    return ~v
                if isinstance(node.op, ast.UAdd):
                    return v
            raise Unsupported('unary operator on a non-number')
        if isinstance(node, (ast.BoolOp, ast.Compare)):
            return self.cond(node, env, guard)
        if isinstance(node, ast.Call):
            return self.call(node, env, guard, stmt=False)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            vals = [self.ev(e, env, guard) for e in node.elts]
            return tuple(vals) if isinstance(node, ast.Tuple) else vals
        if isinstance(node, ast.Dict):
            out = {}
            for k, v in zip(node.keys, node.values):
                if k is None:
                    raise Unsupported('dict unpacking')
                out[self.ev(k, env, guard)] = self.ev(v, env, guard)
            return out
        if isinstance(node, ast.IfExp):
            c = self.cond(node.test, env, guard)
            if c is True:
                return self.ev_raw(node.body, env, guard)
            if c is False:
                return self.ev_raw(node.orelse, env, guard)
            raise Unsupported('conditional expression on a runtime condition')
        raise Unsupported(f'{type(node).__name__} expression')

    def to_str(self, x):
        if isinstance(x, bool) or x is None or isinstance(x, (int, float)):
            return str(x)
        if isinstance(x, str):
            return str(x)
        if isinstance(x, RandomBytes):
            raise Unsupported('random bytes')
        raise Unsupported(f'cannot format {type(x).__name__}')

    def binop(self, op, a, b):
        if is_placeholder_value(a) or is_placeholder_value(b):
            if isinstance(op, ast.Add) and isinstance(a, str) and isinstance(b, str):
                return a + b
            raise Unsupported('derived screen resolution expression')
        if isinstance(op, ast.Div) and isinstance(a, PathV):
            if not isinstance(b, str):
                raise Unsupported('path join with a non-string')
            return PathV(posixpath.join(a, b))
        if isinstance(op, ast.BitAnd) and isinstance(a, StatMode) and isinstance(b, int):
            if b < 0 and not (b & stat_module.S_IWUSR):
                return ReadonlyMode(a.path)
            raise Unsupported('unsupported file mode change')
        ok_types = (str, int, float, bool, list, tuple)
        if not isinstance(a, ok_types) or not isinstance(b, ok_types):
            raise Unsupported(f'operator on {type(a).__name__} and {type(b).__name__}')
        try:
            if isinstance(op, ast.Add):
                return a + b
            if isinstance(op, ast.Sub):
                return a - b
            if isinstance(op, ast.Mult):
                return a * b
            if isinstance(op, ast.Div):
                return a / b
            if isinstance(op, ast.FloorDiv):
                return a // b
            if isinstance(op, ast.Mod):
                return a % b
            if isinstance(op, ast.BitOr):
                return a | b
            if isinstance(op, ast.BitAnd):
                return a & b
        except (TypeError, ZeroDivisionError, ValueError) as e:
            raise Unsupported(f'operator failed: {e}')
        raise Unsupported(f'operator {type(op).__name__}')

    def attr(self, base, name):
        if isinstance(base, Opaque):
            raise Unsupported(base.reason)
        if isinstance(base, Module):
            q = base.name
            if q == 'util.OverrideOrder':
                return OVERRIDE_ORDER[name]
            if q == 'util.DosDevice':
                return DOS_DEVICE[name]
            if q == 'util.BasePath':
                return BASE_PATH[name]
            if q == 'stat' and name.startswith('S_'):
                return getattr(stat_module, name)
            if q == 're' and name.isupper():
                return int(getattr(re, name, 0))
            if q == 'os' and name == 'sep':
                return '/'
            if q in ('sys',) and name == 'argv':
                raise Unsupported('reads the Proton command line')
            return Module(f'{q}.{name}')
        if isinstance(base, PathV):
            if name == 'parent':
                return PathV(posixpath.dirname(base))
            if name == 'name':
                return posixpath.basename(base)
            if name == 'stem':
                return posixpath.splitext(posixpath.basename(base))[0]
            if name == 'suffix':
                return posixpath.splitext(base)[1]
            return ('pathmethod', base, name)
        if isinstance(base, StatObj):
            if name == 'st_mode':
                return StatMode(base.path)
            raise Unsupported(f'file stat {name}')
        if isinstance(base, UrlResp):
            if name == 'status':
                return 200
            return ('respmethod', base, name)
        if isinstance(base, Namespace):
            if name in base.__dict__:
                return base.__dict__[name]
            raise Unsupported(f'unknown attribute {name}')
        if isinstance(base, (str, dict, list, OpenFile, Hasher, Digest, FileDigest, ZipHandle, RandomBytes,
                             EnvRead, JsonDoc, LocalFunc)):
            return ('method', base, name)
        raise Unsupported(f'attribute {name} of {type(base).__name__}')

    def call(self, node, env, guard, stmt):
        func = self.ev_raw(node.func, env, guard)
        if isinstance(func, Module) and (func.name == 'log' or func.name.startswith('log.') or func.name == 'builtins.print'):
            return None
        try:
            return self._call(func, node, env, guard, stmt)
        except EffectUnsupported:
            raise
        except Unsupported as e:
            if isinstance(func, Module) and is_effect_call(func.name):
                raise EffectUnsupported(e.reason)
            raise

    def _call(self, func, node, env, guard, stmt):
        args = []
        for a in node.args:
            if isinstance(a, ast.Starred):
                v = self.ev(a.value, env, guard)
                if not isinstance(v, (list, tuple)):
                    raise Unsupported('star-args of a non-sequence')
                args.extend(v)
            elif isinstance(a, ast.GeneratorExp):
                args.append(a)
            else:
                args.append(self.ev_raw(a, env, guard))
        kwargs = {}
        for k in node.keywords:
            if k.arg is None:
                raise Unsupported('**kwargs')
            kwargs[k.arg] = self.ev_raw(k.value, env, guard)
        if isinstance(func, Opaque):
            raise Unsupported(func.reason)
        if isinstance(func, LocalFunc):
            for a in args:
                if isinstance(a, ast.AST):
                    raise Unsupported('generator argument')
            if stmt:
                self.invoke(func.node, args, kwargs, guard)
                return None
            return self.call_local_value(func.node, args, kwargs, guard)
        if isinstance(func, Module):
            return self.call_qual(func.name, args, kwargs, node, env, guard, stmt)
        if isinstance(func, tuple) and len(func) == 3:
            kind, obj, meth = func
            return self.call_method(obj, meth, args, kwargs, node, env, guard)
        raise Unsupported(f'call of {type(func).__name__}')

    def req(self, args, kwargs, idx, name, default=Ellipsis):
        if idx < len(args):
            v = args[idx]
        elif name in kwargs:
            v = kwargs[name]
        elif default is not Ellipsis:
            return default
        else:
            raise Unsupported(f'missing argument {name}')
        if isinstance(v, Opaque):
            raise Unsupported(v.reason)
        return v

    def token_for(self, p):
        if isinstance(p, str) and p in self.tokens:
            return self.tokens[p]
        return None

    def file_step(self, action, guard, src=None, dst=None, recursive=False):
        step = {'op': 'file', 'action': action}
        if src is not None:
            step['from'] = norm_path(src) if action != 'symlink' else self.symlink_target(src)
        if dst is not None:
            step['to'] = norm_path(dst)
        if recursive:
            step['recursive'] = True
        for k in ('from', 'to'):
            if k in step and is_host_path(step[k]) and not (action == 'symlink' and k == 'from' and not step[k].startswith('/')):
                raise Unsupported('file operation on a host path outside the prefix or game')
        self.emit(step, guard)

    @staticmethod
    def symlink_target(src):
        if not isinstance(src, str):
            raise Unsupported('symlink target is not a string')
        if src.startswith('${') or src.startswith('/'):
            return norm_path(src)
        return src

    def call_qual(self, q, args, kwargs, node, env, guard, stmt):
        if q.startswith('util.') and q.count('.') == 1:
            return self.util_call(q[5:], args, kwargs, node, env, guard)
        if q.startswith('builtins.'):
            return self.builtin(q[9:], args, kwargs, node, env, guard)
        R = lambda i, n, d=Ellipsis: self.req(args, kwargs, i, n, d)
        if q == 'os.path.join':
            parts = [R(i, 'p') for i in range(len(args))]
            for p in parts:
                if not isinstance(p, str):
                    raise Unsupported('path join of a non-string')
            return posixpath.join(*parts)
        if q in ('os.path.exists', 'os.path.isfile', 'os.path.isdir', 'os.path.lexists'):
            p = R(0, 'path')
            if p == '':
                return False
            if self.token_for(p):
                return True
            return Cond((frozenset([lit('fileExists', norm_path(p))]),))
        if q in ('os.path.basename', 'os.path.dirname', 'os.path.abspath', 'os.path.normpath', 'os.path.splitext'):
            p = R(0, 'p')
            if not isinstance(p, str):
                raise Unsupported('path function on a non-string')
            return {'os.path.basename': posixpath.basename, 'os.path.dirname': posixpath.dirname,
                    'os.path.abspath': lambda x: x, 'os.path.normpath': posixpath.normpath,
                    'os.path.splitext': posixpath.splitext}[q](p)
        if q == 'os.getcwd':
            return '${install}'
        if q in ('os.makedirs', 'os.mkdir'):
            self.file_step('mkdir', guard, dst=R(0, 'name'))
            return None
        if q in ('os.remove', 'os.unlink', 'os.rmdir'):
            p = R(0, 'path')
            if self.token_for(p):
                return None
            self.file_step('delete', guard, dst=p)
            return None
        if q in ('os.rename', 'os.replace', 'shutil.move'):
            self.file_step('move', guard, src=R(0, 'src'), dst=R(1, 'dst'))
            return None
        if q == 'os.symlink':
            self.file_step('symlink', guard, src=R(0, 'src'), dst=R(1, 'dst'))
            return None
        if q in ('os.environ.get', 'os.getenv'):
            name = R(0, 'key')
            default = R(1, 'default', None)
            known, val = self.env_static(name)
            if known:
                return val if val is not None else default
            return EnvRead(name, default)
        if q in ('os.environ.pop', 'os.unsetenv'):
            self.emit({'op': 'env', 'unset': [R(0, 'key')]}, guard)
            return None
        if q == 'os.urandom':
            return RandomBytes()
        if q == 'os.chdir':
            p = R(0, 'path')
            if not isinstance(p, str):
                raise EffectUnsupported('changes the working directory to a runtime path')
            self.emit({'op': 'cwd', 'path': norm_path(p)}, guard)
            return None
        if q == 'glob.escape':
            return R(0, 'pathname')
        if q in ('shutil.rmtree',):
            self.file_step('delete', guard, dst=R(0, 'path'), recursive=True)
            return None
        if q in ('shutil.copy', 'shutil.copy2', 'shutil.copyfile'):
            self.file_step('copy', guard, src=R(0, 'src'), dst=R(1, 'dst'))
            return None
        if q == 'shutil.copytree':
            self.file_step('copy', guard, src=R(0, 'src'), dst=R(1, 'dst'), recursive=True)
            return None
        if q in ('subprocess.call', 'subprocess.run', 'subprocess.check_call', 'subprocess.Popen'):
            return self.subprocess_call(args, kwargs, guard)
        if q == 'urllib.request.urlretrieve':
            url = R(0, 'url')
            dest = R(1, 'filename')
            if not isinstance(url, str) or not isinstance(dest, str):
                raise Unsupported('download with non-static arguments')
            self.token_count += 1
            tok = Token(self.token_count, url)
            self.tokens[dest] = tok
            return (dest, None)
        if q == 'urllib.request.urlopen':
            url = R(0, 'url')
            if not isinstance(url, str):
                raise Unsupported('download with non-static url')
            return UrlResp(url)
        if q == 'zipfile.ZipFile':
            p = R(0, 'file')
            tok = self.token_for(p)
            if tok is None:
                raise Unsupported('opens a zip that was not downloaded by the fix')
            return ZipHandle(tok)
        if q == 'zipfile.is_zipfile':
            if self.token_for(R(0, 'filename')):
                return True
            raise Unsupported('zip check on a non-download')
        if q == 'hashlib.sha256':
            if not args:
                return Hasher()
            data = R(0, 'data')
            if isinstance(data, TokenContent):
                return Digest(data.token)
            if isinstance(data, FileContent):
                return FileDigest(data.path)
            raise Unsupported('hash of non-file data')
        if q == 'tempfile.mkdtemp':
            return '${tmp}'
        if q == 'pathlib.Path':
            parts = [R(i, 'p') for i in range(len(args))]
            if not parts:
                return PathV('${install}')
            for p in parts:
                if not isinstance(p, str):
                    raise Unsupported('Path() of a non-string')
            joined = posixpath.join(*parts)
            if len(joined) > 1:
                joined = joined.rstrip('/')
            return PathV(joined)
        if q == 'json.load':
            f = R(0, 'fp')
            if isinstance(f, OpenFile) and f.token is None:
                return JsonDoc(f.path)
            raise Unsupported('json.load of a non-file')
        if q == 'json.dump':
            data = R(0, 'obj')
            f = R(1, 'fp')
            if isinstance(data, JsonDoc) and isinstance(f, OpenFile) and norm_path(f.path) == norm_path(data.path):
                f.handled = True
                return None
            raise Unsupported('json.dump of a non-loaded document')
        if q == 'time.sleep':
            return None
        if q == 'getpass.getuser':
            return '${user}'
        if q == 'threading.Thread':
            raise EffectUnsupported('starts a background thread')
        if q in ('os.kill', 'signal.raise_signal'):
            raise EffectUnsupported('signals a host process')
        if q == 're.sub':
            raise Unsupported('regex rewrite of file content')
        if q == 'str.lower':
            v = R(0, 's')
            if isinstance(v, str):
                return v.lower()
            if isinstance(v, EnvRead):
                return EnvRead(v.name, None if v.default is None else str(v.default).lower(), lower=True)
            raise Unsupported('lower() of a runtime value')
        if q == 'util.ReplaceType':
            return Namespace(from_value=R(0, 'from_value'), to_value=R(1, 'to_value'))
        if q in ('protonmain.g_session.env.copy',):
            raise Unsupported('reads the Proton session environment')
        if q.startswith('util.ReplaceType'):
            return Namespace(from_value=R(0, 'from_value'), to_value=R(1, 'to_value'))
        raise Unsupported(f'calls {q}()')

    def subprocess_call(self, args, kwargs, guard):
        try:
            cmd = self.req(args, kwargs, 0, 'args')
        except Unsupported as e:
            raise EffectUnsupported(e.reason)
        if kwargs.get('shell') is True or isinstance(cmd, str):
            if isinstance(cmd, (list, tuple)) and all(isinstance(a, str) for a in cmd):
                text = ' '.join(cmd)
            elif isinstance(cmd, str):
                text = cmd
            else:
                raise EffectUnsupported('runs a shell command built from runtime values')
            try:
                argv = shlex.split(text)
            except ValueError:
                argv = []
            if argv and argv[0] == 'sed' and self.sed_step(argv[1:], guard):
                return None
            raise EffectUnsupported(f'runs a shell command: {text[:120]}')
        if not isinstance(cmd, (list, tuple)) or not cmd:
            raise EffectUnsupported('subprocess with a non-static command')
        argv = []
        for a in cmd:
            if isinstance(a, Opaque):
                raise EffectUnsupported(a.reason)
            if not isinstance(a, str):
                raise EffectUnsupported('subprocess argument is not a string')
            argv.append(a)
        prog = posixpath.basename(argv[0])
        flags = [a for a in argv[1:] if a.startswith('-')]
        rest = [a for a in argv[1:] if not a.startswith('-')]
        recursive = any(f in ('-r', '-R', '-a', '--recursive') for f in flags)
        if prog == 'cp' and len(rest) == 2:
            self.file_step('copy', guard, src=rest[0], dst=rest[1], recursive=recursive)
            return None
        if prog == 'mv' and len(rest) == 2:
            self.file_step('move', guard, src=rest[0], dst=rest[1])
            return None
        if prog == 'ln' and '-s' in flags and len(rest) == 2:
            dst = rest[1]
            if dst.endswith('/'):
                dst = dst + posixpath.basename(rest[0])
            self.file_step('symlink', guard, src=rest[0], dst=dst)
            return None
        if prog == 'touch' and rest:
            for p in rest:
                self.file_step('touch', guard, dst=p)
            return None
        if prog == 'rm' and rest:
            for p in rest:
                self.file_step('delete', guard, dst=p, recursive=recursive)
            return None
        if prog == 'mkdir' and rest:
            for p in rest:
                self.file_step('mkdir', guard, dst=p)
            return None
        if prog in ('wine', 'wine64') and len(argv) >= 3 and argv[1].lower() in ('regedit', 'regedit.exe'):
            target = [a for a in argv[2:] if not a.startswith('/')]
            if len(target) == 1:
                self.emit({'op': '_regimport', 'path': win_to_host(target[0])}, guard)
                return None
        if prog == 'sed' and self.sed_step(argv[1:], guard):
            return None
        raise EffectUnsupported(f'runs the external program {prog}')

    def sed_step(self, args, guard):
        if len(args) != 3 or args[0] != '-i':
            return False
        script, path = args[1], args[2]
        m = re.fullmatch(r's(.)(.*?)\1(.*?)\1(g?)', script)
        if not m or '\\' in m.group(2) or '\\' in m.group(3) or '&' in m.group(3):
            return False
        pattern, repl = m.group(2), m.group(3)
        step = {'op': 'text_replace', 'path': norm_path(path), 'from': pattern, 'to': repl}
        if re.search(r'[][.*^$]', pattern):
            return False
        if not m.group(4):
            step['note'] = 'sed replaces the first match on each line upstream'
        if is_host_path(step['path']):
            return False
        self.emit(step, guard)
        return True

    def builtin(self, name, args, kwargs, node, env, guard):
        R = lambda i, n, d=Ellipsis: self.req(args, kwargs, i, n, d)
        if name == 'str.lower':
            return self.call_qual('str.lower', args, kwargs, node, env, guard, False)
        if name == 'str':
            v = R(0, 'object', '')
            return self.to_str(v)
        if name == 'int':
            v = R(0, 'x')
            if is_placeholder_value(v):
                raise Unsupported('derived screen resolution expression')
            try:
                return int(v)
            except (TypeError, ValueError):
                raise Unsupported('int() of a runtime value')
        if name == 'float':
            v = R(0, 'x')
            try:
                return float(v)
            except (TypeError, ValueError):
                raise Unsupported('float() of a runtime value')
        if name == 'hex':
            v = R(0, 'x')
            if isinstance(v, int):
                return hex(v)
            raise Unsupported('hex() of a runtime value')
        if name == 'len':
            v = R(0, 'obj')
            if isinstance(v, (str, list, tuple, dict)):
                return len(v)
            raise Unsupported('len() of a runtime value')
        if name == 'range':
            vals = [R(i, 'x') for i in range(len(args))]
            if not all(isinstance(v, int) for v in vals):
                raise Unsupported('range() of a runtime value')
            return list(range(*vals))
        if name in ('any', 'all'):
            g = args[0] if args else None
            if isinstance(g, ast.GeneratorExp) and name == 'any':
                return self.any_name_match(g, env, guard)
            raise Unsupported(f'{name}() of a runtime iterable')
        if name == 'open':
            p = R(0, 'file')
            mode = R(1, 'mode', 'r')
            enc = R(2, 'buffering', None) if False else kwargs.get('encoding')
            if isinstance(p, Opaque):
                raise Unsupported(p.reason)
            tok = self.token_for(p) if isinstance(p, str) else None
            if not isinstance(p, str):
                raise Unsupported('open() of a non-string path')
            if p.startswith('/') and tok is None:
                raise Unsupported(f'reads the host file {p}')
            return OpenFile(p, mode, enc, token=tok if 'r' in mode else None)
        if name in ('dict', 'list', 'tuple'):
            v = R(0, 'x', None)
            if v is None:
                return {} if name == 'dict' else ([] if name == 'list' else ())
            if isinstance(v, (dict, list, tuple)):
                return {'dict': dict, 'list': list, 'tuple': tuple}[name](v)
            raise Unsupported(f'{name}() of a runtime value')
        if name == 'bool':
            return mk_cond(self.truth(R(0, 'x')))
        if name in ('bytearray', 'memoryview'):
            raise Unsupported(f'{name}()')
        raise Unsupported(f'calls {name}()')

    def any_name_match(self, g, env, guard):
        if len(g.generators) != 1:
            raise Unsupported('complex generator')
        gen = g.generators[0]
        it = gen.iter
        if not (isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute) and it.func.attr == 'iterdir'):
            raise Unsupported('any() over a runtime iterable')
        base = self.ev(it.func.value, env, guard)
        if not isinstance(base, str):
            raise Unsupported('iterdir() of a non-path')
        names = []
        for sub in ast.walk(g.elt):
            if isinstance(sub, ast.Compare) and len(sub.ops) == 1 and isinstance(sub.ops[0], ast.Eq):
                for side in (sub.left, sub.comparators[0]):
                    if isinstance(side, ast.Constant) and isinstance(side.value, str):
                        names.append(side.value)
        if len(names) != 1:
            raise Unsupported('any() with an unrecognised predicate')
        return Cond((frozenset([lit('fileExists', norm_path(posixpath.join(base, names[0])))]),))

    def call_method(self, obj, meth, args, kwargs, node, env, guard):
        R = lambda i, n, d=Ellipsis: self.req(args, kwargs, i, n, d)
        if isinstance(obj, PathV):
            if meth in ('exists', 'is_file', 'is_dir'):
                return Cond((frozenset([lit('fileExists', norm_path(obj))]),))
            if meth == 'mkdir':
                self.file_step('mkdir', guard, dst=obj)
                return None
            if meth == 'touch':
                self.file_step('touch', guard, dst=obj)
                return None
            if meth == 'write_text':
                data = R(0, 'data')
                if not isinstance(data, str):
                    raise Unsupported('write_text of a runtime value')
                step = {'op': 'write_file', 'path': norm_path(obj), 'content': data, 'ifMissing': False,
                        'encoding': self.encoding(R(1, 'encoding', None))}
                self.emit(step, guard)
                return len(data)
            if meth == 'read_text':
                return FileContent(obj)
            if meth == 'joinpath':
                parts = [R(i, 'p') for i in range(len(args))]
                return PathV(posixpath.join(obj, *parts))
            if meth == 'stat':
                return StatObj(obj)
            if meth == 'chmod':
                mode = R(0, 'mode')
                if isinstance(mode, ReadonlyMode) and norm_path(mode.path) == norm_path(obj):
                    self.file_step('chmod_ro', guard, dst=obj)
                    return None
                raise Unsupported('chmod with a non read-only mode')
            if meth == 'unlink':
                self.file_step('delete', guard, dst=obj)
                return None
            if meth in ('resolve', 'absolute', 'expanduser'):
                return obj
            raise Unsupported(f'Path.{meth}()')
        if isinstance(obj, tuple) and len(obj) == 3 and obj[0] == 'pathmethod':
            return self.call_method(PathV(obj[1]), obj[2], args, kwargs, node, env, guard)
        if isinstance(obj, OpenFile):
            if meth == 'write':
                data = R(0, 's')
                self.file_write(obj, data, guard)
                return None
            if meth == 'read':
                if obj.token is not None:
                    return TokenContent(obj.token)
                return FileContent(obj.path)
            if meth == 'close':
                return None
            raise Unsupported(f'file.{meth}()')
        if isinstance(obj, Hasher):
            if meth == 'update':
                return None
            if meth == 'hexdigest':
                if obj.token is None:
                    raise Unsupported('hash of unknown data')
                return Digest(obj.token)
            raise Unsupported(f'hash.{meth}()')
        if isinstance(obj, (Digest, FileDigest)):
            if meth == 'hexdigest':
                return obj
            raise Unsupported(f'hash.{meth}()')
        if isinstance(obj, ZipHandle):
            if meth == 'extractall':
                dest = R(0, 'path', '${install}')
                self.emit({'op': 'download', 'url': obj.token.url, 'sha256': None, 'to': norm_path(dest),
                           'extract': True, 'backup': [], '_token': obj.token.id}, guard)
                return None
            raise Unsupported(f'zip.{meth}()')
        if isinstance(obj, RandomBytes):
            if meth == 'hex':
                return '${random}'
            raise Unsupported('random bytes')
        if isinstance(obj, EnvRead):
            raise Unsupported(f'reads the host environment variable {obj.name}')
        if isinstance(obj, dict):
            if meth == 'get':
                key = R(0, 'key')
                return obj.get(key, R(1, 'default', None))
            if meth in ('keys', 'values', 'items'):
                return list(getattr(obj, meth)())
            if meth == 'copy':
                return dict(obj)
            raise Unsupported(f'dict.{meth}()')
        if isinstance(obj, str):
            if is_placeholder_value(obj) and meth not in ('replace', 'format', 'join'):
                raise Unsupported('string method on the screen resolution')
            if meth in STR_METHODS:
                for a in list(args) + list(kwargs.values()):
                    if not isinstance(a, (str, int, type(None), list, tuple)):
                        raise Unsupported(f'str.{meth}() with a runtime argument')
                try:
                    return getattr(obj, meth)(*args, **kwargs)
                except (TypeError, ValueError) as e:
                    raise Unsupported(f'str.{meth}() failed: {e}')
            raise Unsupported(f'str.{meth}()')
        if isinstance(obj, JsonDoc):
            raise Unsupported(f'json.{meth}()')
        if isinstance(obj, tuple) and obj and obj[0] == 'respmethod':
            raise Unsupported('reads a download response')
        raise Unsupported(f'{type(obj).__name__}.{meth}()')

    def file_write(self, f, data, guard):
        if f.token is not None:
            return
        if not isinstance(data, str):
            if isinstance(data, Opaque):
                raise Unsupported(data.reason)
            raise Unsupported('writes runtime data to a file')
        mode = f.mode
        if 'b' in mode:
            raise Unsupported('binary write')
        if not any(c in mode for c in 'wax+'):
            raise Unsupported('write to a file opened for reading')
        if f.step is not None and f.step_guard == guard:
            f.step['content'] += data
            return
        if f.step is not None and 'w' in mode:
            raise Unsupported('conditional writes to the same file')
        path = norm_path(f.path)
        if 'a' in mode:
            step = {'op': '_append', 'path': path, 'content': data}
        else:
            step = {'op': 'write_file', 'path': path, 'content': data, 'ifMissing': False,
                    'encoding': self.encoding(f.encoding)}
        self.emit(step, guard)
        f.step = step
        f.step_guard = guard

    def util_call(self, name, args, kwargs, node, env, guard):
        R = lambda i, n, d=Ellipsis: self.req(args, kwargs, i, n, d)
        if name == 'ReplaceType':
            return Namespace(from_value=R(0, 'from_value'), to_value=R(1, 'to_value'))
        if name == 'protontricks':
            verb = R(0, 'verb')
            if not isinstance(verb, str):
                raise Unsupported('protontricks with a runtime verb')
            for v in verb.split():
                self.emit({'op': 'verb', 'name': v}, guard)
            return True
        if name == 'regedit_add':
            folder = R(0, 'folder')
            vname = R(1, 'name', None)
            typ = R(2, 'typ', None)
            value = R(3, 'value', None)
            for x in (folder, vname, typ, value):
                if x is not None and not isinstance(x, str):
                    raise Unsupported('regedit_add with a runtime argument')
            hive, key = reg_hive(folder)
            values = {}
            if vname is not None and typ is not None and value is not None:
                if typ == 'REG_DWORD':
                    try:
                        value = str(int(value, 0))
                    except ValueError:
                        raise Unsupported(f'REG_DWORD value {value!r} is not a number')
                elif typ not in ('REG_SZ', 'REG_EXPAND_SZ', 'REG_BINARY'):
                    raise Unsupported(f'registry type {typ}')
                values[vname] = {'type': typ, 'value': value}
            self.emit({'op': 'registry', 'hive': hive, 'key': key, 'values': values}, guard)
            return None
        if name == 'replace_command':
            orig = R(0, 'orig')
            repl = R(1, 'repl')
            if not isinstance(orig, str) or not isinstance(repl, str):
                raise Unsupported('replace_command with runtime arguments')
            step = replace_command_step(orig, repl)
            flags = R(2, 'match_flags', None)
            if flags is not None and not (isinstance(flags, int) and flags & re.IGNORECASE):
                step['note'] = 'case-sensitive match upstream'
            self.emit(step, guard)
            return True
        if name == 'append_argument':
            a = R(0, 'argument')
            if not isinstance(a, str):
                raise Unsupported('append_argument with a runtime value')
            self.emit({'op': 'launch_args', 'append': a}, guard)
            return None
        if name == 'set_environment':
            k, v = R(0, 'envvar'), R(1, 'value')
            if not isinstance(k, str) or not isinstance(v, str):
                raise Unsupported('set_environment with runtime arguments')
            self.emit({'op': 'env', 'set': {k: v}}, guard)
            return None
        if name == 'del_environment':
            self.emit({'op': 'env', 'unset': [R(0, 'envvar')]}, guard)
            return None
        if name == 'get_game_install_path':
            return '${install}'
        if name == 'protonprefix':
            return PathV('${prefix}')
        if name in ('winedll_override', 'wineexe_override'):
            dll = R(0, 'dll' if name == 'winedll_override' else 'exe')
            order = R(1, 'dtype')
            if not isinstance(dll, str) or not isinstance(order, str):
                raise Unsupported(f'{name} with runtime arguments')
            if order == '':
                order = 'd'
            if name == 'wineexe_override':
                dll = dll + '.exe'
            self.emit({'op': 'dll_override', 'overrides': {dll: order}}, guard)
            return None
        if name == 'disable_nvapi':
            self.emit({'op': 'dll_override', 'overrides': {d: 'd' for d in (
                'nvapi', 'nvapi64', 'nvcuda', 'nvcuvid', 'nvencodeapi', 'nvencodeapi64')}}, guard)
            return None
        if name in ('disable_esync', 'disable_fsync', 'disable_ntsync'):
            self.emit({'op': 'sync', name.split('_')[1]: False}, guard)
            return None
        if name == 'create_dosbox_conf':
            conf_file = R(0, 'conf_file')
            conf_dict = R(1, 'conf_dict')
            if not isinstance(conf_dict, dict):
                raise Unsupported('create_dosbox_conf with a runtime config')
            self.emit({'op': 'write_file', 'path': norm_path(conf_file), 'content': dosbox_conf_text(conf_dict),
                       'ifMissing': True, 'encoding': 'utf-8'}, guard)
            return None
        if name == 'set_ini_options':
            ini_opts = R(0, 'ini_opts')
            cfile = R(1, 'cfile')
            enc = R(2, 'encoding', 'utf-8')
            base = R(3, 'base_path', 'game')
            if not isinstance(ini_opts, str) or not isinstance(cfile, str):
                raise Unsupported('set_ini_options with runtime arguments')
            if base == 'user':
                root = '${drive_c}/users/${user}/Documents'
            elif base == 'appdata_local':
                root = '${drive_c}/users/${user}/AppData/Local'
            elif base == 'game':
                root = '${install}'
            else:
                root = ''
            path = norm_path(posixpath.join(root, cfile) if root else cfile)
            try:
                sections = ini_sections(ini_opts)
            except configparser.Error as e:
                raise Unsupported(f'cannot parse INI options: {e}')
            for sec, vals in sections:
                if is_placeholder_value(json.dumps(vals)):
                    for v in vals.values():
                        if is_placeholder_value(v) and v not in ('${screen_w}', '${screen_h}'):
                            raise Unsupported('derived screen resolution expression')
                step = {'op': 'ini', 'path': path, 'section': sec, 'values': vals, 'createIfMissing': False,
                        'backup': True}
                enc_n = self.encoding(enc)
                if enc_n != 'utf-8':
                    step['encoding'] = enc_n
                self.emit(step, guard)
            return True
        if name == 'set_xml_options':
            return self.xml_options(args, kwargs, guard)
        if name == 'get_resolution':
            return ('${screen_w}', '${screen_h}')
        if name == 'set_dxvk_option':
            opt, val = R(0, 'opt'), R(1, 'val')
            if not isinstance(opt, str):
                raise Unsupported('set_dxvk_option with runtime arguments')
            self.emit({'op': 'dxvk_config', 'set': {opt: self.to_str(val)}}, guard)
            return None
        if name in RUNTIME_APPS:
            self.emit({'op': 'runtime_app', 'appid': RUNTIME_APPS[name]}, guard)
            return None
        if name == 'set_cpu_topology_limit':
            n = R(0, 'core_limit')
            if not isinstance(n, int):
                raise Unsupported('cpu topology with a runtime value')
            self.emit({'op': 'cpu_topology', 'limit': n, 'nosmt': False}, guard)
            return True
        if name == 'set_cpu_topology':
            n = R(0, 'core_count')
            if not isinstance(n, int):
                raise Unsupported('cpu topology with a runtime value')
            self.emit({'op': 'cpu_topology', 'limit': n, 'nosmt': False,
                       'note': 'upstream sets a fixed core count'}, guard)
            return True
        if name == 'set_cpu_topology_nosmt':
            self.emit({'op': 'cpu_topology', 'nosmt': True}, guard)
            return True
        if name == 'set_game_drive':
            enabled = R(0, 'enabled')
            self.emit({'op': 'env', 'set': {'PROTON_SET_GAME_DRIVE': '1' if enabled else '0'}}, guard)
            return None
        if name == 'import_saves_folder':
            appid = R(0, 'from_appid')
            rel = R(1, 'relative_path')
            is_demo = R(2, 'is_demo', False)
            if not isinstance(appid, int) or not isinstance(rel, str) or not isinstance(is_demo, bool):
                raise Unsupported('import_saves_folder with runtime arguments')
            rel = posixpath.normpath(rel.replace('\\', '/'))
            self.emit({'op': 'import_saves', 'fromAppid': appid, 'path': map_legacy_user_dir(rel),
                       'copy': is_demo}, guard)
            return True
        if name == 'get_steam_account_id':
            return '${steam_id64}'
        if name == 'create_dos_device':
            letter = R(0, 'letter', 'r')
            dev = R(1, 'dev_type', 'cdrom')
            path = R(2, 'path', '/tmp')
            if not isinstance(letter, str) or not isinstance(path, str):
                raise Unsupported('create_dos_device with runtime arguments')
            p = norm_path(path) if path.startswith('${') else path
            self.emit({'op': 'dos_device', 'letter': letter, 'path': p, 'type': dev}, guard)
            return True
        if name == 'use_seccomp':
            return None
        if name == 'try_show_gui_error':
            return None
        if name == 'patch_libcuda':
            return False
        if name == 'checkinstalled':
            verb = R(0, 'verb')
            if not isinstance(verb, str):
                raise Unsupported('checkinstalled with a runtime verb')
            if verb == 'gui':
                return False
            return Cond((frozenset([lit('verbInstalled', verb)]),))
        if name in ('protondir', 'which', 'is_custom_verb', 'check_internet', 'get_cpu_count', 'is_smt_enabled'):
            raise Unsupported(f'util.{name}()')
        if name in ('install_from_zip', 'install_all_from_tgz'):
            raise EffectUnsupported(f'util.{name}()')
        raise EffectUnsupported(f'unknown helper util.{name}()')

    def xml_options(self, args, kwargs, guard):
        R = lambda i, n, d=Ellipsis: self.req(args, kwargs, i, n, d)
        base_attr, xml_line, cfile = R(0, 'base_attibutte'), R(1, 'xml_line'), R(2, 'cfile')
        enc = R(3, 'encoding', 'utf-8')
        base = R(4, 'base_path', 'game')
        if not all(isinstance(x, str) for x in (base_attr, xml_line, cfile)):
            raise Unsupported('set_xml_options with runtime arguments')
        m = re.fullmatch(r'\s*<([A-Za-z_][\w.-]*)>\s*', base_attr)
        if not m:
            raise Unsupported(f'XML anchor {base_attr!r} is not an opening tag')
        roots = {'user': '${drive_c}/users/${user}/Documents', 'appdata_local': '${drive_c}/users/${user}/AppData/Local',
                 'game': '${install}'}
        root = roots.get(base, '')
        path = norm_path(posixpath.join(root, cfile) if root else cfile)
        value = '\n'.join(ln.strip() for ln in xml_line.strip().splitlines() if ln.strip())
        step = {'op': 'xml', 'path': path, 'xpath': f'//{m.group(1)}', 'value': value, 'backup': True}
        enc_n = self.encoding(enc)
        if enc_n != 'utf-8':
            step['encoding'] = enc_n
        self.emit(step, guard)
        return True


PURE_UTIL = {
    'get_game_install_path', 'protonprefix', 'protondir', 'get_resolution', 'checkinstalled', 'get_steam_account_id',
    'which', 'is_custom_verb', 'check_internet', 'get_cpu_count', 'is_smt_enabled', 'ReplaceType',
}


def is_effect_call(q):
    if q.startswith(('subprocess.', 'shutil.', 'urllib.request.urlretrieve', 'threading.')):
        return True
    if q in ('os.system', 'os.remove', 'os.unlink', 'os.rmdir', 'os.rename', 'os.replace', 'os.symlink',
             'os.makedirs', 'os.mkdir', 'os.chdir', 'os.kill'):
        return True
    return q.startswith('util.') and q.count('.') == 1 and q[5:] not in PURE_UTIL


def replace_command_step(orig, repl):
    plain = re.sub(r'\\([.\-])', r'\1', orig)
    literal = re.fullmatch(r'[A-Za-z0-9_ .-]+(/[A-Za-z0-9_ .-]+)*', plain) is not None
    if repl == '':
        if literal:
            return {'op': 'replace_command', 'from': plain, 'to': ''}
        return {'op': 'replace_command', 'from': orig, 'to': '', 'regex': True}
    if literal and re.search(r'\.[A-Za-z0-9]+$', plain):
        parts = plain.split('/')
        to = repl
        if len(parts) > 1:
            to = posixpath.normpath('../' * (len(parts) - 1) + repl)
        return {'op': 'replace_command', 'from': parts[-1], 'to': to}
    return {'op': 'replace_command', 'from': orig, 'to': repl, 'regex': True}


def _strip_internal(step):
    return {k: v for k, v in step.items() if not k.startswith('_')}


def finalize_steps(raw):
    raw = [(copy.deepcopy(s), g, p) for s, g, p in raw]
    for i, (s, g, p) in enumerate(raw):
        if s['op'] != 'download':
            continue
        dest = s['to']
        for j in range(i - 1, -1, -1):
            s2, g2, p2 = raw[j]
            if s2['op'] == 'file' and s2['action'] == 'move' and g2 == g and p2 == p:
                src, to = s2.get('from', ''), s2.get('to', '')
                if src.startswith(dest + '/') and ('${random}' in to or to.endswith('.bak')):
                    s['backup'].insert(0, src[len(dest) + 1:])
                    s2['op'] = '_drop'
    regimports = [(s, g, p) for s, g, p in raw if s['op'] == '_regimport']
    for imp, g, p in regimports:
        path = imp['path']
        replaced = False
        for idx, (s, g2, p2) in enumerate(raw):
            if s['op'] == 'write_file' and s['path'].lower() == path.lower():
                try:
                    entries = parse_reg(s['content'])
                except Unsupported as e:
                    s.clear()
                    s.update({'op': 'unsupported', 'reason': f'registry import: {e.reason}', 'source': ''})
                    replaced = True
                    continue
                new = [({'op': 'registry', **e}, g2, p2) for e in entries]
                raw[idx] = (dict(s, op='_regsplit', _entries=new), g2, p2)
                replaced = True
        for s, g2, p2 in raw:
            if s['op'] == 'file' and s['action'] == 'delete' and s.get('to', '').lower() == path.lower():
                s['op'] = '_drop'
        imp['op'] = '_drop' if replaced else 'unsupported'
        if not replaced:
            imp.update({'reason': 'imports a registry file that the fix did not write', 'source': ''})
    expanded = []
    for s, g, p in raw:
        if s['op'] == '_regsplit':
            expanded.extend(s['_entries'])
        elif s['op'] != '_drop':
            expanded.append((s, g, p))
    pre_order = []
    pre = {}
    for s, g, p in expanded:
        key = json.dumps([s, p], sort_keys=True, default=str)
        if key not in pre:
            pre[key] = [s, FALSE, p]
            pre_order.append(key)
        pre[key][1] = d_or(pre[key][1], g)
    raw = [tuple(pre[k]) for k in pre_order]
    download_tokens = {s.get('_token') for s, g, p in raw if s['op'] == 'download'}

    items = []
    for s, g, p in raw:
        for conj in g:
            st = copy.deepcopy(s)
            c = set(conj)
            bad = []
            for literal in sorted(conj, key=_lit_key):
                pos, kind = literal[0], literal[1]
                if st['op'] == 'unsupported' and kind in ('sha256ok', 'fileHash', 'contentDiffers', 'fileContains',
                                                          'jsonHas', 'opaque'):
                    c.discard(literal)
                    continue
                if kind == 'sha256ok':
                    if literal[2] in download_tokens and pos:
                        if st['op'] == 'download' and st.get('_token') == literal[2]:
                            st['sha256'] = literal[3]
                        c.discard(literal)
                    elif not pos and st['op'] == 'unsupported':
                        c.discard(literal)
                    else:
                        bad.append(('checksum condition', ''))
                elif kind == 'fileHash':
                    if st['op'] == 'download':
                        c.discard(literal)
                        st['note'] = 'upstream skips this when the target files already carry the patched checksum'
                    else:
                        bad.append(('file checksum condition', ''))
                elif kind == 'contentDiffers':
                    if pos and st['op'] == 'write_file' and st['path'] == literal[2] and st['content'] == literal[3]:
                        c.discard(literal)
                    else:
                        bad.append(('file content condition', ''))
                elif kind == 'fileContains':
                    if not pos and st['op'] == '_append' and st['path'] == literal[2]:
                        c.discard(literal)
                    else:
                        bad.append(('file content condition', ''))
                elif kind == 'jsonHas':
                    if pos and st['op'] == 'json_set' and st['path'] == literal[2] and st['key'] == literal[3]:
                        c.discard(literal)
                        st['note'] = 'upstream only sets the key when it already exists'
                    else:
                        bad.append(('JSON key condition', ''))
                elif kind == 'opaque':
                    if st['op'] == 'unsupported':
                        c.discard(literal)
                    else:
                        bad.append((literal[2], literal[3]))
                elif kind == 'fileExists' and not pos:
                    target = st.get('path') if st['op'] in ('write_file',) else st.get('to')
                    if st['op'] == 'write_file' and target == literal[2]:
                        st['ifMissing'] = True
                        c.discard(literal)
                    elif st['op'] == 'file' and st['action'] in ('mkdir', 'touch') and target == literal[2]:
                        c.discard(literal)
            if bad:
                reason, source = bad[0]
                st = {'op': 'unsupported', 'reason': f'depends on an unsupported condition: {reason}',
                      'source': source, 'step': _strip_internal(st)}
                c = {x for x in c if x[1] not in ('opaque', 'sha256ok', 'fileHash', 'contentDiffers', 'fileContains', 'jsonHas')}
            items.append((st, frozenset(c), p))

    out_items = []
    for st, c, p in items:
        if st['op'] == '_append':
            lines = [ln for ln in st['content'].split('\n') if ln.strip()]
            for ln in lines:
                out_items.append(({'op': 'append_line', 'path': st['path'], 'line': ln, 'ifAbsent': True}, c, p))
            continue
        if st['op'] == 'download':
            st.pop('_token', None)
        out_items.append((st, c, p))

    order = []
    groups = {}
    for st, c, p in out_items:
        key = json.dumps([st, p], sort_keys=True)
        if key not in groups:
            groups[key] = (st, p, [])
            order.append(key)
        groups[key][2].append(c)
    merged = []
    for key in order:
        st, p, conjs = groups[key]
        dnf = simplify(tuple(conjs))
        for conj in dnf:
            merged.append((st, conj, p))

    final = []
    for st, c, p in merged:
        if final:
            pst, pc, pp = final[-1]
            if pc == c and pp == p and pst['op'] == st['op']:
                if st['op'] == 'dll_override' and not (set(pst['overrides']) & set(st['overrides'])):
                    pst = dict(pst, overrides={**pst['overrides'], **st['overrides']})
                    final[-1] = (pst, pc, pp)
                    continue
                if st['op'] == 'env' and 'set' in st and 'set' in pst and 'unset' not in st and 'unset' not in pst \
                        and not (set(pst['set']) & set(st['set'])):
                    pst = dict(pst, set={**pst['set'], **st['set']})
                    final[-1] = (pst, pc, pp)
                    continue
                if st['op'] == 'registry' and pst['hive'] == st['hive'] and pst['key'] == st['key'] \
                        and not (set(pst['values']) & set(st['values'])):
                    pst = dict(pst, values={**pst['values'], **st['values']})
                    final[-1] = (pst, pc, pp)
                    continue
                if st['op'] == 'launch_args':
                    pst = dict(pst, append=pst['append'] + ' ' + st['append'])
                    final[-1] = (pst, pc, pp)
                    continue
        final.append((st, c, p))

    last_set = {}
    for idx, (st, c, p) in enumerate(final):
        if st['op'] == 'env' and 'set' in st and len(st['set']) == 1 and 'unset' not in st:
            k = next(iter(st['set']))
            last_set[(k, c, p)] = idx
    drop = set()
    for idx, (st, c, p) in enumerate(final):
        if st['op'] == 'env' and 'set' in st and len(st['set']) == 1 and 'unset' not in st:
            k = next(iter(st['set']))
            if last_set[(k, c, p)] != idx:
                drop.add(idx)

    written_paths = {(st['path'], c, p) for st, c, p in final if st['op'] == 'write_file'}
    for idx, (st, c, p) in enumerate(final):
        if st['op'] == 'file' and st['action'] == 'touch' and (st['to'], c, p) in written_paths:
            drop.add(idx)
    plain_writes = {(st['path'], st['content'], st.get('encoding'), p) for st, c, p in final
                    if st['op'] == 'write_file' and not st['ifMissing'] and not c}
    for idx, (st, c, p) in enumerate(final):
        if st['op'] == 'write_file' and st['ifMissing'] and (st['path'], st['content'], st.get('encoding'), p) in plain_writes:
            drop.add(idx)
    steps = []
    for idx, (st, c, p) in enumerate(final):
        if idx in drop:
            continue
        out = dict(st)
        if c:
            out['when'] = [literal_to_json(x) for x in sorted(c, key=_lit_key)]
        if p:
            out['phase'] = p
        steps.append(out)
    return steps


def convert_fix(source, game_id, store, umu_id=None):
    interp = FixInterp(source, game_id, store, umu_id)
    raw = interp.run()
    steps = finalize_steps(raw)
    last_bad = max((i for i, st in enumerate(steps) if st['op'] == 'unsupported'), default=-1)
    for i in range(last_bad):
        st = steps[i]
        if st['op'] == 'file' and st['action'] in ('delete', 'move'):
            keep = {k: st[k] for k in ('when', 'phase') if k in st}
            inner = {k: v for k, v in st.items() if k not in ('when', 'phase')}
            steps[i] = dict(keep, op='unsupported',
                            reason='destructive file step skipped because a later step of this fix is unsupported',
                            source='', step=inner)
    return steps, coverage_of(steps, interp.notes), interp.notes


KNOWN_OPS = {
    'env', 'dll_override', 'launch_args', 'replace_command', 'registry', 'verb', 'write_file', 'append_line', 'ini',
    'json_set', 'file', 'download', 'dxvk_config', 'sync', 'cpu_topology', 'cwd', 'import_saves', 'runtime_app',
    'regsvr32', 'exec', 'dos_device', 'text_replace', 'xml', 'unsupported',
}


def load_overrides(path):
    out = {}
    if not path or not os.path.isdir(path):
        return out
    for store in sorted(os.listdir(path)):
        d = os.path.join(path, store)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith('.json'):
                continue
            with open(os.path.join(d, f), encoding='utf-8') as fh:
                data = json.load(fh)
            key = f'{store}/{f[:-5]}'
            steps = data.get('steps')
            if not isinstance(steps, list):
                raise ValueError(f'override {key}: steps must be a list')
            for st in steps:
                if not isinstance(st, dict) or st.get('op') not in KNOWN_OPS:
                    raise ValueError(f'override {key}: bad step {st!r}')
            if data.get('coverage') not in (None, 'full', 'partial', 'none'):
                raise ValueError(f'override {key}: bad coverage')
            out[key] = data
    return out


def apply_override(override, steps, coverage, notes):
    new_steps = copy.deepcopy(override['steps'])
    new_notes = list(notes) + list(override.get('notes', []))
    new_cov = override.get('coverage') or coverage_of(new_steps)
    return new_steps, new_cov, new_notes


def coverage_of(steps, notes=()):
    n_uns = sum(1 for s in steps if s['op'] == 'unsupported')
    if not steps:
        return 'full' if not notes else 'none'
    if n_uns == 0:
        return 'full'
    if n_uns == len(steps):
        return 'none'
    return 'partial'


def _step_writes(st):
    op = st.get('op')
    if op == 'file':
        out = [st.get('to')]
        if st.get('action') == 'move':
            out.append(st.get('from'))
        return [('path', x) for x in out if x]
    if op in ('write_file', 'append_line', 'ini', 'json_set', 'text_replace', 'xml'):
        return [('path', st['path'])]
    if op == 'download':
        return [('tree', st['to'])]
    if op == 'verb':
        return [('verb', st['name'])]
    return []


def _guard_subjects(st):
    out = []
    for c in st.get('when', []):
        inner = c.get('not', c) if isinstance(c, dict) else {}
        for k in ('fileExists', 'fileMissing'):
            if k in inner:
                out.append((json.dumps(c, sort_keys=True), 'path', inner[k]))
        if 'verbInstalled' in inner:
            out.append((json.dumps(c, sort_keys=True), 'verb', inner['verbInstalled']))
    return out


def _touches(kind, subject, writes):
    for wkind, w in writes:
        if kind == 'verb' and wkind == 'verb' and w == subject:
            return True
        if kind == 'path' and wkind == 'path' and (w == subject or w.startswith(subject + '/') or subject.startswith(w + '/')):
            return True
        if kind == 'path' and wkind == 'tree' and subject.startswith(w + '/'):
            return True
    return False


def sequencing_hazards(steps):
    """Steps whose `when` re-tests something an earlier step under the same test has changed.

    Upstream evaluates such a test once, before the earlier step runs. An interpreter that
    evaluates `when` per step at run time sees the changed state and skips the later step.
    """
    found = []
    for i, st in enumerate(steps):
        if st['op'] == 'unsupported':
            continue
        for key, kind, subject in _guard_subjects(st):
            for j in range(i):
                prev = steps[j]
                if prev['op'] == 'unsupported':
                    continue
                same_test = any(k2 == key or k2 == json.dumps({'not': json.loads(key)}, sort_keys=True)
                                for k2, _, _ in _guard_subjects(prev))
                if same_test and _touches(kind, subject, _step_writes(prev)):
                    found.append((j, i, json.loads(key)))
    return found


def module_docstring_name(source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    doc = ast.get_docstring(tree)
    if not doc:
        return None
    line = doc.strip().split('\n')[0].strip()
    for prefix in ('Game fix for ', 'Game fix ', 'Gamefix for ', 'Fix for ', 'Application fix ', 'Mod support for '):
        if line.lower().startswith(prefix.lower()):
            line = line[len(prefix):]
            break
    line = line.strip().rstrip('.').strip()
    line = line.replace('\\&', '&')
    return line or None


class Src:
    def __init__(self, url, sha, chain=()):
        self.url = url
        self.sha = sha
        self.chain = tuple(chain)

    def child(self, entry, fmt):
        return Src(self.url, self.sha, self.chain + ((entry, fmt),))

    def renamed(self, entry):
        if not self.chain:
            return self
        return Src(self.url, self.sha, self.chain[:-1] + ((entry, self.chain[-1][1]),))


WT_DIRS = {
    '/SYS32': 'x86', '/SYS64': 'x64', '/FONTS': 'font',
}
WT_IGNORED_CMDS = {
    'w_warn', 'w_info', 'echo', 'w_die', 'w_try_cd', 'cd', 'rm', 'touch', 'unset', 'w_wineserver',
    'w_package_broken', 'w_package_broken_win64', 'w_package_broken_wow64', 'w_package_warn_win64',
    'w_package_unsupported_win64', 'w_package_unsupported_win32', 'w_package_broken_win32',
    'w_skip_windows', 'w_store_winver', 'w_restore_winver', 'sleep', 'kill', 'w_backup_reg_file',
    'w_try_mkdir', 'mkdir', 'w_get_sha256sum', 'w_ahk_do', 'w_linkcheck_ignore', 'w_override_no_dlls',
    'return', 'true', ':', 'w_killall', 'w_try_regedit32', 'printf', 'w_read_key', 'w_verify_cabextract_available',
    'winetricks_set_winver', 'w_workaround_wine_bug', 'w_warn_cancel', 'w_question',
}
WT_INSTALLER_CMDS = {
    'w_try_ms_installer', 'w_try_msiexec64', 'w_try_msiexec32', 'w_try_msiexec', 'w_ahk_do',
}


class WTInterp:
    def __init__(self, wt, verb):
        self.wt = wt
        self.verb = verb
        self.vars = {
            'W_TMP': '/WTMP', 'W_TMP_EARLY': '/WTMP', 'W_TMP_WIN': 'WTMPWIN', 'W_TMP_EARLY_WIN': 'WTMPWIN',
            'W_CACHE': '/WCACHE', 'W_SYSTEM32_DLLS': '/SYS32', 'W_SYSTEM64_DLLS': '/SYS64',
            'W_FONTSDIR_UNIX': '/FONTS', 'W_WINDIR_UNIX': '/WINDIR', 'W_PROGRAMS_X86_UNIX': '/PROGX86',
            'W_PROGRAMS_UNIX': '/PROG', 'W_PROGRAMW6432_UNIX': '/PROG64', 'W_DRIVE_C': '/DRIVEC',
            'W_ARCH': 'win64', 'WINE': 'WINE', 'WINE64': 'WINE', 'WINE_ARCH': 'WINE', 'WINE_MULTI': 'WINE',
            'W_OPT_UNATTENDED': '1', 'WINEPREFIX': '/PFX', 'W_SYSTEM32_DLLS_WIN': 'C:\\windows\\syswow64',
            'W_SYSTEM64_DLLS_WIN': 'C:\\windows\\system32', 'W_FONTSDIR_WIN': 'C:\\windows\\Fonts',
            'W_WINDIR_WIN': 'C:\\windows', 'LANG': 'C', 'W_PACKAGE': verb,
        }
        self.vfs = OrderedDict()
        self.vdirs = OrderedDict()
        self.texts = {}
        self.cwd = '/WTMP'
        self.files = []
        self.overrides = OrderedDict()
        self.registry = []
        self.font_registry = OrderedDict()
        self.winver = None
        self.regsvr = []
        self.installers = []
        self.unhandled = []
        self.calls = []
        self.downloads = []
        self.problems = []
        self.depth = 0
        self.loop_marks = {}

    def expand(self, s, pos):
        def param(m):
            body = m.group(1) or m.group(2)
            if body in ('@', '*'):
                return ' '.join(pos)
            if body.isdigit():
                n = int(body)
                return pos[n - 1] if 0 < n <= len(pos) else ''
            mm = re.match(r'^(\w+)(:\+|:-|%%|%|##|#)(.*)$', body)
            if mm:
                name, op, word = mm.groups()
                val = self.lookup(name, pos)
                word = self.expand(word, pos)
                if op == ':+':
                    return word if val else ''
                if op == ':-':
                    return val if val else word
                if op in ('%', '%%'):
                    pat = word
                    for k in (range(len(val), -1, -1) if op == '%' else range(0, len(val) + 1)):
                        if fnmatch.fnmatchcase(val[k:], pat):
                            return val[:k]
                    return val
                if op in ('#', '##'):
                    for k in (range(0, len(val) + 1) if op == '#' else range(len(val), -1, -1)):
                        if fnmatch.fnmatchcase(val[:k], word):
                            return val[k:]
                    return val
            return self.lookup(body, pos)

        def cmdsub(m):
            inner = m.group(1).strip()
            parts = inner.split(None, 1)
            if parts and parts[0] == 'basename' and len(parts) == 2:
                bargs = shlex.split(parts[1])
                arg = posixpath.basename(self.expand(bargs[0], pos)) if bargs else ''
                if len(bargs) > 1 and arg.endswith(bargs[1]):
                    arg = arg[:-len(bargs[1])]
                return arg
            return ''

        s = re.sub(r'\$\(([^()]*)\)', cmdsub, s)
        return re.sub(r'\$\{([^}]*)\}|\$([A-Za-z_][A-Za-z0-9_]*|[0-9@*])', param, s)

    def lookup(self, name, pos):
        if name.isdigit():
            n = int(name)
            return pos[n - 1] if 0 < n <= len(pos) else ''
        return self.vars.get(name, '')

    def norm(self, p):
        if not p:
            return p
        if p.startswith('WTMPWIN'):
            p = '/WTMP/' + p[len('WTMPWIN'):].lstrip('\\/').replace('\\', '/')
        if not p.startswith('/'):
            p = self.cwd.rstrip('/') + '/' + p
        p = posixpath.normpath(p)
        return p

    def resolve(self, path):
        p = self.norm(path)
        if p in self.vfs:
            return self.vfs[p]
        for k in reversed(list(self.vfs)):
            if posixpath.dirname(k) == posixpath.dirname(p) and (
                fnmatch.fnmatch(p.lower(), k.lower()) or fnmatch.fnmatch(k.lower(), p.lower())
            ):
                src = self.vfs[k]
                if any(ch in posixpath.basename(p) for ch in '*?['):
                    return src
                return src.renamed(posixpath.basename(p)) if any(ch in src.chain[-1][0] for ch in '*?[') else src
        for d in reversed(list(self.vdirs)):
            if p.startswith(d + '/'):
                return self.vdirs[d].renamed(p[len(d) + 1:])
        return None

    def target(self, path):
        p = self.norm(path)
        for prefix, arch in WT_DIRS.items():
            if p == prefix:
                return arch, ''
            if p.startswith(prefix + '/'):
                return arch, p[len(prefix) + 1:]
        return None, None

    def add_file(self, src, arch, name):
        if not src.chain:
            entry, nested, fmts = None, [], []
        else:
            entry = src.chain[-1][0]
            nested = [e for e, _ in src.chain[:-1]]
            fmts = [f for _, f in src.chain]
        inst = {'arch': 'any' if arch == 'font' else arch, 'name': name}
        for f in self.files:
            if f['url'] == src.url and f.get('entry') == entry and f.get('nested', []) == nested:
                if inst not in f['install']:
                    f['install'].append(inst)
                return
        rec = {'url': src.url, 'sha256': src.sha, 'entry': entry, 'install': [inst]}
        if nested:
            rec['nested'] = nested
        if fmts:
            rec['formats'] = fmts
        rec['_font'] = arch == 'font'
        self.files.append(rec)

    def extract(self, archive, dest, filters, fmt, lower):
        src = self.resolve(archive)
        if src is None:
            self.unhandled.append(f'extract from unknown archive {archive}')
            return
        dest = self.norm(dest) if dest else self.cwd
        arch, sub = self.target(dest)
        for flt in (filters or [None]):
            entry = flt if flt else '*'
            if arch:
                has_glob = any(ch in entry for ch in '*?[')
                name = '*' if has_glob else (entry.lower() if lower else entry)
                name = posixpath.join(sub, name) if sub else name
                self.add_file(src.child(entry, fmt), arch, name)
            else:
                child = src.child(entry, fmt)
                if flt:
                    key = posixpath.join(dest, flt.lower() if lower else flt)
                    self.vfs[key] = child
                else:
                    self.vdirs[dest] = child

    def copy(self, src_path, dst_path, move=False):
        src = self.resolve(src_path)
        if src is None:
            self.unhandled.append(f'copy of unknown file {src_path}')
            return
        arch, sub = self.target(dst_path)
        sbase = posixpath.basename(self.norm(src_path))
        if arch:
            if sub == '' or dst_path.endswith('/'):
                name = posixpath.join(sub, sbase) if sub else sbase
            else:
                name = sub
            if any(ch in sbase for ch in '*?['):
                name = posixpath.join(posixpath.dirname(name), '*') if posixpath.dirname(name) else '*'
            self.add_file(src, arch, name)
            return
        d = self.norm(dst_path)
        if d.startswith('/WTMP') or d.startswith('/WCACHE'):
            if d in self.vdirs or dst_path.endswith('/') or re.match(r'^/WCACHE/[^/]+$', d) or d == '/WTMP':
                d = posixpath.join(d, sbase)
            self.vfs[d] = src
            if move:
                self.vfs.pop(self.norm(src_path), None)
            return
        self.unhandled.append(f'copy to {dst_path}')

    def run_func(self, name, pos):
        body = self.wt.funcs.get(name)
        if body is None:
            self.unhandled.append(f'missing function {name}')
            return
        self.depth += 1
        if self.depth > 20:
            self.depth -= 1
            return
        try:
            nodes = self.wt.parsed(name)
            self.exec_nodes(nodes, pos)
        finally:
            self.depth -= 1

    def exec_nodes(self, nodes, pos):
        for n in nodes:
            kind = n[0]
            if kind == 'cmd':
                self.exec_cmd(n[1], n[2], pos)
            elif kind == 'if':
                for cond, body in n[1]:
                    if self.eval_cond(cond, pos):
                        self.exec_nodes(body, pos)
                        break
                else:
                    self.exec_nodes(n[2], pos)
            elif kind == 'case':
                word = self.expand(n[1].strip().strip('"'), pos)
                for pats, body in n[2]:
                    if any(fnmatch.fnmatchcase(word, self.expand(p.strip().strip('"'), pos)) for p in pats):
                        self.exec_nodes(body, pos)
                        break
            elif kind == 'for':
                for item in self.for_items(n[2], pos):
                    self.vars[n[1]] = item
                    self.exec_nodes(n[3], pos)
            elif kind == 'while':
                self.unhandled.append('while loop')

    def for_items(self, words, pos):
        try:
            toks = shlex.split(words, posix=True)
        except ValueError:
            return []
        out = []
        for t in toks:
            if 'WINETRICKS_METADATA' in t and t.endswith('.vars'):
                cat = t.split('/')[-2]
                out.extend(f'/META/{cat}/{n}.vars' for n, m in sorted(self.wt.meta.items())
                           if m.get('category') == cat and self.wt.source.get(n) == 'winetricks')
                continue
            v = self.expand(t, pos)
            if any(ch in v for ch in '*?['):
                p = self.norm(v)
                d = posixpath.dirname(p)
                keys = list(self.vfs)
                mark = self.loop_marks.get(d, 0)
                cands = [k for k in keys[mark:] if posixpath.dirname(k) == d]
                if not cands:
                    cands = [k for k in keys if posixpath.dirname(k) == d]
                self.loop_marks[d] = len(keys)
                if cands:
                    out.extend(cands)
                else:
                    out.append(p)
            else:
                out.extend(v.split())
        return out

    def eval_cond(self, cond, pos):
        c = cond.strip()
        neg_ = False
        if c.startswith('! '):
            neg_ = True
            c = c[2:].strip()
        res = False
        if 'W_ARCH' in c:
            exp = self.expand(c, pos)
            if '!=' in exp:
                res = 'win64' not in exp.split('!=')[1].split()[0]
            else:
                res = 'win64' in exp
        elif c.startswith('w_workaround_wine_bug'):
            res = True
        elif re.match(r'^(test|\[)\s+!\s+-[fesd]\s', c):
            res = True
        elif re.match(r'^(test|\[)\s+-[fesd]\s', c):
            res = False
        else:
            self.unhandled.append(f'condition {c[:60]}')
            res = False
        return not res if neg_ else res

    def exec_cmd(self, text, heredoc, pos):
        text = re.sub(r'\$\(([^()]*)\)', lambda m: self.expand(m.group(0), pos), text)
        text = re.sub(r'\$\{(\w+):([+-])([^}]*)\}',
                      lambda m: (m.group(3) if self.lookup(m.group(1), pos) else '') if m.group(2) == '+'
                      else (self.lookup(m.group(1), pos) or m.group(3)), text)
        try:
            raw_toks = shlex.split(text, posix=True, comments=True)
        except ValueError:
            self.unhandled.append(text[:80])
            return
        if not raw_toks:
            return
        toks = []
        for t in raw_toks:
            if t in ('$@', '$*'):
                toks.extend(pos)
            else:
                toks.append(self.expand(t, pos))
        while toks and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', toks[0]):
            name, val = toks[0].split('=', 1)
            if len(toks) == 1:
                self.vars[name] = val
            toks = toks[1:]
        if not toks:
            return
        cmd, args = toks[0], toks[1:]
        if cmd == 'cat' and heredoc is not None:
            for i, a in enumerate(args):
                if a in ('>', '>>') and i + 1 < len(args):
                    path = self.norm(args[i + 1])
                    content = self.expand(heredoc.replace('\\\\', '\\'), pos)
                    self.texts[path] = (self.texts.get(path, '') if a == '>>' else '') + content
                    return
            return
        if cmd == 'iconv' and heredoc is not None:
            for i, a in enumerate(args):
                if a in ('>', '>>') and i + 1 < len(args):
                    path = self.norm(args[i + 1])
                    content = self.expand(heredoc.replace('\\\\', '\\'), pos)
                    self.texts[path] = (self.texts.get(path, '') if a == '>>' else '') + content
                    return
            return
        if cmd == 'w_try' and args:
            cmd, args = args[0], args[1:]
        if cmd in ('w_download', 'w_download_to'):
            if cmd == 'w_download':
                args = [self.verb] + args
            if len(args) < 2:
                return
            pkg, url = args[0], args[1]
            sha = args[2] if len(args) > 2 and args[2] else None
            fname = args[3] if len(args) > 3 and args[3] else posixpath.basename(url)
            base = pkg if pkg.startswith('/') else '/WCACHE/' + pkg
            self.vfs[self.norm(base + '/' + fname)] = Src(url, sha)
            self.downloads.append(url)
            return
        if cmd in ('w_try_cabextract', 'cabextract'):
            d, flt, low, rest = None, [], False, []
            it = iter(args)
            for a in it:
                if a in ('-d', '--directory'):
                    d = next(it, None)
                elif a.startswith('--directory='):
                    d = a.split('=', 1)[1]
                elif a in ('-F', '--filter'):
                    flt.append(next(it, ''))
                elif a.startswith('--filter='):
                    flt.append(a.split('=', 1)[1])
                elif a in ('-L', '--lowercase'):
                    low = True
                elif a.startswith('-'):
                    pass
                else:
                    rest.append(a)
            for archive in rest:
                self.extract(archive, d, flt, 'cab', low)
            return
        if cmd in ('w_try_unzip', 'w_try_7z'):
            if len(args) < 2:
                return
            d, archive = args[0], args[1]
            flt = [a for a in args[2:] if not a.startswith('-')]
            self.extract(archive, d, flt, 'zip' if cmd == 'w_try_unzip' else '7z', False)
            return
        if cmd == 'tar':
            d, archive, fmt = self.cwd, None, 'tar'
            it = iter(args)
            for a in it:
                if a == '-C':
                    d = next(it, d)
                elif a.startswith('-') or re.match(r'^[xvJzjf]+$', a):
                    if 'J' in a:
                        fmt = 'tar.xz'
                    elif 'z' in a:
                        fmt = 'tar.gz'
                    elif 'j' in a:
                        fmt = 'tar.bz2'
                elif archive is None:
                    archive = a
            if archive:
                self.extract(archive, d, [], fmt, False)
            return
        if cmd in ('w_try_cp_dll', 'cp', 'mv'):
            paths = [a for a in args if not a.startswith('-')]
            if len(paths) == 2:
                self.copy(paths[0], paths[1], move=(cmd == 'mv'))
            else:
                self.unhandled.append(text[:80])
            return
        if cmd == 'w_try_cp_font_files':
            if len(args) < 2:
                return
            sdir, ddir = self.norm(args[0]), args[1]
            pat = args[2] if len(args) > 2 else '*.ttf'
            arch, sub = self.target(ddir)
            if arch != 'font':
                self.unhandled.append(text[:80])
                return
            matched = [k for k in self.vfs if posixpath.dirname(k) == sdir
                       and fnmatch.fnmatch(posixpath.basename(k).lower(), pat.lower())]
            if matched:
                for k in matched:
                    fname = posixpath.basename(k).lower()
                    self.add_file(self.vfs[k], 'font', '*' if any(ch in fname for ch in '*?[') else fname)
            elif sdir in self.vdirs:
                self.add_file(self.vdirs[sdir].renamed(pat), 'font', '*')
            elif any(sdir.startswith(d + '/') for d in self.vdirs):
                d = [d for d in self.vdirs if sdir.startswith(d + '/')][-1]
                self.add_file(self.vdirs[d].renamed(sdir[len(d) + 1:] + '/' + pat), 'font', '*')
            else:
                self.unhandled.append(f'font copy from unknown dir {sdir}')
            return
        if cmd == 'w_register_font':
            if len(args) >= 2:
                f, name = args[0], args[1]
                if f.lower().endswith(('.ttf', '.ttc')):
                    name = f'{name} (TrueType)'
                self.font_registry[name] = f
            return
        if cmd == 'w_register_font_replacement':
            if len(args) >= 2:
                self.add_registry('HKCU', 'Software\\Wine\\Fonts\\Replacements', args[0], 'REG_SZ', args[1])
            return
        if cmd == 'w_override_dlls':
            if not args:
                return
            mode = args[0]
            m = {'native': 'n', 'builtin': 'b', 'native,builtin': 'n,b', 'builtin,native': 'b,n',
                 'disabled': 'd', '': 'd'}
            for d in args[1:]:
                if mode == 'default':
                    self.overrides.pop(d, None)
                elif mode in m:
                    self.overrides[d] = m[mode]
                else:
                    self.problems.append(f'override mode {mode}')
            return
        if cmd == 'w_override_app_dlls':
            if len(args) < 3:
                return
            app, mode = args[0], args[1]
            m = {'native': 'native', 'builtin': 'builtin', 'native,builtin': 'native,builtin',
                 'builtin,native': 'builtin,native', 'disabled': ''}
            for d in args[2:]:
                self.add_registry('HKCU', f'Software\\Wine\\AppDefaults\\{app}\\DllOverrides', '*' + d, 'REG_SZ',
                                  m.get(mode, mode))
            return
        if cmd == 'w_set_winver':
            if args:
                self.winver = args[0]
            return
        if cmd == 'w_set_app_winver':
            if len(args) >= 2:
                self.add_registry('HKCU', f'Software\\Wine\\AppDefaults\\{args[0]}', 'Version', 'REG_SZ', args[1])
            return
        if cmd == 'w_try_regedit':
            files = [a for a in args if not (a.startswith('/') and len(a) <= 3)]
            if '/d' in args or '/D' in args:
                return
            for f in files:
                p = self.norm(f)
                text_ = self.texts.get(p)
                if text_ is None:
                    self.unhandled.append(f'regedit of unknown file {f}')
                    continue
                try:
                    for e in parse_reg(text_):
                        for k, v in e['values'].items():
                            self.add_registry(e['hive'], e['key'], k, v['type'], v['value'])
                except Unsupported as e:
                    self.problems.append(f'registry: {e.reason}')
            return
        if cmd in ('w_try_regsvr32', 'w_try_regsvr64', 'w_try_regsvr'):
            for a in args:
                if not a.startswith('/'):
                    self.regsvr.append({'arch': 'x64' if cmd == 'w_try_regsvr64' else 'x86', 'name': a})
            return
        if cmd in ('w_call', 'w_do_call'):
            if args:
                self.calls.append(args[0])
            return
        if cmd in WT_INSTALLER_CMDS:
            self.installers.append(' '.join(args[:3]))
            return
        if cmd == 'WINE':
            if any(a.upper().startswith('/T:') for a in args) and any(a.lower() == '/c' for a in args):
                archive = args[0]
                dest = next(a for a in args if a.upper().startswith('/T:'))[3:]
                self.extract(archive, dest, [], 'cab', False)
                return
            self.installers.append(' '.join(a for a in args[:3] if a) or 'an installer')
            return
        if cmd in ('w_try_cd', 'cd'):
            if args:
                self.cwd = self.norm(args[0])
            return
        if cmd == 'echo' and '>>' in args:
            return
        if cmd in self.wt.funcs and not cmd.startswith('load_'):
            self.run_func(cmd, args)
            return
        if cmd in WT_IGNORED_CMDS or cmd.startswith('w_package_') or cmd.startswith('w_warn'):
            return
        self.unhandled.append(text[:80])

    def add_registry(self, hive, key, name, typ, value):
        for e in self.registry:
            if e['hive'] == hive and e['key'] == key:
                if key == 'Software\\Wine\\DllOverrides' and hive == 'HKCU':
                    return
                e['values'][name] = {'type': typ, 'value': value}
                return
        if key == 'Software\\Wine\\DllOverrides' and hive == 'HKCU':
            return
        self.registry.append({'hive': hive, 'key': key, 'values': {name: {'type': typ, 'value': value}}})


class Winetricks:
    def __init__(self):
        self.meta = {}
        self.funcs = {}
        self.source = {}
        self._parsed = {}

    def add_script(self, text, source):
        lines = text.split('\n')
        i = 0
        while i < len(lines):
            ln = lines[i]
            if ln.startswith('w_metadata '):
                buf = ln
                while buf.rstrip().endswith('\\') and i + 1 < len(lines):
                    i += 1
                    buf = buf.rstrip()[:-1] + ' ' + lines[i].strip()
                try:
                    toks = shlex.split(buf)
                except ValueError:
                    i += 1
                    continue
                if len(toks) >= 3:
                    kv = {}
                    for t in toks[3:]:
                        if '=' in t:
                            k, v = t.split('=', 1)
                            kv[k] = v
                    self.meta[toks[1]] = dict(kv, category=toks[2])
                    self.source[toks[1]] = source
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*(\{\s*)?$', ln)
            if m:
                name = m.group(1)
                j = i + 1
                if not m.group(2):
                    if j < len(lines) and lines[j].strip() == '{':
                        j += 1
                    else:
                        i += 1
                        continue
                body = []
                while j < len(lines) and lines[j].rstrip() != '}':
                    body.append(lines[j])
                    j += 1
                self.funcs[name] = body
                self._parsed.pop(name, None)
                i = j
            i += 1

    def parsed(self, name):
        if name not in self._parsed:
            segs = split_shell(self.funcs.get(name, []))
            nodes, _ = parse_shell(segs, 0, set())
            self._parsed[name] = nodes
        return self._parsed[name]

    def verb_func(self, verb):
        base = verb.split('=', 1)[0]
        return 'load_' + base, (verb.split('=', 1)[1] if '=' in verb else None)

    def resolve(self, verb, stack=()):
        if verb == 'gui':
            return {'kind': 'unsupported', 'reason': 'opens the interactive winetricks GUI'}
        if verb not in self.meta:
            return {'kind': 'unsupported', 'reason': 'unknown winetricks verb'}
        meta = self.meta[verb]
        fname, arg = self.verb_func(verb)
        if fname not in self.funcs:
            return {'kind': 'unsupported', 'reason': f'no {fname} in winetricks', 'title': meta.get('title')}
        it = WTInterp(self, verb)
        for k, v in meta.items():
            if re.match(r'^file\d+$', k):
                it.vars[k] = it.expand(v, [])
        if arg is not None:
            it.vars['arg'] = arg
        it.run_func(fname, [arg] if arg is not None else [])
        result = {'title': meta.get('title'), 'category': meta.get('category'),
                  'source': self.source.get(verb, 'winetricks')}
        files = [dict(f) for f in it.files]
        overrides = OrderedDict(it.overrides)
        registry = list(it.registry)
        fonts = OrderedDict(it.font_registry)
        winver = it.winver
        regsvr = list(it.regsvr)
        installers = list(it.installers)
        unhandled = list(it.unhandled)
        problems = list(it.problems)
        includes = []
        failed_calls = []
        for callee in it.calls:
            if callee in stack or callee == verb:
                continue
            sub = self.resolve(callee, stack + (verb,))
            includes.append(callee)
            if sub['kind'] in ('unsupported',):
                installers.append(f'{callee}: {sub.get("reason")}')
                failed_calls.append(callee)
                continue
            for f in sub.get('files', []):
                if f not in files:
                    files.append(f)
            for k, v in sub.get('overrides', {}).items():
                overrides.setdefault(k, v)
            registry.extend(sub.get('registry', []))
            fonts.update(sub.get('fontRegistry', {}))
            regsvr.extend(sub.get('regsvr32', []))
            if sub.get('kind') == 'winver' and not winver:
                winver = sub.get('version')
            if sub.get('kind') == 'premark':
                installers.append(f'{callee}: installer')
        if includes:
            result['includes'] = includes
        category = meta.get('category')
        is_font = category == 'fonts'
        for f in files:
            f.pop('_font', None)
        if category == 'settings':
            if verb.startswith('sound='):
                result.update(kind='unsupported', reason='audio driver is fixed on Android')
            elif problems:
                result.update(kind='unsupported', reason='; '.join(problems))
            elif winver and not registry:
                result.update(kind='winver', version=winver)
            elif registry:
                result.update(kind='registry', registry=registry)
                if winver:
                    result['version'] = winver
            else:
                result.update(kind='unsupported', reason='setting could not be parsed')
            return result
        if verb.startswith('vcrun') and installers:
            result.update(kind='premark', overrides=dict(overrides),
                          note='Proton/Wine ship these runtimes; winetricks runs the MS installer, which is skipped')
            return result
        if installers and not files:
            result.update(kind='unsupported', reason='runs an installer under Wine: ' + installers[0])
            return result
        if not files and not overrides and not registry and not fonts:
            reason = 'nothing to install was parsed'
            if unhandled:
                reason += ': ' + unhandled[0]
            result.update(kind='unsupported', reason=reason)
            return result
        broken = [u for u in unhandled if 'unknown archive' in u or 'unknown file' in u]
        if failed_calls and verb != 'allfonts':
            result.update(kind='unsupported', reason=f'depends on unsupported verb {failed_calls[0]}')
            return result
        if broken:
            result.update(kind='unsupported', reason='could not follow the winetricks steps: ' + broken[0])
            return result
        result['kind'] = 'font' if is_font and files else 'dll'
        result['files'] = files
        if overrides:
            result['overrides'] = dict(overrides)
        if registry:
            result['registry'] = registry
        if fonts:
            result['fontRegistry'] = dict(fonts)
        if regsvr:
            uniq = []
            for r in regsvr:
                if r not in uniq:
                    uniq.append(r)
            result['regsvr32'] = uniq
        if winver:
            result['version'] = winver
        if installers:
            result['skipped'] = installers
        unparsed = [u for u in unhandled if not u.startswith('condition') and not u.startswith('exit')]
        if unparsed:
            result['unparsed'] = unparsed
        if any('KB976932' in f['url'] or 'KB936929' in f['url'].upper() or 'kb936929' in f['url'] for f in files):
            result['note'] = 'extracts from a Windows service pack (several hundred MB download)'
        return result


def split_shell(body):
    segs = []
    i = 0
    while i < len(body):
        s = body[i].strip()
        if not s or s.startswith('#'):
            i += 1
            continue
        while s.endswith('\\') and i + 1 < len(body):
            i += 1
            s = s[:-1] + ' ' + body[i].strip()
        hd = None
        m = re.search(r"<<-?\s*['\"]?(_?[A-Za-z_]+_?)['\"]?\s*$", s)
        if m:
            marker = m.group(1)
            content = []
            i += 1
            while i < len(body) and body[i].strip() != marker:
                content.append(body[i])
                i += 1
            hd = '\n'.join(content) + '\n'
        for part in split_semicolons(s):
            segs.append((part, None))
        if hd is not None and segs:
            segs[-1] = (segs[-1][0], hd)
        i += 1
    return segs


def split_semicolons(s):
    out = []
    cur = []
    q = None
    depth = 0
    i = 0
    while i < len(s):
        ch = s[i]
        if q:
            cur.append(ch)
            if ch == '\\' and q == '"' and i + 1 < len(s):
                cur.append(s[i + 1])
                i += 2
                continue
            if ch == q:
                q = None
        elif ch in ('"', "'"):
            q = ch
            cur.append(ch)
        elif ch == '\\' and i + 1 < len(s):
            cur.append(ch)
            cur.append(s[i + 1])
            i += 2
            continue
        elif ch == '$' and i + 1 < len(s) and s[i + 1] == '(':
            depth += 1
            cur.append(ch)
        elif ch == ')' and depth:
            depth -= 1
            cur.append(ch)
        elif ch == '#' and depth == 0 and (i == 0 or s[i - 1] in ' \t'):
            break
        elif ch == ';' and depth == 0:
            if i + 1 < len(s) and s[i + 1] == ';':
                if ''.join(cur).strip():
                    out.append(''.join(cur).strip())
                out.append(';;')
                cur = []
                i += 2
                continue
            if ''.join(cur).strip():
                out.append(''.join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    if ''.join(cur).strip():
        out.append(''.join(cur).strip())
    return out


def _first(text):
    parts = text.split(None, 1)
    return parts[0] if parts else ''


def _strip_kw(segs, i, kw):
    text, hd = segs[i]
    if text == kw:
        return i + 1
    if text.startswith(kw + ' '):
        segs[i] = (text[len(kw) + 1:].strip(), hd)
        return i
    return i


def parse_shell(segs, i, stops):
    nodes = []
    while i < len(segs):
        text, hd = segs[i]
        first = _first(text)
        if first in stops or text in stops:
            return nodes, i
        if first == 'if':
            branches = []
            cond = text[2:].strip()
            i += 1
            if i < len(segs):
                i = _strip_kw(segs, i, 'then')
            body, i = parse_shell(segs, i, {'elif', 'else', 'fi'})
            branches.append((cond, body))
            else_body = []
            while i < len(segs):
                t, _ = segs[i]
                f = _first(t)
                if f == 'elif':
                    cond = t[4:].strip()
                    i += 1
                    if i < len(segs):
                        i = _strip_kw(segs, i, 'then')
                    body, i = parse_shell(segs, i, {'elif', 'else', 'fi'})
                    branches.append((cond, body))
                elif f == 'else':
                    i = _strip_kw(segs, i, 'else')
                    else_body, i = parse_shell(segs, i, {'fi'})
                elif f == 'fi':
                    i += 1
                    break
                else:
                    i += 1
                    break
            nodes.append(('if', branches, else_body))
            continue
        if first == 'for':
            m = re.match(r'^for\s+(\w+)\s+in\s+(.*)$', text)
            i += 1
            if i < len(segs):
                i = _strip_kw(segs, i, 'do')
            body, i = parse_shell(segs, i, {'done'})
            i += 1
            if m:
                nodes.append(('for', m.group(1), m.group(2), body))
            continue
        if first in ('while', 'until'):
            i += 1
            if i < len(segs):
                i = _strip_kw(segs, i, 'do')
            _, i = parse_shell(segs, i, {'done'})
            i += 1
            nodes.append(('while',))
            continue
        if first == 'case':
            m = re.match(r'^case\s+(.*?)\s+in$', text)
            word = m.group(1) if m else ''
            i += 1
            arms = []
            while i < len(segs):
                t, h = segs[i]
                if _first(t) == 'esac':
                    i += 1
                    break
                if t == ';;':
                    i += 1
                    continue
                pm = re.match(r'^\(?\s*([^)]*?)\s*\)\s*(.*)$', t)
                if not pm:
                    i += 1
                    continue
                pats = [p for p in pm.group(1).split('|')]
                rest = pm.group(2).strip()
                if rest:
                    segs[i] = (rest, h)
                else:
                    i += 1
                body, i = parse_shell(segs, i, {';;', 'esac'})
                arms.append((pats, body))
            nodes.append(('case', word, arms))
            continue
        if text in ('{', '}', ';;'):
            i += 1
            continue
        nodes.append(('cmd', text, hd))
        i += 1
    return nodes, i


def collect_verbs(docs):
    used = set()
    for d in docs:
        for s in d['steps']:
            if s['op'] == 'verb':
                used.add(s['name'])
            if s['op'] == 'unsupported' and isinstance(s.get('step'), dict) and s['step'].get('op') == 'verb':
                used.add(s['step']['name'])
    return used


def _load_verbs_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location('pf_verbs', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'verbs.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERBS = _load_verbs_module()


def build_verbs(wt, names):
    out = {}
    for n in sorted(set(names) | set(VERBS.EXTRA_VERBS)):
        try:
            out[n] = wt.resolve(n)
        except RecursionError:
            out[n] = {'kind': 'unsupported', 'reason': 'verb recursion'}
        out[n] = {k: v for k, v in out[n].items() if v is not None}
    return VERBS.classify_all(out, wt)


def git_info(path, file=None):
    cmd = ['git', '-C', path, 'log', '-1', '--format=%H%n%cI']
    if file:
        cmd += ['--', file]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None, None
    lines = r.stdout.strip().split('\n')
    if r.returncode != 0 or len(lines) < 2 or not lines[0]:
        return None, None
    return lines[0], lines[1]


def dump_json(obj):
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + '\n'


SAFE_ID = re.compile(r'[^A-Za-z0-9._-]')


def safe_id(s):
    return SAFE_ID.sub('_', s)


def load_umu_db(path):
    rows = []
    if path and os.path.isfile(path):
        with open(path, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                rows.append(row)
    return rows


def gather_entries(pf_root):
    entries = []
    for d in sorted(os.listdir(pf_root)):
        if not d.startswith('gamefixes-'):
            continue
        store = d[len('gamefixes-'):]
        full = os.path.join(pf_root, d)
        if not os.path.isdir(full):
            continue
        for f in sorted(os.listdir(full)):
            if not f.endswith('.py') or f in ('__init__.py', 'default.py'):
                continue
            p = os.path.join(full, f)
            real = os.path.realpath(p)
            entries.append({
                'store': store, 'stem': f[:-3], 'path': f'{d}/{f}', 'abs': p,
                'link': os.path.islink(p), 'real': real,
                'real_rel': os.path.relpath(real, os.path.realpath(pf_root)),
            })
    return entries


def doc_ids(entry, rows):
    store, stem = entry['store'], entry['stem']
    if store in ('steam', 'umu'):
        return [stem], True
    db_store = DB_STORE.get(store, store)
    codes = [r['CODENAME'] for r in rows if r.get('UMU_ID') == stem and r.get('STORE') == db_store
             and r.get('CODENAME') and r['CODENAME'] != 'none']
    seen = []
    for c in codes:
        if c not in seen:
            seen.append(c)
    if not seen:
        return [stem], False
    return seen, True


def db_title(entry, rows):
    store, stem = entry['store'], entry['stem']
    if store == 'steam':
        for r in rows:
            if r.get('UMU_ID') == f'umu-{stem}':
                return r.get('TITLE')
        return None
    db_store = DB_STORE.get(store, store)
    for r in rows:
        if r.get('UMU_ID') == stem and r.get('STORE') == db_store:
            return r.get('TITLE')
    for r in rows:
        if r.get('UMU_ID') == stem:
            return r.get('TITLE')
    return None


LAUNCH_OPTION_TEMPLATES = {
    'protontricks': (1, {'op': 'verb', 'name': '{value}'}),
    'append_argument': (1, {'op': 'launch_args', 'append': '{value}'}),
    'set_dxvk_option': (2, {'op': 'dxvk_config', 'set': {'{key}': '{value}'}}),
    'replace_command': (2, {'op': 'replace_command', 'from': '{key}', 'to': '{value}', 'regex': True}),
    'set_environment': (2, {'op': 'env', 'set': {'{key}': '{value}'}}),
}


def _util_call_name(node):
    if isinstance(node, ast.Expr):
        node = node.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and isinstance(node.func.value, ast.Name) and node.func.value.id == 'util':
        return node.func.attr, node.args
    return None, None


def parse_launch_options(tree, source):
    """Reads the `-pf_<name>=<value>` branches of a store default.py."""
    options = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and len(node.test.ops) == 1
                and isinstance(node.test.ops[0], ast.Eq) and isinstance(node.test.comparators[0], ast.Constant)
                and isinstance(node.test.comparators[0].value, str)
                and node.test.comparators[0].value.startswith('-pf_')):
            continue
        name = node.test.comparators[0].value
        opt = {'name': name}
        body = node.body
        split = None
        if len(body) == 2 and isinstance(body[0], ast.Assign) and isinstance(body[1], ast.If):
            call = body[0].value
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == 'partition'
                    and len(call.args) == 1 and isinstance(call.args[0], ast.Constant)):
                split = call.args[0].value
                body = body[1].body
        if split is not None:
            opt['split'] = split
        fname, fargs = _util_call_name(body[0]) if len(body) == 1 else (None, None)
        tmpl = LAUNCH_OPTION_TEMPLATES.get(fname)
        if tmpl is None or tmpl[0] != len(fargs or []) or (tmpl[0] == 2) != (split is not None):
            opt['step'] = {'op': 'unsupported', 'reason': f'launch option body not recognised ({fname or "?"})',
                           'source': ast.get_source_segment(source, node) or ''}
        else:
            opt['step'] = copy.deepcopy(tmpl[1])
        options.append(opt)
    return options


def strip_argv_helpers(tree):
    """Drops local functions (and their calls) that read sys.argv; parse_launch_options covers them."""
    argv_funcs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name not in ('main', 'early', 'main_with_id', 'early_with_id'):
            if any(isinstance(n, ast.Attribute) and n.attr == 'argv' for n in ast.walk(node)):
                argv_funcs.add(node.name)

    class Strip(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            self.generic_visit(node)
            if node.name in argv_funcs:
                return None
            if not node.body:
                node.body = [ast.Pass()]
            return node

        def visit_Expr(self, node):
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) \
                    and node.value.func.id in argv_funcs:
                return None
            return node

    return ast.fix_missing_locations(Strip().visit(tree))


def convert_default(source, store):
    tree = ast.parse(source)
    options = parse_launch_options(tree, source)
    stripped = ast.unparse(strip_argv_helpers(ast.parse(source)))
    steps, coverage, notes = convert_fix(stripped, 'default', store, None)
    return steps, options, coverage


def generate(pf_root, wt_paths, db_path, out_dir, upstream_commit=None, upstream_date=None,
             winetricks_commit=None, extra_verbs=None, per_file_commits=True, overrides_dir=OVERRIDES_DIR):
    rows = load_umu_db(db_path)
    overrides = load_overrides(overrides_dir)
    override_use = {k: {'status': 'unused', 'docs': []} for k in overrides}
    converted_coverage = {}
    head, head_date = git_info(pf_root)
    upstream_commit = upstream_commit or head or 'unknown'
    upstream_date = upstream_date or head_date or ''
    entries = gather_entries(pf_root)

    by_real = OrderedDict()
    for e in entries:
        by_real.setdefault(e['real'], []).append(e)

    file_commit = {}
    docs = []
    taken = {}
    collisions = []
    unmapped = []
    conversion_cache = {}
    real_order = sorted(by_real, key=lambda r: (any(not x['link'] for x in by_real[r]) is False, r))
    for real in real_order:
        group = by_real[real]
        group.sort(key=lambda e: (e['link'], e['store'] != 'steam', e['path']))
        with open(real, encoding='utf-8') as f:
            source = f.read()
        with open(real, 'rb') as f:
            source_sha = hashlib.sha256(f.read()).hexdigest()
        real_key = None
        real_dir, real_file = os.path.split(os.path.relpath(real, os.path.realpath(pf_root)))
        if real_dir.startswith('gamefixes-') and real_file.endswith('.py'):
            real_key = f'{real_dir[len("gamefixes-"):]}/{real_file[:-3]}'
        doc_name = module_docstring_name(source)
        if real not in file_commit:
            sha = None
            if per_file_commits:
                sha, _ = git_info(pf_root, os.path.relpath(real, pf_root))
            file_commit[real] = (sha or upstream_commit)[:7]
        identities = []
        for e in group:
            ids, mapped = doc_ids(e, rows)
            if not mapped and e['store'] not in ('steam', 'umu'):
                unmapped.append(e['path'])
            for i in ids:
                identities.append((e, i))
        group_docs = []
        for e, ident in identities:
            key = (e['store'], ident)
            if key in taken:
                collisions.append(f'{e["store"]}/{ident} from {e["path"]} (already from {taken[key]})')
                continue
            taken[key] = e['path']
            ck = (real, e['stem'], e['store'])
            if ck not in conversion_cache:
                umu_id = e['stem'] if e['store'] != 'steam' else None
                try:
                    conversion_cache[ck] = convert_fix(source, e['stem'], e['store'], umu_id)
                except SyntaxError as ex:
                    conversion_cache[ck] = ([{'op': 'unsupported', 'reason': f'syntax error: {ex}', 'source': ''}],
                                            'none', [])
            steps, coverage, notes = conversion_cache[ck]
            converted_coverage[(e['store'], ident)] = coverage
            override_key = next((k for k in (f'{e["store"]}/{e["stem"]}', real_key) if k in overrides), None)
            if override_key:
                ov = overrides[override_key]
                if ov.get('upstreamSha256') == source_sha:
                    steps, coverage, notes = apply_override(ov, steps, coverage, notes)
                    override_use[override_key]['status'] = 'applied'
                else:
                    override_use[override_key]['status'] = 'stale'
                    override_key = None
            if override_key:
                override_use[override_key]['docs'].append(f'{e["store"]}/{ident}')
            name = db_title(e, rows)
            if not name and not e['link'] and doc_name and not doc_name.endswith(':'):
                name = doc_name
            name = name or (doc_name if doc_name and not doc_name.endswith(':') else None) or ident
            doc = OrderedDict()
            doc['schema'] = SCHEMA
            doc['store'] = e['store']
            doc['id'] = ident
            doc['name'] = name
            doc['upstream'] = e['path']
            doc['upstreamCommit'] = file_commit[real]
            doc['coverage'] = coverage
            doc['steps'] = steps
            if e['store'] != 'steam':
                doc['umuId'] = e['stem']
            if notes:
                doc['notes'] = notes
            if override_key:
                doc['override'] = f'tools/protonfixes/overrides/{override_key}.json'
            group_docs.append(doc)
        if not group_docs:
            continue
        canonical = group_docs[0]
        aliases = sorted(({'store': d['store'], 'id': d['id']} for d in group_docs[1:]),
                         key=lambda a: (a['store'], a['id']))
        if aliases:
            canonical['aliases'] = aliases
        for d in group_docs[1:]:
            d['canonical'] = {'store': canonical['store'], 'id': canonical['id']}
        docs.extend(group_docs)

    wt = Winetricks()
    wt_commit = winetricks_commit
    for path, source in wt_paths:
        with open(path, encoding='utf-8', errors='replace') as f:
            wt.add_script(f.read(), source)
    verb_names = set(collect_verbs(docs)) | set(extra_verbs or [])
    verbs = build_verbs(wt, verb_names)

    os.makedirs(out_dir, exist_ok=True)
    written = set()
    index_entries = {}
    for d in sorted(docs, key=lambda x: (x['store'], x['id'])):
        rel = f'{d["store"]}/{safe_id(d["id"])}.json'
        data = dump_json(d)
        path = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(data)
        written.add(os.path.normpath(path))
        index_entries[f'{d["store"]}/{d["id"]}'] = {
            'file': rel, 'sha256': hashlib.sha256(data.encode('utf-8')).hexdigest(),
            'name': d['name'], 'coverage': d['coverage'],
        }
    defaults = {}
    for store in KNOWN_STORES:
        p = os.path.join(pf_root, f'gamefixes-{store}', 'default.py')
        if not os.path.isfile(p):
            continue
        with open(p, encoding='utf-8') as f:
            source = f.read()
        steps, options, coverage = convert_default(source, store)
        sha = None
        if per_file_commits:
            sha, _ = git_info(pf_root, os.path.relpath(os.path.realpath(p), pf_root))
        ddoc = OrderedDict()
        ddoc['schema'] = SCHEMA
        ddoc['store'] = store
        ddoc['upstream'] = f'gamefixes-{store}/default.py'
        ddoc['upstreamCommit'] = (sha or upstream_commit)[:7]
        ddoc['coverage'] = coverage
        ddoc['steps'] = steps
        ddoc['launchOptions'] = options
        rel = f'{store}/default.json'
        data = dump_json(ddoc)
        path = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(data)
        written.add(os.path.normpath(path))
        defaults[store] = {'file': rel, 'sha256': hashlib.sha256(data.encode('utf-8')).hexdigest()}

    for store_dir in os.listdir(out_dir):
        full = os.path.join(out_dir, store_dir)
        if store_dir in KNOWN_STORES and os.path.isdir(full):
            for f in os.listdir(full):
                p = os.path.normpath(os.path.join(full, f))
                if f.endswith('.json') and p not in written:
                    os.remove(p)
            if not os.listdir(full):
                os.rmdir(full)

    verbs_doc = {'schema': SCHEMA, 'winetricksCommit': wt_commit or 'unknown',
                 'protonfixesCommit': upstream_commit, 'verbs': verbs}
    with open(os.path.join(out_dir, 'verbs.json'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(dump_json(verbs_doc))

    index = {'schema': SCHEMA, 'upstreamCommit': upstream_commit, 'upstreamDate': upstream_date,
             'winetricksCommit': wt_commit or 'unknown', 'entries': index_entries, 'defaults': defaults}
    index_path = os.path.join(out_dir, 'index.json')
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    if os.path.isfile(index_path):
        try:
            with open(index_path, encoding='utf-8') as f:
                old = json.load(f)
            old_cmp = {k: v for k, v in old.items() if k != 'generatedAt'}
            if old_cmp == index and old.get('generatedAt'):
                generated_at = old['generatedAt']
        except (OSError, ValueError):
            pass
    index['generatedAt'] = generated_at
    with open(index_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(dump_json(index))

    report = build_report(docs, verbs, entries, collisions, unmapped, upstream_commit, wt_commit, pf_root,
                          converted_coverage, override_use)
    with open(os.path.join(out_dir, 'REPORT.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(report)
    return docs, verbs


def _md(s):
    return s.replace('|', '\\|').replace('\n', ' ')


def build_report(docs, verbs, entries, collisions, unmapped, upstream_commit, wt_commit, pf_root,
                 converted_coverage=None, override_use=None):
    lines = ['# protonfixes import report', '']
    lines.append(f'- umu-protonfixes commit: `{upstream_commit}`')
    lines.append(f'- winetricks commit: `{wt_commit or "unknown"}`')
    real = sum(1 for e in entries if not e['link'])
    lines.append(f'- upstream fix files: {len(entries)} ({real} real files, {len(entries) - real} symlinks; '
                 '`__init__.py` is not imported; each `default.py` becomes `<store>/default.json`)')
    lines.append(f'- documents written: {len(docs)} '
                 f'({sum(1 for d in docs if "canonical" not in d)} canonical, '
                 f'{sum(1 for d in docs if "canonical" in d)} aliases)')
    cov = Counter(d['coverage'] for d in docs)
    lines.append(f'- coverage: full {cov.get("full", 0)}, partial {cov.get("partial", 0)}, none {cov.get("none", 0)}')
    if converted_coverage:
        cc = Counter(converted_coverage.values())
        lines.append(f'- coverage from the converter alone (before overrides): full {cc.get("full", 0)}, '
                     f'partial {cc.get("partial", 0)}, none {cc.get("none", 0)}')
    if override_use:
        applied = [k for k, v in override_use.items() if v['status'] == 'applied']
        lines.append(f'- hand-ported overrides: {len(override_use)} files, {len(applied)} applied to '
                     f'{sum(len(v["docs"]) for v in override_use.values())} documents')
    unsupported_verbs = {n for n, v in verbs.items() if v.get('kind') == 'unsupported'}
    eff = 0
    for d in docs:
        if d['coverage'] == 'full' and not any(s['op'] == 'verb' and s['name'] in unsupported_verbs for s in d['steps']):
            eff += 1
    lines.append(f'- full documents whose verbs are all supported too: {eff}')
    lines.append('')
    lines.append('## Documents per store')
    lines.append('')
    lines.append('| store | documents | canonical | aliases | full | partial | none |')
    lines.append('|---|---|---|---|---|---|---|')
    for store in sorted({d['store'] for d in docs}):
        ds = [d for d in docs if d['store'] == store]
        c = Counter(d['coverage'] for d in ds)
        lines.append(f'| {store} | {len(ds)} | {sum(1 for d in ds if "canonical" not in d)} | '
                     f'{sum(1 for d in ds if "canonical" in d)} | {c.get("full", 0)} | {c.get("partial", 0)} | {c.get("none", 0)} |')
    lines.append('')
    reasons = Counter()
    for d in docs:
        if 'canonical' in d:
            continue
        for s in d['steps']:
            if s['op'] == 'unsupported':
                reasons[s['reason']] += 1
    lines.append('## Unsupported step reasons (canonical documents)')
    lines.append('')
    lines.append('| count | reason |')
    lines.append('|---|---|')
    for r, n in reasons.most_common():
        lines.append(f'| {n} | {_md(r)} |')
    lines.append('')
    lines.append('## Partial and none documents')
    lines.append('')
    lines.append('| document | coverage | name | reasons |')
    lines.append('|---|---|---|---|')
    for d in sorted(docs, key=lambda x: (x['store'], x['id'])):
        if d['coverage'] == 'full':
            continue
        rs = []
        for s in d['steps']:
            if s['op'] == 'unsupported' and s['reason'] not in rs:
                rs.append(s['reason'])
        if not rs and d.get('notes'):
            rs = d['notes']
        alias = f' (alias of {d["canonical"]["store"]}/{d["canonical"]["id"]})' if 'canonical' in d else ''
        lines.append(f'| {d["store"]}/{d["id"]}{alias} | {d["coverage"]} | {_md(d["name"] or "")} | {_md("; ".join(rs))} |')
    lines.append('')
    if override_use:
        lines.append('## Hand-ported overrides (`tools/protonfixes/overrides/`)')
        lines.append('')
        lines.append('| override | status | documents | coverage (converter → override) |')
        lines.append('|---|---|---|---|')
        by_doc = {(d['store'], d['id']): d for d in docs}
        for key in sorted(override_use):
            use = override_use[key]
            covs = []
            for ref in use['docs']:
                st, i = ref.split('/', 1)
                before = (converted_coverage or {}).get((st, i), '?')
                covs.append(f'{before} → {by_doc[(st, i)]["coverage"]}')
            uniq = sorted(set(covs))
            lines.append(f'| {key} | {use["status"]} | {len(use["docs"])} | {_md(", ".join(uniq))} |')
        lines.append('')
        lines.append('`stale` means the upstream file changed since the override was written (its `upstreamSha256` '
                     'no longer matches), so the converter output is used until the override is reviewed.')
        lines.append('')
    hazards = []
    for d in sorted(docs, key=lambda x: (x['store'], x['id'])):
        if 'canonical' in d or 'override' in d:
            continue
        for j, i, cond in sequencing_hazards(d['steps']):
            hazards.append(f'- {d["store"]}/{d["id"]}: step {i} tests `{json.dumps(cond)}`, which step {j} '
                           f'({d["steps"][j]["op"]}) changes')
    if hazards:
        lines.append('## Sequencing hazards')
        lines.append('')
        lines.append('Upstream tests these conditions once, before the earlier step runs. If `when` is evaluated '
                     'per step at run time, the later step is skipped.')
        lines.append('')
        lines.extend(hazards)
        lines.append('')
    lines.append('## Winetricks verbs')
    lines.append('')
    used = collect_verbs(docs)
    by_kind = {}
    for n, v in sorted(verbs.items()):
        by_kind.setdefault(v.get('kind'), []).append(n)
    for kind in ('dll', 'font', 'registry', 'winver', 'premark', 'unsupported'):
        names = by_kind.get(kind, [])
        lines.append(f'### {kind} ({len(names)})')
        lines.append('')
        if kind == 'unsupported':
            lines.append('| verb | used by fixes | reason |')
            lines.append('|---|---|---|')
            for n in names:
                lines.append(f'| {n} | {"yes" if n in used else "no"} | {_md(verbs[n].get("reason", ""))} |')
        else:
            lines.append(', '.join(f'`{n}`{"*" if n in used else ""}' for n in names) or '(none)')
        lines.append('')
    lines.append('`*` = referenced by at least one fix.')
    lines.append('')
    if unmapped:
        lines.append('## Non-Steam fixes without a store id in umu-database (umu id used as id)')
        lines.append('')
        for u in unmapped:
            lines.append(f'- {u}')
        lines.append('')
    if collisions:
        lines.append('## Skipped duplicate ids')
        lines.append('')
        for c in collisions:
            lines.append(f'- {c}')
        lines.append('')
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--protonfixes', required=True, help='umu-protonfixes checkout')
    ap.add_argument('--winetricks', required=True, help='path to the winetricks script (src/winetricks)')
    ap.add_argument('--umu-database', help='umu-database.csv (default: <protonfixes>/umu-database.csv)')
    ap.add_argument('--out', required=True, help='output directory (data/)')
    ap.add_argument('--upstream-commit')
    ap.add_argument('--upstream-date')
    ap.add_argument('--winetricks-commit')
    ap.add_argument('--no-per-file-commits', action='store_true')
    a = ap.parse_args(argv)
    pf = os.path.abspath(a.protonfixes)
    db = a.umu_database or os.path.join(pf, 'umu-database.csv')
    if not os.path.isfile(db):
        print(f'umu-database not found at {db}', file=sys.stderr)
        return 2
    wt_commit = a.winetricks_commit
    if not wt_commit:
        wt_dir = os.path.dirname(os.path.abspath(a.winetricks))
        wt_commit, _ = git_info(wt_dir)
    wt_paths = [(a.winetricks, 'winetricks')]
    verbs_dir = os.path.join(pf, 'verbs')
    if os.path.isdir(verbs_dir):
        for f in sorted(os.listdir(verbs_dir)):
            if f.endswith('.verb'):
                wt_paths.append((os.path.join(verbs_dir, f), 'umu-protonfixes'))
    docs, verbs = generate(pf, wt_paths, db, a.out, a.upstream_commit, a.upstream_date, wt_commit,
                           DEFAULT_EXTRA_VERBS, not a.no_per_file_commits)
    cov = Counter(d['coverage'] for d in docs)
    print(f'documents: {len(docs)}  full: {cov.get("full", 0)}  partial: {cov.get("partial", 0)}  '
          f'none: {cov.get("none", 0)}  verbs: {len(verbs)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
