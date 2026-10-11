#!/usr/bin/env python3
"""Build the offline-install layouts for the winetricks installer verbs.

For every `offline_installer` verb in data/verbs.json this downloads the official
installers (pinned sha256), finds the Windows Installer packages inside them, reads their
File/Component/Directory/Registry tables and writes data/verbs/<verb>.layout.json:
which archive member each file comes from, where it goes in the prefix, and the registry
values to write. The app then needs only the layout and libarchive.

The MSI reader is pure Python. Cabinets are read with bsdtar, cabextract or 7z when one is
installed, else with the built-in reader (stored and MSZIP only). Installers are untrusted
data: nothing in them is executed.
"""

import argparse
import codecs
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
import zlib

LAYOUT_SCHEMA = 1
MSI_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
CAB_MAGIC = b'MSCF\0\0\0\0'
BURN_MAGIC = 0x00F14300
FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
MAX_DOWNLOAD_BYTES = 400 * 1024 * 1024
MAX_DEPTH = 3
USER_AGENT = 'GameNative-verbs/1 (+https://github.com/utkarshdalal/GameNative)'

MSITYPE_VALID = 0x0100
MSITYPE_STRING = 0x0800
MSITYPE_NULLABLE = 0x1000
MSITYPE_TEMPORARY = 0x4000
COMPONENT_64BIT = 0x0100
FILE_NONCOMPRESSED = 0x2000
SET_PROPERTY = 51
SET_DIRECTORY = 35
ERROR_ACTION = 19

KNOWN_FOLDERS = {
    'TARGETDIR': 'C:\\', 'ROOTDRIVE': 'C:\\', 'WindowsVolume': 'C:\\',
    'WindowsFolder': 'C:\\windows\\',
    'SystemFolder': 'C:\\windows\\syswow64\\',
    'System64Folder': 'C:\\windows\\system32\\',
    'System16Folder': 'C:\\windows\\system\\',
    'FontsFolder': 'C:\\windows\\Fonts\\',
    'ProgramFilesFolder': 'C:\\Program Files (x86)\\',
    'ProgramFiles64Folder': 'C:\\Program Files\\',
    'CommonFilesFolder': 'C:\\Program Files (x86)\\Common Files\\',
    'CommonFiles64Folder': 'C:\\Program Files\\Common Files\\',
    'CommonAppDataFolder': 'C:\\ProgramData\\',
    'AppDataFolder': 'C:\\users\\steamuser\\AppData\\Roaming\\',
    'LocalAppDataFolder': 'C:\\users\\steamuser\\AppData\\Local\\',
    'PersonalFolder': 'C:\\users\\steamuser\\Documents\\',
}
SKIPPED_FOLDERS = {
    'TempFolder', 'DesktopFolder', 'ProgramMenuFolder', 'StartMenuFolder', 'StartupFolder', 'SendToFolder',
    'FavoritesFolder', 'NetHoodFolder', 'PrintHoodFolder', 'RecentFolder', 'TemplateFolder', 'MyPicturesFolder',
    'AdminToolsFolder',
}
SYSTEM_PROPERTIES = {
    'VersionNT': '603', 'VersionNT64': '603', 'WindowsBuild': '9600', 'ServicePackLevel': '0', 'Msix64': '6',
    'ALLUSERS': '1', 'Privileged': '1', 'AdminUser': '1', 'MsiNTProductType': '1', 'UILevel': '2',
    'INSTALLLEVEL': '1', 'VersionMsi': '5.00',
}
UNSUPPORTED_TABLES = ('SelfReg', 'Class', 'ProgId', 'TypeLib', 'Extension', 'MIME', 'AppId', 'IniFile',
                      'ServiceInstall', 'MoveFile', 'PublishComponent', 'ODBCDriver')
SKIP_PLATFORMS = ('arm64', 'arm')


class LayoutError(Exception):
    pass


# ---------------------------------------------------------------- OLE2 compound files

