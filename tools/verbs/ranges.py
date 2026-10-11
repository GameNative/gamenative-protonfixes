#!/usr/bin/env python3
"""Byte-range hops for DLLs that winetricks takes from service-pack cabinets.

The Windows 7 SP1 update (537 MB x86, 903 MB x64) and the Windows 2000 SP4 update (129 MB) are
self-extracting cabinets. Each DLL that a verb needs sits in one LZX folder of the outer cabinet.
For each such file this script finds that folder and the data blocks up to the end of the file,
and writes a small cabinet header that describes only those blocks and that file. The app then
downloads only that byte range, puts the header in front of it and unpacks the member from the
result like any other cabinet.

The output is tools/verbs/ranges.json, which tools/protonfixes/verbs.py reads. The service packs
never change (their sha256 is pinned by winetricks), so this runs by hand, not in the weekly sync.
Downloads are untrusted data: nothing in them is executed.
"""

import argparse
import base64
import hashlib
import json
import mmap
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import msi_tables  # noqa: E402

RANGES_PATH = os.path.join(HERE, 'ranges.json')
SCHEMA = 1
VERBS = ('gdiplus', 'usp10', 'oleaut32', 'crypt32', 'wininet', 'urlmon', 'msxml3')
MAX_DOWNLOAD_BYTES = 2 << 30
MAX_SLICE_BYTES = 150 * 1000 * 1000
CFFOLDER_SIZE = 8
CFFILE_SIZE = 16
CFDATA_SIZE = 8
PADDING_NAME = b'padding.bin'


class Cabinet:
    """The headers of the cabinet at [base] in [data]: folders, files and the per-block reserve."""

    def __init__(self, data, base=0):
        if data[base:base + 8] != msi_tables.CAB_MAGIC:
            raise msi_tables.LayoutError('not a cabinet')
        self.data = data
        self.base = base
        self.size, = struct.unpack_from('<I', data, base + 8)
        files_at, = struct.unpack_from('<I', data, base + 16)
        folders, count, self.flags = struct.unpack_from('<HHH', data, base + 26)
        at = base + 36
        folder_reserve = self.data_reserve = 0
        if self.flags & 4:
            header_reserve, folder_reserve, self.data_reserve = struct.unpack_from('<HBB', data, at)
            at += 4 + header_reserve
        if self.flags & 3:
            raise msi_tables.LayoutError('a cabinet set spanning several files')
        self.folders = []
        for _ in range(folders):
            first, blocks, compression = struct.unpack_from('<IHH', data, at)
            self.folders.append((first, blocks, compression))
            at += CFFOLDER_SIZE + folder_reserve
        self.files = []
        at = base + files_at
        for _ in range(count):
            size, offset, folder, date, time, attribs = struct.unpack_from('<IIHHHH', data, at)
            end = data.find(b'\0', at + CFFILE_SIZE)
            raw = bytes(data[at + CFFILE_SIZE:end])
            name = raw.decode('utf-8' if attribs & 0x80 else 'latin-1', 'replace').replace('\\', '/')
            self.files.append({'name': name, 'raw': raw, 'size': size, 'offset': offset, 'folder': folder,
                               'date': date, 'time': time, 'attribs': attribs})
            at = end + 1

    def find(self, member):
        wanted = member.lower()
        matches = [f for f in self.files if f['name'].lower() == wanted]
        if len(matches) != 1:
            raise msi_tables.LayoutError('%s: %d matches in the cabinet' % (member, len(matches)))
        return matches[0]

    def blocks_for(self, member):
        """(absolute start, byte length, block count) of the folder blocks that hold [member] completely."""
        if member['folder'] >= len(self.folders):
            raise msi_tables.LayoutError('%s is in a continued folder' % member['name'])
        first, blocks, _ = self.folders[member['folder']]
        start = self.base + first
        at = start
        unpacked = 0
        need = member['offset'] + member['size']
        for index in range(blocks):
            _, packed, size = struct.unpack_from('<IHH', self.data, at)
            at += CFDATA_SIZE + self.data_reserve + packed
            unpacked += size
            if unpacked >= need:
                return start, at - start, index + 1
        raise msi_tables.LayoutError('%s ends after its folder' % member['name'])

    def folder_bytes(self, member):
        first = self.folders[member['folder']][0]
        later = [f[0] for f in self.folders if f[0] > first]
        return (min(later) if later else self.size) - first


def single_folder_header(cab, member, blocks, length):
    """A cabinet header for one folder of [blocks] data blocks that holds [member]; the data follows it.

    libarchive wants the files of a folder to cover it from offset 0 without gaps, so the bytes before
    [member] are listed as one file named PADDING_NAME.
    """
    reserve = cab.data_reserve > 0
    flags = 4 if reserve else 0
    fixed = 36 + (4 if reserve else 0)
    files_at = fixed + CFFOLDER_SIZE
    entries = []
    if member['offset']:
        entries.append((member['offset'], 0, member['date'], member['time'], 0, PADDING_NAME))
    entries.append((member['size'], member['offset'], member['date'], member['time'], member['attribs'],
                    member['raw']))
    header_size = files_at + sum(CFFILE_SIZE + len(e[5]) + 1 for e in entries)
    compression = cab.folders[member['folder']][2]
    out = struct.pack('<8sIIIIBBHHHHH', msi_tables.CAB_MAGIC, header_size + length, 0, files_at, 0, 3, 1,
                      1, len(entries), flags, 0, 0)
    if reserve:
        out += struct.pack('<HBB', 0, 0, cab.data_reserve)
    out += struct.pack('<IHH', header_size, blocks, compression)
    for size, offset, date, time, attribs, name in entries:
        out += struct.pack('<IIHHHH', size, offset, 0, date, time, attribs) + name + b'\0'
    assert len(out) == header_size
    return out


