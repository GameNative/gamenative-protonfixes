#!/usr/bin/env python3
"""Write data/tools.json: the Termux aarch64 packages of the extractors that layout `tool` hops run.

For each tool this reads the termux-main package index, downloads the .deb (and the .deb of each
library the binary needs that the app's imagefs does not have), checks the index sha256, takes the
binary out of data.tar.xz and reads its NEEDED entries from the ELF dynamic section. Termux keeps
only the current version of a package in its pool, so the URLs stop working after an update; run
this again then. Downloads are untrusted data: nothing in them is executed.
"""

import argparse
import hashlib
import io
import json
import os
import re
import struct
import sys
import tarfile
import urllib.request

SCHEMA = 1
REPO = 'https://packages.termux.dev/apt/termux-main/'
INDEX = REPO + 'dists/stable/main/binary-aarch64/Packages'
PREFIX = 'data/data/com.termux/files/usr/'
TOOLS = {'cabextract': ('cabextract', 'bin/cabextract'), '7zz': ('7zip', 'bin/7zz')}
LIBRARY_PACKAGES = {'libc++_shared.so': 'libc++', 'libandroid-glob.so': 'libandroid-glob',
                    'libandroid-support.so': 'libandroid-support', 'libiconv.so': 'libiconv'}
SYSTEM_LIBS = {'libc.so', 'libm.so', 'libdl.so', 'liblog.so', 'libandroid.so'}
IMAGEFS_LIBS = {'libz.so.1', 'libz.so', 'liblzma.so', 'liblzma.so.5', 'libffi.so', 'libandroid-support.so'}
USER_AGENT = 'GameNative-verbs/1 (+https://github.com/utkarshdalal/GameNative)'
MAX_BYTES = 64 << 20


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('%s is larger than %d bytes' % (url, MAX_BYTES))
    return data


def parse_index(text):
    packages = {}
    for block in text.split('\n\n'):
        fields = dict(re.findall(r'^([\w-]+): (.*)$', block, re.M))
        if fields.get('Package'):
            packages[fields['Package']] = fields
    return packages


def deb_member(deb, name):
    """The bytes of the ar member [name] of the .deb [deb]."""
    if deb[:8] != b'!<arch>\n':
        raise ValueError('not an ar archive')
    at = 8
    while at + 60 <= len(deb):
        header = deb[at:at + 60]
        member = header[:16].decode('ascii').strip().rstrip('/')
        size = int(header[48:58].decode('ascii').strip())
        if member == name:
            return deb[at + 60:at + 60 + size]
        at += 60 + size + (size & 1)
    raise ValueError('%s is not in the package' % name)


def tar_file(data_tar, path):
    with tarfile.open(fileobj=io.BytesIO(data_tar), mode='r:*') as archive:
        for info in archive.getmembers():
            if info.name.lstrip('./') == path and info.isfile():
                return archive.extractfile(info).read()
    raise ValueError('%s is not in data.tar.xz' % path)


def elf_needed(data):
    """The DT_NEEDED names of a 64-bit little-endian ELF file."""
    if data[:4] != b'\x7fELF' or data[4] != 2 or data[5] != 1:
        raise ValueError('not a 64-bit little-endian ELF file')
    phoff, = struct.unpack_from('<Q', data, 0x20)
    phentsize, phnum = struct.unpack_from('<HH', data, 0x36)
    loads = []
    dynamic = None
    for i in range(phnum):
        kind, _, offset, vaddr, _, filesz, _, _ = struct.unpack_from('<IIQQQQQQ', data, phoff + i * phentsize)
        if kind == 1:
            loads.append((vaddr, offset, filesz))
        elif kind == 2:
            dynamic = (offset, filesz)
    if dynamic is None:
        return []
    entries = []
    for at in range(dynamic[0], dynamic[0] + dynamic[1], 16):
        tag, value = struct.unpack_from('<qQ', data, at)
        if tag == 0:
            break
        entries.append((tag, value))
    strtab = next((v for t, v in entries if t == 5), None)
    if strtab is None:
        return []
    base = next((off + strtab - va for va, off, size in loads if va <= strtab < va + size), None)
    if base is None:
        raise ValueError('DT_STRTAB is outside the loaded segments')
    return [data[base + v:data.index(b'\0', base + v)].decode('ascii') for t, v in entries if t == 1]


def package_entry(packages, name, path):
    fields = packages.get(name)
    if not fields:
        raise ValueError('%s is not in the Termux index' % name)
    url = REPO + fields['Filename']
    deb = fetch(url)
    digest = hashlib.sha256(deb).hexdigest()
    if digest != fields['SHA256'].lower():
        raise ValueError('%s has sha256 %s, the index says %s' % (url, digest, fields['SHA256']))
    data = tar_file(deb_member(deb, 'data.tar.xz'), PREFIX + path)
    return {
        'package': name, 'version': fields['Version'], 'url': url, 'sha256': digest, 'size': len(deb),
        'member': 'data.tar.xz', 'path': PREFIX + path, 'fileSha256': hashlib.sha256(data).hexdigest(),
        'needed': elf_needed(data),
    }


def build(packages):
    out = {}
    for tool, (package, path) in sorted(TOOLS.items()):
        entry = package_entry(packages, package, path)
        entry['schema'] = SCHEMA
        entry['arch'] = 'aarch64'
        libs = []
        pending = [n for n in entry['needed'] if n not in SYSTEM_LIBS | IMAGEFS_LIBS]
        while pending:
            name = pending.pop(0)
            if name in {lib['name'] for lib in libs}:
                continue
            if name not in LIBRARY_PACKAGES:
                raise ValueError('%s needs %s, which has no known package' % (tool, name))
            lib = package_entry(packages, LIBRARY_PACKAGES[name], 'lib/' + name)
            lib['name'] = name
            libs.append(lib)
            pending.extend(n for n in lib['needed'] if n not in SYSTEM_LIBS | IMAGEFS_LIBS)
        entry['libs'] = libs
        out[tool] = entry
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', required=True, help='data/tools.json')
    a = ap.parse_args(argv)
    packages = parse_index(fetch(INDEX).decode('utf-8', 'replace'))
    tools = build(packages)
    with open(a.out, 'w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(tools, indent=2, sort_keys=True) + '\n')
    for name, entry in tools.items():
        print('%s: %s %s, needs %s' % (name, entry['package'], entry['version'],
                                       ', '.join(entry['needed']) or 'nothing'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