class CompoundFile:
    def __init__(self, data):
        if data[:8] != MSI_MAGIC:
            raise LayoutError('not a compound file')
        self.data = data
        self.sector_size = 1 << struct.unpack_from('<H', data, 0x1E)[0]
        self.mini_size = 1 << struct.unpack_from('<H', data, 0x20)[0]
        fat_count, dir_start = struct.unpack_from('<II', data, 0x2C)
        self.cutoff, minifat_start, minifat_count, difat_start, difat_count = struct.unpack_from('<IIIII', data, 0x38)
        per_sector = self.sector_size // 4
        difat = list(struct.unpack_from('<109I', data, 0x4C))
        sector, seen = difat_start, 0
        while sector not in (FREESECT, ENDOFCHAIN) and seen < difat_count:
            entries = struct.unpack_from('<%dI' % per_sector, data, self.offset(sector))
            difat.extend(entries[:-1])
            sector = entries[-1]
            seen += 1
        self.fat = []
        for sector in [s for s in difat if s not in (FREESECT, ENDOFCHAIN)][:fat_count]:
            self.fat.extend(struct.unpack_from('<%dI' % per_sector, data, self.offset(sector)))
        directory = self.chain_bytes(dir_start)
        self.entries = []
        for i in range(len(directory) // 128):
            entry = directory[i * 128:(i + 1) * 128]
            name_len = struct.unpack_from('<H', entry, 64)[0]
            name = entry[:max(0, min(64, name_len) - 2)].decode('utf-16-le', 'surrogatepass')
            start = struct.unpack_from('<I', entry, 116)[0]
            size = struct.unpack_from('<Q', entry, 120)[0]
            if self.sector_size == 512:
                size &= 0xFFFFFFFF
            self.entries.append((name, entry[66], start, size))
        root = self.entries[0] if self.entries else ('', 5, ENDOFCHAIN, 0)
        self.ministream = self.chain_bytes(root[2], root[3]) if root[3] else b''
        minifat = self.chain_bytes(minifat_start) if minifat_count else b''
        self.minifat = list(struct.unpack('<%dI' % (len(minifat) // 4), minifat[:len(minifat) // 4 * 4]))
        self.streams = {name: (start, size) for name, kind, start, size in self.entries if kind == 2}

    def offset(self, sector):
        return (sector + 1) * self.sector_size

    def chain(self, start, table=None):
        table = self.fat if table is None else table
        sectors = []
        sector = start
        while sector not in (FREESECT, ENDOFCHAIN) and sector < len(table):
            if len(sectors) > len(table):
                raise LayoutError('sector chain loops')
            sectors.append(sector)
            sector = table[sector]
        return sectors

    def chain_bytes(self, start, size=None):
        out = b''.join(self.data[self.offset(s):self.offset(s) + self.sector_size] for s in self.chain(start))
        return out if size is None else out[:size]

    def read(self, name):
        start, size = self.streams[name]
        if size < self.cutoff:
            chunks = [self.ministream[s * self.mini_size:(s + 1) * self.mini_size] for s in self.chain(start, self.minifat)]
            return b''.join(chunks)[:size]
        return self.chain_bytes(start, size)

    def ranges(self, name):
        """The stream's bytes as [offset, length] runs of the file, or None for a mini stream."""
        start, size = self.streams[name]
        if size < self.cutoff:
            return None
        runs = []
        left = size
        for sector in self.chain(start):
            if left <= 0:
                break
            take = min(self.sector_size, left)
            offset = self.offset(sector)
            if runs and runs[-1][0] + runs[-1][1] == offset:
                runs[-1][1] += take
            else:
                runs.append([offset, take])
            left -= take
        return runs


# ---------------------------------------------------------------- MSI tables

_MIME = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._'


def decode_stream_name(name):
    out = []
    for ch in name:
        code = ord(ch)
        if 0x3800 <= code < 0x4840:
            if code >= 0x4800:
                out.append(_MIME[code - 0x4800])
            else:
                code -= 0x3800
                out.append(_MIME[code & 0x3F])
                out.append(_MIME[(code >> 6) & 0x3F])
        else:
            out.append(ch)
    return ''.join(out)


def encode_stream_name(name, table=False):
    out = ['\u4840'] if table else []
    i = 0
    while i < len(name):
        a = _MIME.find(name[i])
        if a < 0:
            out.append(name[i])
            i += 1
            continue
        b = _MIME.find(name[i + 1]) if i + 1 < len(name) else -1
        if b >= 0:
            out.append(chr(0x3800 + a + (b << 6)))
            i += 2
        else:
            out.append(chr(0x4800 + a))
            i += 1
    return ''.join(out)


def _codec(codepage):
    if codepage in (0, 1252):
        return 'cp1252'
    if codepage == 65001:
        return 'utf-8'
    try:
        codecs.lookup('cp%d' % codepage)
        return 'cp%d' % codepage
    except LookupError:
        return 'latin-1'


class Msi:
    TABLE = '\u4840'

    def __init__(self, data):
        self.cf = CompoundFile(data)
        self.names = {decode_stream_name(n): n for n in self.cf.streams}
        pool = self.raw('_StringPool')
        text = self.raw('_StringData')
        if len(pool) < 4:
            raise LayoutError('no string pool')
        codepage, flags = struct.unpack_from('<HH', pool, 0)
        self.ref_size = 3 if flags & 0x8000 else 2
        codec = _codec(codepage)
        words = struct.unpack('<%dH' % (len(pool) // 2), pool[:len(pool) // 2 * 2])
        self.strings = [None]
        count = len(pool) // 4
        i, offset = 1, 0
        while i < count:
            length, refs = words[i * 2], words[i * 2 + 1]
            if length == 0 and refs == 0:
                self.strings.append('')
                i += 1
                continue
            if length == 0:
                if i * 2 + 3 >= len(words):
                    break
                length = (words[i * 2 + 3] << 16) + words[i * 2 + 2]
                i += 2
            else:
                i += 1
            self.strings.append(text[offset:offset + length].decode(codec, 'replace'))
            offset += length
        self._tables = {}
        self.columns = {}
        for row in self._read('_Columns', [('Table', 0x0D48), ('Number', 0x0502), ('Name', 0x0D48), ('Type', 0x0502)]):
            self.columns.setdefault(row['Table'], []).append((row['Number'], row['Name'], row['Type']))
        self.table_names = {row['Name'] for row in self._read('_Tables', [('Name', 0x0D48)])}

    def raw(self, table):
        name = self.names.get(self.TABLE + table)
        return self.cf.read(name) if name is not None else b''

    def stream(self, name):
        return self.names.get(name)

    def _size(self, kind):
        if (kind & ~MSITYPE_NULLABLE) == (MSITYPE_STRING | MSITYPE_VALID):
            return 2
        if kind & MSITYPE_STRING:
            return self.ref_size
        return 2 if (kind & 0xFF) <= 2 else 4

    def _value(self, kind, raw, size):
        if (kind & ~MSITYPE_NULLABLE) == (MSITYPE_STRING | MSITYPE_VALID):
            return raw
        if kind & MSITYPE_STRING:
            return self.strings[raw] if 0 < raw < len(self.strings) else ''
        if raw == 0:
            return None
        return raw - 0x8000 if size == 2 else raw - 0x80000000

    def _read(self, name, columns):
        data = self.raw(name)
        sizes = [self._size(kind) for _, kind in columns]
        row_size = sum(sizes)
        if not data or not row_size:
            return []
        count = len(data) // row_size
        rows = [dict() for _ in range(count)]
        offset = 0
        for (column, kind), size in zip(columns, sizes):
            for r in range(count):
                start = offset + r * size
                rows[r][column] = self._value(kind, int.from_bytes(data[start:start + size], 'little'), size)
            offset += count * size
        return rows

    def table(self, name):
        if name not in self._tables:
            cols = [(col, kind) for _, col, kind in sorted(self.columns.get(name, []))
                    if not kind & MSITYPE_TEMPORARY]
            self._tables[name] = self._read(name, cols) if cols else []
        return self._tables[name]

    def summary(self):
        key = self.names.get('\x05SummaryInformation')
        if key is None:
            return {}
        data = self.cf.read(key)
        props = {}
        try:
            section = struct.unpack_from('<I', data, 44)[0]
            _, count = struct.unpack_from('<II', data, section)
            for i in range(count):
                pid, poff = struct.unpack_from('<II', data, section + 8 + i * 8)
                at = section + poff
                kind = struct.unpack_from('<I', data, at)[0]
                if kind == 30:
                    n = struct.unpack_from('<I', data, at + 4)[0]
                    props[pid] = data[at + 8:at + 8 + n].split(b'\0')[0].decode('latin-1')
                elif kind == 2:
                    props[pid] = struct.unpack_from('<h', data, at + 4)[0]
                elif kind == 3:
                    props[pid] = struct.unpack_from('<i', data, at + 4)[0]
        except struct.error:
            pass
        return props


# ---------------------------------------------------------------- conditions

_TOKEN = re.compile(r'\s*(?:(?P<str>"[^"]*")|(?P<num>-?\d+)(?![\w.])|(?P<op>~?(?:<>|<=|>=|><|<<|>>|=|<|>))'
                    r'|(?P<paren>[()])|(?P<ident>[%&!$?]?[A-Za-z_][\w.]*))', re.S)
_KEYWORDS = {'NOT', 'AND', 'OR', 'XOR', 'EQV', 'IMP'}


class Conditions:
    def __init__(self, props, feature_state, component_state):
        self.props = props
        self.feature_state = feature_state
        self.component_state = component_state
        self.tokens = []
        self.pos = 0

    def evaluate(self, text, default=True):
        if text is None or not text.strip():
            return default
        try:
            self.tokens = self._tokenize(text)
            self.pos = 0
            value = self._expr()
            if self.pos != len(self.tokens):
                return default
            return self._truth(value)
        except (LayoutError, IndexError, ValueError, TypeError):
            return default

    @staticmethod
    def _tokenize(text):
        tokens = []
        pos = 0
        while pos < len(text):
            if not text[pos:].strip():
                break
            m = _TOKEN.match(text, pos)
            if not m or m.end() == pos:
                raise LayoutError('bad condition')
            pos = m.end()
            kind = m.lastgroup
            value = m.group(kind)
            if kind == 'ident' and value.upper() in _KEYWORDS:
                tokens.append(('kw', value.upper()))
            else:
                tokens.append((kind, value))
        return tokens

    def _peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def _binary(self, keyword, nxt, combine):
        left = nxt()
        while self._peek() == ('kw', keyword):
            self.pos += 1
            right = nxt()
            left = combine(self._truth(left), self._truth(right))
        return left

    def _expr(self):
        return self._binary('IMP', self._eqv, lambda a, b: (not a) or b)

    def _eqv(self):
        return self._binary('EQV', self._xor, lambda a, b: a == b)

    def _xor(self):
        return self._binary('XOR', self._or, lambda a, b: a != b)

    def _or(self):
        return self._binary('OR', self._and, lambda a, b: a or b)

    def _and(self):
        return self._binary('AND', self._not, lambda a, b: a and b)

    def _not(self):
        if self._peek() == ('kw', 'NOT'):
            self.pos += 1
            return not self._truth(self._not())
        return self._compare()

    def _compare(self):
        left = self._value()
        kind, op = self._peek()
        if kind != 'op':
            return left
        self.pos += 1
        right = self._value()
        fold = op.startswith('~')
        op = op.lstrip('~')
        a, b = left, right
        if isinstance(a, int) != isinstance(b, int):
            try:
                a, b = int(a), int(b)
            except (TypeError, ValueError):
                return op == '<>'
        if fold and isinstance(a, str):
            a, b = a.lower(), b.lower()
        if op == '><':
            return isinstance(a, str) and b in a
        if op == '<<':
            return isinstance(a, str) and a.startswith(b)
        if op == '>>':
            return isinstance(a, str) and a.endswith(b)
        return {'=': a == b, '<>': a != b, '<': a < b, '>': a > b, '<=': a <= b, '>=': a >= b}[op]

    def _value(self):
        kind, value = self._peek()
        self.pos += 1
        if kind == 'paren' and value == '(':
            inner = self._expr()
            if self._peek() != ('paren', ')'):
                raise LayoutError('unbalanced condition')
            self.pos += 1
            return inner
        if kind == 'str':
            return value[1:-1]
        if kind == 'num':
            return int(value)
        if kind == 'ident':
            if value[0] in '&!':
                return self.feature_state(value[1:])
            if value[0] in '$?':
                return self.component_state(value[1:])
            if value[0] == '%':
                return ''
            raw = self.props.get(value, '')
            return '' if raw is None else raw
        raise LayoutError('bad condition value')

    @staticmethod
    def _truth(value):
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        return bool(value)


# ---------------------------------------------------------------- one package

def long_name(value):
    value = (value or '').split(':')[0]
    return value.split('|', 1)[1] if '|' in value else value


def to_int(value, default=0):
    if value is None:
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except ValueError:
        return default


def win_to_rel(path):
    path = path.replace('/', '\\').rstrip('\\')
    if len(path) >= 2 and path[1] == ':':
        if path[0].upper() != 'C':
            return None
        path = path[2:]
    parts = [p for p in path.split('\\') if p not in ('', '.')]
    if any(p == '..' for p in parts):
        return None
    return '/'.join(['drive_c'] + parts)


def rel_to_win(rel, folder=True):
    if not rel or not rel.startswith('drive_c'):
        return ''
    text = 'C:\\' + rel[len('drive_c'):].strip('/').replace('/', '\\')
    if folder and not text.endswith('\\'):
        text += '\\'
    return text


class Package:
    """Where one MSI puts its files and which registry values it writes, as Wine's msi would."""

    def __init__(self, msi, properties=None):
        self.msi = msi
        self.notes = []
        self.summary = msi.summary()
        self.platform = str(self.summary.get(7, '')).split(';')[0].strip().lower()
        self.props = dict(SYSTEM_PROPERTIES)
        self.props.update(properties or {})
        if self.platform in ('', 'intel'):
            self.props['Intel'] = '6'
        self.props.update(KNOWN_FOLDERS)
        for row in msi.table('Property'):
            if row['Property'] not in self.props or row['Property'] in ('INSTALLLEVEL', 'ALLUSERS', 'UILevel'):
                self.props[row['Property']] = row['Value']
        self.props['ALLUSERS'] = '1'
        self.directories = {row['Directory']: row for row in msi.table('Directory')}
        self.components = {row['Component']: row for row in msi.table('Component')}
        self.features = set()
        self.included = set()
        self.set_by_action = set()
        self.resolving = set()
        self.files = {}
        self.conditions = Conditions(self.props, self.feature_state, self.component_state)

    def feature_state(self, name):
        return 3 if name in self.features else -1

    def component_state(self, name):
        return 3 if name in self.included else -1

    def directory(self, key):
        if key in self.resolving or key in SKIPPED_FOLDERS:
            return None
        row = self.directories.get(key)
        if key in KNOWN_FOLDERS or key in self.set_by_action or row is None:
            value = self.props.get(key)
            return win_to_rel(value) if value else None
        parent = row.get('Directory_Parent') or ''
        default = long_name(row.get('DefaultDir'))
        if not parent or parent == key:
            return 'drive_c'
        self.resolving.add(key)
        try:
            base = self.directory(parent)
        finally:
            self.resolving.discard(key)
        if base is None:
            return None
        if default in ('.', ''):
            return base
        if '\\' in default or '/' in default or default == '..':
            return None
        return base + '/' + default

    def format(self, text):
        if not text:
            return text or ''

        def one(match):
            inner = match.group(1)
            if inner.startswith('\\') and len(inner) == 2:
                return inner[1]
            if inner == '~':
                return '\0'
            if inner[:1] in ('#', '!'):
                rel = self.files.get(inner[1:])
                return rel_to_win(rel, folder=False) if rel else ''
            if inner[:1] == '$':
                comp = self.components.get(inner[1:])
                rel = self.directory(comp['Directory_']) if comp else None
                return rel_to_win(rel) if rel else ''
            if inner[:1] == '%':
                return ''
            if inner in self.directories or inner in KNOWN_FOLDERS:
                rel = self.directory(inner)
                return rel_to_win(rel) if rel else ''
            return self.props.get(inner, '') or ''

        previous = None
        for _ in range(8):
            if text == previous:
                break
            previous = text
            text = re.sub(r'\[([^\[\]]*)\]', one, text)
        return text

    def run_set_actions(self):
        actions = {row['Action']: row for row in self.msi.table('CustomAction')}
        order = {}
        for table in ('InstallUISequence', 'InstallExecuteSequence'):
            for row in self.msi.table(table):
                if row['Action'] in actions and row['Action'] not in order:
                    order[row['Action']] = (to_int(row.get('Sequence')), row.get('Condition') or '')
        skipped = []
        for name, (_, condition) in sorted(order.items(), key=lambda item: item[1][0]):
            row = actions[name]
            kind = to_int(row.get('Type')) & 0x3F
            if kind in (SET_PROPERTY, SET_DIRECTORY):
                if self.conditions.evaluate(condition, default=True):
                    self.props[row['Source']] = self.format(row.get('Target') or '')
                    self.set_by_action.add(row['Source'])
            elif kind != ERROR_ACTION and self.conditions.evaluate(condition, default=True):
                skipped.append(name)
        if skipped:
            self.notes.append('custom actions not run: ' + ', '.join(sorted(skipped)[:12]))

    def select(self):
        level = to_int(self.props.get('INSTALLLEVEL'), 1) or 1
        features = {row['Feature']: dict(row) for row in self.msi.table('Feature')}
        for row in self.msi.table('Condition'):
            feature = features.get(row['Feature_'])
            if feature is not None and self.conditions.evaluate(row.get('Condition'), default=False):
                feature['Level'] = row['Level']

        def included(name, depth=0):
            feature = features.get(name)
            if feature is None or depth > 32:
                return False
            lvl = to_int(feature.get('Level'))
            if lvl <= 0 or lvl > level:
                return False
            parent = feature.get('Feature_Parent') or ''
            return not parent or parent == name or included(parent, depth + 1)

        self.features = {name for name in features if included(name)}
        owners = {}
        for row in self.msi.table('FeatureComponents'):
            owners.setdefault(row['Component_'], set()).add(row['Feature_'])
        for name, row in self.components.items():
            mine = owners.get(name)
            if mine is not None and not mine & self.features:
                continue
            if self.conditions.evaluate(row.get('Condition'), default=True):
                self.included.add(name)

    def product_major(self):
        return to_int(str(self.props.get('ProductVersion') or '0').split('.')[0])

    def assembly_targets(self):
        idents = {}
        for row in self.msi.table('MsiAssemblyName'):
            idents.setdefault(row['Component_'], {})[(row['Name'] or '').lower()] = row['Value'] or ''
        placed = {}
        for row in self.msi.table('MsiAssembly'):
            comp = row['Component_']
            if comp in self.included and not row.get('File_Application'):
                placed[comp] = (to_int(row.get('Attributes')), idents.get(comp, {}), row.get('File_Manifest') or '')
        return placed

    def gac_folder(self, ident):
        name = ident.get('name', '')
        token = ident.get('publickeytoken', '').lower()
        if not name or not token or '/' in name or '\\' in name:
            return None
        version = ident.get('version', '0.0.0.0')
        culture = ident.get('culture', 'neutral')
        culture = '' if culture.lower() == 'neutral' else culture
        arch = ident.get('processorarchitecture', '').lower()
        gac = {'x86': 'GAC_32', 'amd64': 'GAC_64', 'x64': 'GAC_64', 'msil': 'GAC_MSIL'}.get(arch, 'GAC_MSIL' if arch else 'GAC')
        if self.product_major() >= 4:
            return 'drive_c/windows/Microsoft.NET/assembly/%s/%s/v4.0_%s_%s_%s' % (gac, name, version, culture, token)
        return 'drive_c/windows/assembly/%s/%s/%s_%s_%s' % (gac, name, version, culture, token)

    @staticmethod
    def sxs_names(ident):
        arch = (ident.get('processorarchitecture') or 'x86').lower()
        name = ident.get('name', '').lower()
        token = ident.get('publickeytoken', '').lower()
        version = ident.get('version', '')
        if not name or not token or not version or '/' in name or '\\' in name:
            return None
        if ident.get('type', '').lower() == 'win32-policy':
            target = re.sub(r'^policy\.\d+\.\d+\.', '', name)
            return 'policy', 'drive_c/windows/winsxs/policies/%s_%s_%s_none_deadbeef' % (arch, target, token), version
        return 'assembly', '%s_%s_%s_%s_none_deadbeef' % (arch, name, token, version), version

    def destinations(self):
        assemblies = self.assembly_targets()
        out = {}
        for row in self.msi.table('File'):
            comp = row['Component_']
            if comp not in self.included:
                continue
            name = long_name(row['FileName'])
            if not name or '/' in name or '\\' in name or name in ('.', '..'):
                continue
            key = row['File']
            if comp in assemblies:
                kind, ident, manifest = assemblies[comp]
                if kind == 0:
                    folder = self.gac_folder(ident)
                    out[key] = folder + '/' + name if folder else None
                    continue
                names = self.sxs_names(ident)
                if names is None:
                    out[key] = None
                elif names[0] == 'policy':
                    out[key] = names[1] + '/' + names[2] + '.policy' if key == manifest else None
                elif key == manifest:
                    out[key] = 'drive_c/windows/winsxs/manifests/%s.manifest' % names[1]
                elif name.lower().endswith('.cat'):
                    out[key] = None
                else:
                    out[key] = 'drive_c/windows/winsxs/%s/%s' % (names[1], name)
                continue
            folder = self.directory(self.components[comp]['Directory_'])
            out[key] = folder + '/' + name if folder else None
        for key, rel in out.items():
            if rel:
                self.files[key] = rel
        return out

    def duplicates(self):
        out = []
        for row in self.msi.table('DuplicateFile'):
            if row['Component_'] not in self.included or row['File_'] not in self.files:
                continue
            source = self.files[row['File_']]
            folder = self.directory(row['DestFolder']) if row.get('DestFolder') else source.rsplit('/', 1)[0]
            name = long_name(row.get('DestName')) or source.rsplit('/', 1)[1]
            if folder and '/' not in name:
                out.append((row['File_'], folder + '/' + name))
        return out

    def registry_value(self, raw):
        if raw is None or raw == '':
            return 'REG_SZ', ''
        if raw[:2] in ('#x', '#X'):
            digits = re.sub(r'[^0-9A-Fa-f]', '', self.format(raw[2:]))
            return 'REG_BINARY', ('0' + digits if len(digits) % 2 else digits).lower()
        if raw.startswith('#%'):
            return 'REG_EXPAND_SZ', self.format(raw[2:])
        if raw.startswith('##'):
            return 'REG_SZ', self.format(raw[1:])
        if raw.startswith('#'):
            number = self.format(raw[1:]).strip()
            if re.fullmatch(r'[+-]?\d+', number):
                return 'REG_DWORD', str(int(number) & 0xFFFFFFFF)
            return 'REG_SZ', number
        text = self.format(raw)
        if '\0' in text:
            return 'REG_MULTI_SZ', [part for part in text.split('\0') if part]
        return 'REG_SZ', text

    def registry(self):
        out = []
        for row in self.msi.table('Registry'):
            comp = row['Component_']
            if comp not in self.included:
                continue
            hive, prefix = {-1: ('HKLM', ''), 0: ('HKLM', 'Software\\Classes\\'), 1: ('HKCU', ''),
                            2: ('HKLM', '')}.get(to_int(row.get('Root'), 2), (None, None))
            if hive is None:
                continue
            key = prefix + self.format(row.get('Key')).strip('\\')
            wow64 = not to_int(self.components[comp].get('Attributes')) & COMPONENT_64BIT
            if hive == 'HKLM' and wow64 and key.lower().startswith('software\\') \
                    and not key.lower().startswith('software\\wow6432node\\'):
                key = 'Software\\Wow6432Node\\' + key[len('software\\'):]
            name = row.get('Name') or ''
            raw = row.get('Value')
            if (name in ('+', '-', '*') and not raw) or (not name and not raw):
                if name != '-':
                    out.append({'hive': hive, 'key': key, 'type': 'KEY'})
                continue
            kind, data = self.registry_value(raw)
            out.append({'hive': hive, 'key': key, 'name': self.format(name), 'type': kind, 'value': data})
        for row in self.msi.table('Environment'):
            if row['Component_'] not in self.included:
                continue
            flags = re.match(r'^[=+\-!*]*', row['Name']).group(0)
            name = row['Name'][len(flags):]
            raw = row.get('Value') or ''
            if '!' in flags or '-' in flags or not name or '[~]' in raw:
                continue
            hive, key = ('HKLM', 'System\\CurrentControlSet\\Control\\Session Manager\\Environment') if '*' in flags \
                else ('HKCU', 'Environment')
            out.append({'hive': hive, 'key': key, 'name': name, 'type': 'REG_SZ', 'value': self.format(raw)})
        return out

    def check(self):
        for name in UNSUPPORTED_TABLES:
            if name in self.msi.table_names and self.msi.table(name):
                self.notes.append('%d %s rows not applied' % (len(self.msi.table(name)), name))

    def media_for(self, sequence):
        best = None
        for row in self.msi.table('Media'):
            last = to_int(row.get('LastSequence'))
            if last >= sequence and (best is None or last < to_int(best.get('LastSequence'))):
                best = row
        return best

    def prepare(self):
        self.run_set_actions()
        self.select()
        self.check()


# ---------------------------------------------------------------- cabinets and installers

def sniff(data):
    if data[:8] == MSI_MAGIC:
        return 'msi'
    if data[:4] == b'PK\x03\x04':
        return 'zip'
    if data[:8] == CAB_MAGIC:
        return 'cab'
    if data[:2] == b'MZ':
        return 'exe'
    if data[:6] == b"7z\xbc\xaf'\x1c":
        return '7z'
    return 'other'


def cab_members(data, offset=0):
    """(name, uncompressed size) of every member of the cabinet at [offset]."""
    if data[offset:offset + 8] != CAB_MAGIC:
        raise LayoutError('not a cabinet')
    files_at, = struct.unpack_from('<I', data, offset + 16)
    _, count, _ = struct.unpack_from('<HHH', data, offset + 26)
    out = []
    at = offset + files_at
    for _ in range(count):
        size, _, _, _, _, attribs = struct.unpack_from('<IIHHHH', data, at)
        end = data.index(b'\0', at + 16)
        name = data[at + 16:end].decode('utf-8' if attribs & 0x80 else 'latin-1', 'replace').replace('\\', '/')
        out.append((name, size))
        at = end + 1
    return out


def embedded_cabinets(data, start=0):
    found = []
    position = start
    while True:
        at = data.find(CAB_MAGIC, position)
        if at < 0 or at + 36 > len(data):
            return found
        size, = struct.unpack_from('<I', data, at + 8)
        if data[at + 24] == 3 and data[at + 25] == 1 and size >= 36 and at + size <= len(data):
            found.append((at, size))
            position = at + size
        else:
            position = at + 4


def burn_containers(data):
    try:
        pe, = struct.unpack_from('<I', data, 0x3C)
        if data[pe:pe + 4] != b'PE\0\0':
            return None
        count, = struct.unpack_from('<H', data, pe + 6)
        optional, = struct.unpack_from('<H', data, pe + 20)
        table = pe + 24 + optional
        section = None
        for i in range(count):
            name, _, _, raw_size, raw_offset = struct.unpack_from('<8sIIII', data, table + 40 * i)
            if name.rstrip(b'\0') == b'.wixburn':
                section = (raw_offset, raw_size)
        if section is None or section[1] < 52:
            return None
        base = section[0]
        if struct.unpack_from('<I', data, base)[0] != BURN_MAGIC:
            return None
        stub, = struct.unpack_from('<I', data, base + 24)
        containers, = struct.unpack_from('<I', data, base + 44)
        sizes = struct.unpack_from('<%dI' % containers, data, base + 48)
    except (struct.error, IndexError):
        return None
    found = []
    position = stub
    for size in sizes:
        while True:
            at = data.find(b'MSCF', position)
            if at < 0 or at + 12 > len(data):
                return None
            if struct.unpack_from('<I', data, at + 8)[0] == size:
                break
            position = at + 4
        found.append((at, size))
        position = at + size
    return found


def _python_cab_extract(data, dest):
    folders, count, flags = struct.unpack_from('<HHH', data, 26)
    at = 36
    reserve_folder = reserve_data = 0
    if flags & 4:
        header_reserve, reserve_folder, reserve_data = struct.unpack_from('<HBB', data, at)
        at += 4 + header_reserve
    if flags & 1:
        at = data.index(b'\0', data.index(b'\0', at) + 1) + 1
    if flags & 2:
        at = data.index(b'\0', data.index(b'\0', at) + 1) + 1
    streams = []
    for _ in range(folders):
        first, blocks, compression = struct.unpack_from('<IHH', data, at)
        at += 8 + reserve_folder
        out = bytearray()
        position = first
        for _ in range(blocks):
            _, packed, _ = struct.unpack_from('<IHH', data, position)
            payload = data[position + 8 + reserve_data:position + 8 + reserve_data + packed]
            position += 8 + reserve_data + packed
            if compression & 0xF == 0:
                out += payload
            elif compression & 0xF == 1:
                if payload[:2] != b'CK':
                    raise LayoutError('bad MSZIP block')
                window = bytes(out[-32768:])
                inflater = zlib.decompressobj(-15, zdict=window) if window else zlib.decompressobj(-15)
                out += inflater.decompress(payload[2:]) + inflater.flush()
            else:
                raise LayoutError('an LZX or Quantum cabinet needs bsdtar, cabextract or 7z')
        streams.append(bytes(out))
    files_at, = struct.unpack_from('<I', data, 16)
    at = files_at
    for _ in range(count):
        size, offset, folder, _, _, attribs = struct.unpack_from('<IIHHHH', data, at)
        end = data.index(b'\0', at + 16)
        name = data[at + 16:end].decode('utf-8' if attribs & 0x80 else 'latin-1', 'replace')
        at = end + 1
        target = _safe_join(dest, name)
        if target is None or folder >= len(streams):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'wb') as out:
            out.write(streams[folder][offset:offset + size])


def _safe_join(root, name):
    parts = [p for p in name.replace('\\', '/').split('/') if p not in ('', '.')]
    if not parts or '..' in parts:
        return None
    return os.path.join(root, *parts)


def _tool(*names):
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def _has_files(root):
    return any(files for _, _, files in os.walk(root))


def device_readers():
    """The unpackers whose success means libarchive on the device can open the archive too."""
    return ('zip', 'bsdtar', 'python') if _tool('bsdtar') else ('zip', 'bsdtar', 'python', 'cabextract')


def extract_archive(path, dest, tools=True, sevenzip=True):
    """Unpacks a cabinet, zip or 7z archive into [dest]; returns the unpacker that worked, or None."""
    os.makedirs(dest, exist_ok=True)
    with open(path, 'rb') as f:
        head = f.read(8)
    if head[:4] == b'PK\x03\x04':
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                target = _safe_join(dest, info.filename)
                if target is None or info.is_dir():
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with archive.open(info) as source, open(target, 'wb') as out:
                    shutil.copyfileobj(source, out)
        return 'zip'
    commands = []
    if tools:
        if _tool('bsdtar'):
            commands.append(('bsdtar', [_tool('bsdtar'), '-xf', path, '-C', dest]))
        if head == CAB_MAGIC and _tool('cabextract'):
            commands.append(('cabextract', [_tool('cabextract'), '-q', '-d', dest, path]))
        if sevenzip and _tool('7z', '7zz', '7za'):
            commands.append(('7z', [_tool('7z', '7zz', '7za'), 'x', '-y', '-bd', '-bso0', '-bsp0', '-o' + dest, path]))
    for name, command in commands:
        result = subprocess.run(command, capture_output=True, timeout=1800, check=False)
        if result.returncode == 0 and _has_files(dest):
            return name
        shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(dest, exist_ok=True)
    if head == CAB_MAGIC:
        with open(path, 'rb') as f:
            _python_cab_extract(f.read(), dest)
        return 'python' if _has_files(dest) else None
    return None


def burn_names(manifest):
    names = {}
    for tag in re.findall(r'<Payload\b[^>]*>', manifest):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', tag))
        if attrs.get('SourcePath') and attrs.get('FilePath'):
            names[attrs['SourcePath']] = attrs['FilePath'].replace('\\', '/')
    return names


class Node:
    def __init__(self, path, chain, logical, parent, device=True):
        self.path = path
        self.chain = chain
        self.logical = logical
        self.parent = parent
        self.device = device
        self.opened = None


def _walk(root):
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(base, name)
            yield full, os.path.relpath(full, root).replace(os.sep, '/')


def _sliced(data, start, size, work, tag):
    path = os.path.join(work, '%s-%d.cab' % (tag, start))
    with open(path, 'wb') as f:
        f.write(data[start:start + size])
    return path


def unpack(path, chain, work, depth=0, parent=None, out=None):
    """Every file inside the installer at [path], each with the hops the app takes to reach it."""
    out = [] if out is None else out
    with open(path, 'rb') as f:
        data = f.read()
    kind = sniff(data)
    if kind == 'msi' or depth > MAX_DEPTH:
        return out
    groups = []
    readable = device_readers()
    parent_ok = parent is None or parent.device
    if kind in ('zip', 'cab', '7z'):
        dest = tempfile.mkdtemp(dir=work)
        tool = extract_archive(path, dest)
        if tool:
            groups.append((dest, [], {}, tool))
    elif kind == 'exe':
        containers = burn_containers(data)
        if containers:
            names = {}
            for index, (start, size) in enumerate(containers):
                dest = tempfile.mkdtemp(dir=work)
                tool = extract_archive(_sliced(data, start, size, work, 'burn%d' % depth), dest)
                if not tool:
                    continue
                if index == 0:
                    manifest = os.path.join(dest, '0')
                    if os.path.isfile(manifest):
                        with open(manifest, encoding='utf-8', errors='replace') as f:
                            names = burn_names(f.read())
                    continue
                groups.append((dest, [{'slice': [start, size]}], names, tool))
        else:
            cabinets = embedded_cabinets(data)
            for start, size in cabinets:
                dest = tempfile.mkdtemp(dir=work)
                tool = extract_archive(_sliced(data, start, size, work, 'sfx%d' % depth), dest)
                if tool:
                    groups.append((dest, [{'slice': [start, size]}], {}, tool))
            if not cabinets:
                dest = tempfile.mkdtemp(dir=work)
                tool = extract_archive(path, dest, sevenzip=depth == 0)
                if tool:
                    groups.append((dest, [], {}, tool))
    if parent is not None and groups:
        parent.opened = groups[0][3]
    for dest, prefix, names, tool in groups:
        for full, rel in _walk(dest):
            node = Node(full, chain + prefix + [rel], names.get(rel, rel), parent, parent_ok and tool in readable)
            out.append(node)
            with open(full, 'rb') as f:
                head = f.read(8)
            if sniff(head) in ('zip', 'cab', 'exe', '7z'):
                unpack(full, node.chain, work, depth + 1, node, out)
    return out


# ---------------------------------------------------------------- one verb

def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def download(url, sha256, cache, max_bytes=MAX_DOWNLOAD_BYTES):
    os.makedirs(cache, exist_ok=True)
    target = os.path.join(cache, sha256)
    if os.path.isfile(target) and file_sha256(target) == sha256:
        return target
    partial = target + '.part'
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'})
    total = 0
    with urllib.request.urlopen(request, timeout=120) as response, open(partial, 'wb') as out:
        while True:
            block = response.read(1 << 20)
            if not block:
                break
            total += len(block)
            if total > max_bytes:
                raise LayoutError('%s is larger than %d bytes' % (url, max_bytes))
            out.write(block)
    actual = file_sha256(partial)
    if actual != sha256:
        os.remove(partial)
        raise LayoutError('%s has sha256 %s, expected %s' % (url, actual, sha256))
    os.replace(partial, target)
    return target


def _is_msi(path):
    with open(path, 'rb') as f:
        return f.read(8) == MSI_MAGIC


def choose_packages(nodes, patterns):
    msis = [n for n in nodes if _is_msi(n.path)]
    if patterns:
        return [n for n in msis if any(fnmatch.fnmatch(n.logical.lower(), p.lower()) for p in patterns)]
    if not msis:
        return []
    depth = min(len(n.chain) for n in msis)
    top = [n for n in msis if len(n.chain) == depth]
    lower = [n.logical.lower() for n in top]
    if any('full' in name for name in lower) and any('_core' in name or '_extended' in name for name in lower):
        top = [n for n in top if '_core' not in n.logical.lower() and '_extended' not in n.logical.lower()]
    return top


class LayoutBuilder:
    def __init__(self, verb, keep, properties=None):
        self.verb = verb
        self.keep = keep
        self.properties = properties
        self.archives = []
        self.files = []
        self.registry = []
        self.notes = []
        self.targets = set()
        self.unpacked = 0
        self.undecodable = 0
        self.missing = 0

    def archive_id(self, installer, hops):
        entry = {'installer': installer, 'hops': hops}
        if entry not in self.archives:
            self.archives.append(entry)
        return self.archives.index(entry)

    def add_file(self, archive, member, rel, size):
        if rel.lower() in self.targets or not self.keep(rel):
            return
        self.targets.add(rel.lower())
        self.unpacked += size
        self.files.append({'archive': archive, 'member': member, 'to': rel, 'size': size})

    def add_package(self, installer, node, nodes):
        with open(node.path, 'rb') as f:
            msi = Msi(f.read())
        package = Package(msi, self.properties)
        if package.platform in SKIP_PLATFORMS:
            return False
        package.prepare()
        destinations = package.destinations()
        cabinets = {}
        missing = 0
        placed = {}
        for row in msi.table('File'):
            key = row['File']
            rel = destinations.get(key)
            if not rel:
                continue
            media = package.media_for(to_int(row.get('Sequence')))
            cabinet = (media or {}).get('Cabinet') or ''
            if not cabinet or to_int(row.get('Attributes')) & FILE_NONCOMPRESSED:
                missing += 1
                continue
            if cabinet not in cabinets:
                cabinets[cabinet] = self.cabinet_source(installer, node, msi, cabinet, nodes)
            source = cabinets[cabinet]
            if source == 'undecodable':
                self.undecodable += 1
                continue
            if source is None or key not in source[1]:
                missing += 1
                continue
            placed[key] = (source[0], source[1][key])
            self.add_file(source[0], key, rel, source[1][key])
        for key, rel in package.duplicates():
            if key in placed:
                self.add_file(placed[key][0], key, rel, placed[key][1])
        if missing:
            self.missing += missing
            self.notes.append('%s: %d file(s) are not in its cabinets' % (node.logical, missing))
        self.registry.extend(package.registry())
        self.notes.extend('%s: %s' % (node.logical, note) for note in package.notes)
        return True

    def cabinet_source(self, installer, node, msi, cabinet, nodes):
        if not node.device:
            return 'undecodable'
        if cabinet.startswith('#'):
            stream = msi.stream(cabinet[1:])
            if stream is None:
                return None
            data = msi.cf.read(stream)
            ranges = msi.cf.ranges(stream)
            if ranges is None or data[:8] != CAB_MAGIC:
                return None
            if not libarchive_reads(data):
                return 'undecodable'
            hops = node.chain + [{'ranges': ranges, 'size': len(data)}]
        else:
            folder = posixpath.dirname(node.logical)
            wanted = (posixpath.join(folder, cabinet) if folder else cabinet).lower()
            siblings = [n for n in nodes if n.parent is node.parent and n.chain[:-1] == node.chain[:-1]]
            match = [n for n in siblings if n.logical.lower() == wanted] or \
                [n for n in siblings if posixpath.basename(n.logical).lower() == cabinet.lower()]
            if not match:
                return None
            with open(match[0].path, 'rb') as f:
                data = f.read()
            if data[:8] != CAB_MAGIC:
                return None
            if not match[0].device or match[0].opened not in device_readers():
                return 'undecodable'
            hops = match[0].chain
        return self.archive_id(installer, hops), dict(cab_members(data))


def libarchive_reads(data):
    """True when bsdtar (libarchive, as on the device) unpacks the cabinet [data]; assumed when it is not installed."""
    if not _tool('bsdtar'):
        return True
    with tempfile.TemporaryDirectory() as work:
        cab = os.path.join(work, 'x.cab')
        with open(cab, 'wb') as f:
            f.write(data)
        out = os.path.join(work, 'out')
        return extract_archive(cab, out, sevenzip=False) == 'bsdtar'


def build_layout(verb, spec, cache, keep=None):
    builder = LayoutBuilder(verb, keep or (lambda rel: True), spec.get('msiProperties'))
    installers = []
    with tempfile.TemporaryDirectory(prefix='verb-%s-' % re.sub(r'[^\w.-]', '_', verb)) as work:
        for index, installer in enumerate(spec.get('installers', [])):
            sha = (installer.get('sha256') or '').lower()
            if len(sha) != 64:
                raise LayoutError('%s has no pinned sha256' % installer.get('url'))
            path = download(installer['url'], sha, cache)
            installers.append({'url': installer['url'], 'sha256': sha, 'size': os.path.getsize(path),
                               'arch': installer.get('arch', 'any')})
            if _is_msi(path):
                nodes = [Node(path, [], posixpath.basename(installer['url'].split('?')[0]), None)]
            else:
                nodes = unpack(path, [], work)
            packages = choose_packages(nodes, spec.get('msi'))
            if not packages:
                raise LayoutError('%s holds no Windows Installer package' % installer['url'])
            for node in packages:
                builder.add_package(index, node, nodes)
    if builder.undecodable:
        raise LayoutError('%d file(s) are in archives libarchive cannot unpack on the device '
                          '(7z LZMA/BCJ2 with a large dictionary, or a cabinet libarchive rejects)' % builder.undecodable)
    if builder.missing:
        raise LayoutError('%d file(s) are not in the package cabinets; a partial layout is not shipped' % builder.missing)
    if not builder.files:
        raise LayoutError('the packages install no files here')
    return {
        'schema': LAYOUT_SCHEMA,
        'verb': verb,
        'installers': installers,
        'archives': builder.archives,
        'files': sorted(builder.files, key=lambda f: f['to'].lower()),
        'registry': builder.registry,
        'unpackedBytes': builder.unpacked,
        'notes': builder.notes,
    }


def dump(obj):
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + '\n'


def offline_specs(verbs_doc):
    return {name: verb for name, verb in sorted(verbs_doc.get('verbs', {}).items())
            if verb.get('class') == 'offline_installer' and verb.get('installers')}


def record_layouts(verbs_doc, out_dir):
    for name, verb in verbs_doc.get('verbs', {}).items():
        path = os.path.join(out_dir, '%s.layout.json' % name)
        if verb.get('class') == 'offline_installer' and os.path.isfile(path):
            verb['layout'] = {'path': 'verbs/%s.layout.json' % name, 'sha256': file_sha256(path)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--verbs', required=True, help='data/verbs.json')
    ap.add_argument('--out', required=True, help='layout directory (data/verbs)')
    ap.add_argument('--cache', required=True, help='download cache directory')
    ap.add_argument('--only', action='append', help='only this verb (repeatable)')
    ap.add_argument('--update-verbs', action='store_true', help='record each layout and its sha256 in verbs.json')
    a = ap.parse_args(argv)
    with open(a.verbs, encoding='utf-8') as f:
        verbs_doc = json.load(f)
    os.makedirs(a.out, exist_ok=True)
    failures = []
    for name, spec in offline_specs(verbs_doc).items():
        if a.only and name not in a.only:
            continue
        try:
            layout = build_layout(name, spec, a.cache)
        except (LayoutError, OSError, ValueError, struct.error, zipfile.BadZipFile) as error:
            failures.append(name)
            print('%s: %s' % (name, error), file=sys.stderr)
            continue
        with open(os.path.join(a.out, '%s.layout.json' % name), 'w', encoding='utf-8', newline='\n') as f:
            f.write(dump(layout))
        print('%s: %d files, %d registry values, %d MB unpacked, %d notes' % (
            name, len(layout['files']), len(layout['registry']), layout['unpackedBytes'] >> 20, len(layout['notes'])))
    if a.update_verbs:
        record_layouts(verbs_doc, a.out)
        with open(a.verbs, 'w', encoding='utf-8', newline='\n') as f:
            f.write(json.dumps(verbs_doc, indent=2, sort_keys=True, ensure_ascii=False) + '\n')
    if failures:
        print('no layout for: ' + ', '.join(failures), file=sys.stderr)
    return 1 if failures and a.only else 0


if __name__ == '__main__':
    sys.exit(main())
