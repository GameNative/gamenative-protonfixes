import hashlib
import importlib.util
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
SPEC = importlib.util.spec_from_file_location('verb_ranges', os.path.join(HERE, '..', 'ranges.py'))
ranges = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ranges)
msi_tables = ranges.msi_tables


def stored_folders(folders, reserve=0):
    """A stored (uncompressed) cabinet with one folder per list of (name, bytes) and [reserve] bytes per block."""
    entries = b''
    block_lists = []
    for index, files in enumerate(folders):
        offset = 0
        for name, data in files:
            entries += struct.pack('<IIHHHH', len(data), offset, index, 0, 0, 0x20) + name.encode() + b'\0'
            offset += len(data)
        payload = b''.join(d for _, d in files)
        block_lists.append([payload[i:i + 32768] for i in range(0, len(payload), 32768)] or [b''])
    fixed = 36 + (4 if reserve else 0)
    files_at = fixed + 8 * len(folders)
    first = files_at + len(entries)
    folder_table = b''
    data = b''
    for blocks in block_lists:
        folder_table += struct.pack('<IHH', first + len(data), len(blocks), 0)
        data += b''.join(struct.pack('<IHH', 0, len(b), len(b)) + b'\xee' * reserve + b for b in blocks)
    count = sum(len(f) for f in folders)
    header = b'MSCF' + struct.pack('<IIIIIBBHHHHH', 0, first + len(data), 0, files_at, 0, 3, 1, len(folders),
                                   count, 4 if reserve else 0, 0, 0)
    if reserve:
        header += struct.pack('<HBB', 0, 0, reserve)
    return header + folder_table + entries + data


def blob(seed, size):
    out = b''
    while len(out) < size:
        seed = hashlib.sha256(seed).digest()
        out += seed
    return out[:size]


class RangeTests(unittest.TestCase):
    def setUp(self):
        self.a = blob(b'a', 50000)
        self.b = blob(b'b', 70000)
        self.c = blob(b'c', 9000)
        self.d = blob(b'd', 100000)
        self.cab = stored_folders([[('dir\\a.dll', self.a), ('dir\\b.dll', self.b), ('c.dll', self.c)],
                                   [('x86_x\\d.dll', self.d)]], reserve=4)
        self.exe = b'MZ' + b'\0' * 1000 + self.cab + b'\0' * 50

    def slice_of(self, member):
        cab = ranges.outer_cabinet(self.exe, member)
        m = cab.find(member)
        start, length, blocks = cab.blocks_for(m)
        header = ranges.single_folder_header(cab, m, blocks, length)
        return start, length, blocks, header + self.exe[start:start + length]

    def unpack(self, data):
        with tempfile.TemporaryDirectory() as work:
            msi_tables._python_cab_extract(data, work)
            return {rel: open(full, 'rb').read() for full, rel in msi_tables._walk(work)}

    def test_member_in_the_middle_of_a_folder(self):
        start, length, blocks, data = self.slice_of('dir/b.dll')
        self.assertEqual(blocks, 4)
        out = self.unpack(data)
        self.assertEqual(out['dir/b.dll'], self.b)
        self.assertEqual(len(out['padding.bin']), len(self.a))
        self.assertNotIn('c.dll', out)
        self.assertLess(length, len(self.cab))

    def test_member_at_the_start_of_a_later_folder(self):
        start, length, blocks, data = self.slice_of('x86_x/d.dll')
        out = self.unpack(data)
        self.assertEqual(out, {'x86_x/d.dll': self.d})
        self.assertEqual(self.exe[start:start + 4], struct.pack('<I', 0))

    def test_header_fields(self):
        cab = ranges.outer_cabinet(self.exe, 'c.dll')
        m = cab.find('c.dll')
        start, length, blocks = cab.blocks_for(m)
        header = ranges.single_folder_header(cab, m, blocks, length)
        size, = struct.unpack_from('<I', header, 8)
        self.assertEqual(size, len(header) + length)
        self.assertEqual(struct.unpack_from('<HHH', header, 26), (1, 2, 4))
        self.assertEqual(header[36:40], struct.pack('<HBB', 0, 0, 4))

    def test_unknown_member(self):
        with self.assertRaises(msi_tables.LayoutError):
            ranges.outer_cabinet(self.exe, 'nope.dll')


if __name__ == '__main__':
    unittest.main()