def outer_cabinet(data, member):
    """The embedded cabinet that lists [member]."""
    if data[:8] == msi_tables.CAB_MAGIC:
        starts = [0]
    else:
        starts = [at for at, _ in msi_tables.embedded_cabinets(data)]
    for start in starts:
        cab = Cabinet(data, start)
        if any(f['name'].lower() == member.lower() for f in cab.files):
            return cab
    raise msi_tables.LayoutError('%s is in none of the embedded cabinets' % member)


def unpack_member(cab_path, member, work, tag):
    """Unpacks [member] (a glob for the only member) with bsdtar and with cabextract; both must agree."""
    results = {}
    for tool in ('bsdtar', 'cabextract'):
        binary = msi_tables._tool(tool)
        if not binary:
            raise msi_tables.LayoutError('%s is not installed' % tool)
        dest = tempfile.mkdtemp(prefix='%s-%s-' % (tag, tool), dir=work)
        if tool == 'bsdtar':
            command = [binary, '-xf', cab_path, '-C', dest]
        else:
            command = [binary, '-q', '-d', dest, cab_path]
        result = subprocess.run(command, capture_output=True, timeout=600, check=False)
        found = [(full, rel) for full, rel in msi_tables._walk(dest)]
        if result.returncode != 0 and not found:
            raise msi_tables.LayoutError('%s cannot unpack %s: %s' % (tool, member, result.stderr[-300:]))
        if member == '*':
            if len(found) != 1:
                raise msi_tables.LayoutError('%s: expected one member, got %d' % (tool, len(found)))
            full, rel = found[0]
        else:
            match = [(f, r) for f, r in found if r.replace('\\', '/').lower() == member.lower()]
            if len(match) != 1:
                raise msi_tables.LayoutError('%s did not unpack %s' % (tool, member))
            full, rel = match[0]
        results[tool] = (full, rel.replace('\\', '/'), msi_tables.file_sha256(full), os.path.getsize(full))
    shas = {r[2] for r in results.values()}
    if len(shas) != 1:
        raise msi_tables.LayoutError('bsdtar and cabextract disagree on %s' % member)
    return results['bsdtar']


def slice_file(url, sha256, chain, cache, work):
    """The range record for the member path [chain] (outer member first) of the download [url]."""
    path = msi_tables.download(url, sha256, cache, MAX_DOWNLOAD_BYTES)
    with open(path, 'rb') as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
        cab = outer_cabinet(data, chain[0])
        member = cab.find(chain[0])
        start, length, blocks = cab.blocks_for(member)
        header = single_folder_header(cab, member, blocks, length)
        piece = bytes(data[start:start + length])
        folder = cab.folder_bytes(member)
    record = {
        'url': url, 'sha256': sha256, 'offset': start, 'length': length,
        'rangeSha256': hashlib.sha256(piece).hexdigest(),
        'header': base64.b64encode(header).decode('ascii'),
        'folderBytes': folder, 'members': [],
    }
    current = os.path.join(work, 'slice-%d.cab' % start)
    with open(current, 'wb') as out:
        out.write(header)
        out.write(piece)
    for depth, name in enumerate(chain):
        full, rel, digest, size = unpack_member(current, name, work, 'hop%d' % depth)
        record['members'].append(rel)
        current = full
    record['extractedSha256'] = digest
    record['extractedSize'] = size
    return record


def file_key(entry):
    return '%s:%s' % (entry['sha256'], '|'.join(list(entry.get('nested') or []) + [entry['entry']]))


def wanted_files(verbs_doc):
    for name in VERBS:
        verb = verbs_doc['verbs'].get(name) or {}
        for entry in verb.get('files', []):
            if entry.get('sha256') and entry.get('entry') and (entry.get('formats') or [None])[0] == 'cab':
                yield name, entry


def build(verbs_doc, cache, previous=None):
    out = {}
    failures = {}
    with tempfile.TemporaryDirectory(prefix='verb-ranges-') as work:
        for verb, entry in wanted_files(verbs_doc):
            key = file_key(entry)
            if key in out or key in failures:
                continue
            chain = list(entry.get('nested') or []) + [entry['entry']]
            try:
                record = slice_file(entry['url'], entry['sha256'], chain, cache, work)
            except (msi_tables.LayoutError, OSError, ValueError, struct.error) as error:
                failures[key] = str(error)
                print('%s %s: %s' % (verb, entry['entry'], error), file=sys.stderr)
                if previous and key in previous:
                    out[key] = previous[key]
                continue
            if record['length'] > MAX_SLICE_BYTES:
                record = {'url': entry['url'], 'sha256': entry['sha256'], 'tooLarge': True,
                          'length': record['length'], 'folderBytes': record['folderBytes']}
            out[key] = record
            print('%s %s: %d MB of %d MB folder' % (verb, chain[-1], record['length'] >> 20,
                                                     record['folderBytes'] >> 20))
    return out, failures


def load(path=None):
    try:
        with open(path or RANGES_PATH, encoding='utf-8') as f:
            return json.load(f).get('files', {})
    except (OSError, ValueError):
        return {}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--verbs', required=True, help='data/verbs.json')
    ap.add_argument('--cache', required=True, help='download cache directory (files named by sha256)')
    ap.add_argument('--out', default=RANGES_PATH)
    a = ap.parse_args(argv)
    with open(a.verbs, encoding='utf-8') as f:
        verbs_doc = json.load(f)
    files, failures = build(verbs_doc, a.cache, load(a.out))
    with open(a.out, 'w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps({'schema': SCHEMA, 'files': files}, indent=1, sort_keys=True) + '\n')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
