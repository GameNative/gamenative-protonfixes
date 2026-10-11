import importlib.util
import io
import os
import struct
import tarfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location('verb_device_tools', os.path.join(HERE, '..', 'device_tools.py'))
device_tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(device_tools)


def tiny_elf(needed):
    strtab = b'\0' + b''.join(n.encode() + b'\0' for n in needed)
    phoff, phnum = 64, 2
    dyn_at = phoff + 56 * phnum
    entries = []
    at = 1
    for n in needed:
        entries.append((1, at))
        at += len(n) + 1
    str_at = dyn_at + 16 * (len(entries) + 2)
    entries += [(5, str_at), (0, 0)]
    size = str_at + len(strtab)
    header = b'\x7fELF' + bytes([2, 1, 1]) + b'\0' * 9
    header += struct.pack('<HHIQQQIHHHHHH', 3, 183, 1, 0, phoff, 0, 0, 64, 56, phnum, 64, 0, 0)
    load = struct.pack('<IIQQQQQQ', 1, 5, 0, 0, 0, size, size, 0x1000)
    dynamic = struct.pack('<IIQQQQQQ', 2, 6, dyn_at, dyn_at, dyn_at, 16 * len(entries), 16 * len(entries), 8)
    body = b''.join(struct.pack('<qQ', t, v) for t, v in entries)
    return header + load + dynamic + body + strtab


def deb(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:xz') as archive:
        for name, data in files.items():
            info = tarfile.TarInfo('./' + name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    out = b'!<arch>\n'
    for name, data in (('debian-binary', b'2.0\n'), ('data.tar.xz', buffer.getvalue())):
        out += ('%-16s%-12s%-6s%-6s%-8s%-10d`\n' % (name, 0, 0, 0, 100644, len(data))).encode() + data
        if len(data) & 1:
            out += b'\n'
    return out


class DeviceToolTests(unittest.TestCase):
    def test_elf_needed(self):
        self.assertEqual(device_tools.elf_needed(tiny_elf(['libc.so', 'libc++_shared.so'])),
                         ['libc.so', 'libc++_shared.so'])

    def test_binary_from_deb(self):
        elf = tiny_elf(['libc.so'])
        package = deb({device_tools.PREFIX + 'bin/cabextract': elf})
        data = device_tools.tar_file(device_tools.deb_member(package, 'data.tar.xz'),
                                     device_tools.PREFIX + 'bin/cabextract')
        self.assertEqual(data, elf)
        with self.assertRaises(ValueError):
            device_tools.deb_member(package, 'control.tar.xz')

    def test_index(self):
        packages = device_tools.parse_index('Package: 7zip\nVersion: 26.04\nFilename: pool/main/7/7zip/x.deb\n'
                                            'SHA256: ab\n\nPackage: cabextract\nVersion: 1.11-1\n')
        self.assertEqual(packages['7zip']['Filename'], 'pool/main/7/7zip/x.deb')
        self.assertEqual(packages['cabextract']['Version'], '1.11-1')


if __name__ == '__main__':
    unittest.main()
