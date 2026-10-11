import hashlib
import importlib.util
import io
import json
import os
import struct
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location('verb_msi_tables', os.path.join(HERE, '..', 'msi_tables.py'))
msi_tables = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(msi_tables)

ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF
FATSECT = 0xFFFFFFFD
STR = 0x0D48
NSTR = 0x1D48
I2 = 0x0502
NI2 = 0x1502
I4 = 0x0104


def compound_file(streams):
    """A version 3 compound file holding [streams] (name -> bytes), every stream in the regular FAT."""
    sector = 512
    data_sectors = []
    starts = {}
    for name, data in streams.items():
        count = max(1, -(-len(data) // sector))
        starts[name] = len(data_sectors)
        padded = data + b'\0' * (count * sector - len(data))
        data_sectors.extend(padded[i * sector:(i + 1) * sector] for i in range(count))
    entries = [('Root Entry', 5, ENDOFCHAIN, 0)] + [(n, 2, starts[n], len(d)) for n, d in streams.items()]
    directory = b''
    for index, (name, kind, start, size) in enumerate(entries):
        encoded = name.encode('utf-16-le') + b'\0\0'
        right = index + 1 if 0 < index < len(entries) - 1 else FREESECT
        child = 1 if index == 0 and len(entries) > 1 else FREESECT
        entry = encoded.ljust(64, b'\0') + struct.pack('<HBBIII', len(encoded), kind, 1, FREESECT, right, child)
        entry += b'\0' * 36 + struct.pack('<IQ', start, size)
        directory += entry
    directory += b'\0' * (-len(directory) % sector)
    dir_start = len(data_sectors)
    dir_count = len(directory) // sector
    used = dir_start + dir_count
    fat_count = 1
    while used + fat_count > fat_count * (sector // 4):
        fat_count += 1
    fat = [FREESECT] * (fat_count * sector // 4)
    for name, data in streams.items():
        count = max(1, -(-len(data) // sector))
        for i in range(count):
            fat[starts[name] + i] = starts[name] + i + 1 if i < count - 1 else ENDOFCHAIN
    for i in range(dir_count):
        fat[dir_start + i] = dir_start + i + 1 if i < dir_count - 1 else ENDOFCHAIN
    for i in range(fat_count):
        fat[used + i] = FATSECT
    difat = [used + i for i in range(fat_count)] + [FREESECT] * (109 - fat_count)
    header = msi_tables.MSI_MAGIC + b'\0' * 16 + struct.pack('<HHHHH', 0x3E, 3, 0xFFFE, 9, 6) + b'\0' * 6
    header += struct.pack('<IIIIIIIII', 0, fat_count, dir_start, 0, 0, ENDOFCHAIN, 0, ENDOFCHAIN, 0)
    header += struct.pack('<109I', *difat)
    body = b''.join(data_sectors) + directory + struct.pack('<%dI' % len(fat), *fat)
    return header + body


def stored_cab(files):
    names = list(files)
    payload = b''.join(files[n] for n in names)
    entries = b''
    offset = 0
    for name in names:
        entries += struct.pack('<IIHHHH', len(files[name]), offset, 0, 0, 0, 0x20) + name.encode() + b'\0'
        offset += len(files[name])
    blocks = [payload[i:i + 32768] for i in range(0, len(payload), 32768)] or [b'']
    first_data = 36 + 8 + len(entries)
    data = b''.join(struct.pack('<IHH', 0, len(b), len(b)) + b for b in blocks)
    total = first_data + len(data)
    header = b'MSCF' + struct.pack('<IIIIIBBHHHHH', 0, total, 0, 44, 0, 3, 1, 1, len(names), 0, 0, 0)
    folder = struct.pack('<IHH', first_data, len(blocks), 0)
    return header + folder + entries + data


class MsiBuilder:
    def __init__(self):
        self.strings = ['']
        self.tables = {}

    def ref(self, text):
        if text is None:
            return 0
        if text not in self.strings:
            self.strings.append(text)
        return self.strings.index(text)

    def add(self, name, columns, rows):
        self.tables[name] = (columns, rows)

    def build(self, extra):
        column_rows = []
        streams = {}
        for table, (columns, rows) in self.tables.items():
            for number, (col, kind) in enumerate(columns, 1):
                column_rows.append((table, number, col, kind))
        for table, (columns, rows) in list(self.tables.items()) + [
            ('_Columns', ([('Table', STR), ('Number', I2), ('Name', STR), ('Type', I2)], column_rows)),
            ('_Tables', ([('Name', STR)], [(t,) for t in self.tables])),
        ]:
            data = b''
            for index, (_, kind) in enumerate(columns):
                for row in rows:
                    value = row[index]
                    if kind & 0x0800:
                        data += struct.pack('<H', self.ref(value))
                    elif (kind & 0xFF) == 4:
                        data += struct.pack('<I', 0 if value is None else (value + 0x80000000) & 0xFFFFFFFF)
                    else:
                        data += struct.pack('<H', 0 if value is None else (value + 0x8000) & 0xFFFF)
            streams[msi_tables.encode_stream_name(table, table=True)] = data
        pool = struct.pack('<HH', 1252, 0)
        text = b''
        for s in self.strings[1:]:
            raw = s.encode('cp1252')
            pool += struct.pack('<HH', len(raw), 1)
            text += raw
        streams[msi_tables.encode_stream_name('_StringPool', table=True)] = pool
        streams[msi_tables.encode_stream_name('_StringData', table=True)] = text
        for name, data in extra.items():
            streams[msi_tables.encode_stream_name(name)] = data
        return compound_file(streams)


def sample_msi(cab):
    b = MsiBuilder()
    b.add('Property', [('Property', STR), ('Value', STR)], [('ProductVersion', '14.0.1'), ('ProductName', 'Test')])
    b.add('Directory', [('Directory', STR), ('Directory_Parent', NSTR), ('DefaultDir', STR)], [
        ('TARGETDIR', None, 'SourceDir'), ('System64Folder', 'TARGETDIR', '.'), ('SystemFolder', 'TARGETDIR', '.'),
        ('ProgramFilesFolder', 'TARGETDIR', '.'), ('APPDIR', 'ProgramFilesFolder', 'APP|My App'),
    ])
    b.add('Feature', [('Feature', STR), ('Feature_Parent', NSTR), ('Level', I2)], [('Main', None, 1), ('Off', None, 0)])
    b.add('Component', [('Component', STR), ('ComponentId', NSTR), ('Directory_', STR), ('Attributes', I2),
                        ('Condition', NSTR), ('KeyPath', NSTR)], [
        ('C64', None, 'System64Folder', 0x100, None, 'f64'),
        ('C32', None, 'SystemFolder', 0, None, 'f32'),
        ('CApp', None, 'APPDIR', 0, 'VersionNT >= 600 AND NOT Installed', 'fapp'),
        ('CSkip', None, 'SystemFolder', 0, 'VersionNT < 500', 'fskip'),
        ('COff', None, 'SystemFolder', 0, None, 'foff'),
    ])
    b.add('FeatureComponents', [('Feature_', STR), ('Component_', STR)],
          [('Main', 'C64'), ('Main', 'C32'), ('Main', 'CApp'), ('Main', 'CSkip'), ('Off', 'COff')])
    b.add('File', [('File', STR), ('Component_', STR), ('FileName', STR), ('FileSize', I4), ('Version', NSTR),
                   ('Language', NSTR), ('Attributes', NI2), ('Sequence', I2)], [
        ('f64', 'C64', 'MSVCP1~1.DLL|msvcp140.dll', 4, None, None, None, 1),
        ('f32', 'C32', 'msvcp140.dll', 4, None, None, None, 2),
        ('fapp', 'CApp', 'game.dat', 3, None, None, None, 3),
        ('fskip', 'CSkip', 'skip.dll', 1, None, None, None, 4),
        ('foff', 'COff', 'off.dll', 1, None, None, None, 5),
    ])
    b.add('Media', [('DiskId', I2), ('LastSequence', I2), ('DiskPrompt', NSTR), ('Cabinet', NSTR),
                    ('VolumeLabel', NSTR), ('Source', NSTR)], [(1, 10, None, '#Data1.cab', None, None)])
    b.add('Registry', [('Registry', STR), ('Root', I2), ('Key', STR), ('Name', NSTR), ('Value', NSTR),
                       ('Component_', STR)], [
        ('r1', 2, 'Software\\Test', 'Version', '[ProductVersion]', 'C32'),
        ('r2', 2, 'Software\\Test', 'Count', '#5', 'C64'),
        ('r3', 2, 'Software\\Test\\Path', None, '[APPDIR]', 'C64'),
        ('r4', -1, 'Software\\Test\\Key', '+', None, 'CApp'),
        ('r5', 1, 'Software\\Test', 'Bin', '#x0AFF', 'C64'),
        ('r6', 0, 'CLSID\\{X}\\InprocServer32', None, '[#f32]', 'C32'),
        ('r7', 2, 'Software\\Off', 'X', '1', 'COff'),
    ])
    return b.build({'Data1.cab': cab})


class MsiTableTests(unittest.TestCase):
    def setUp(self):
        self.files = {'f64': b'six4', 'f32': b'thr2', 'fapp': b'app', 'fskip': b's', 'foff': b'o'}
        self.cab = stored_cab(self.files)
        self.msi_bytes = sample_msi(self.cab + b'\0' * 5000)

    def test_stream_names_round_trip(self):
        for name in ('_StringPool', 'Data1.cab', 'Binary.NewBinary1'):
            self.assertEqual(msi_tables.decode_stream_name(msi_tables.encode_stream_name(name)), name)
        self.assertEqual(msi_tables.decode_stream_name(msi_tables.encode_stream_name('File', table=True)), '\u4840File')

    def test_reads_tables(self):
        msi = msi_tables.Msi(self.msi_bytes)
        self.assertIn('File', msi.table_names)
        files = {row['File']: row for row in msi.table('File')}
        self.assertEqual(files['f64']['FileName'], 'MSVCP1~1.DLL|msvcp140.dll')
        self.assertEqual(files['f32']['Sequence'], 2)
        self.assertIsNone(files['f32']['Attributes'])
        self.assertEqual({row['Root'] for row in msi.table('Registry')}, {-1, 0, 1, 2})

    def test_layout_of_one_package(self):
        package = msi_tables.Package(msi_tables.Msi(self.msi_bytes))
        package.prepare()
        targets = package.destinations()
        self.assertEqual(targets['f64'], 'drive_c/windows/system32/msvcp140.dll')
        self.assertEqual(targets['f32'], 'drive_c/windows/syswow64/msvcp140.dll')
        self.assertEqual(targets['fapp'], 'drive_c/Program Files (x86)/My App/game.dat')
        self.assertNotIn('fskip', targets)
        self.assertNotIn('foff', targets)
        registry = {(r['hive'], r['key'], r.get('name')): r for r in package.registry()}
        self.assertEqual(registry[('HKLM', 'Software\\Wow6432Node\\Test', 'Version')]['value'], '14.0.1')
        self.assertEqual(registry[('HKLM', 'Software\\Test', 'Count')], {
            'hive': 'HKLM', 'key': 'Software\\Test', 'name': 'Count', 'type': 'REG_DWORD', 'value': '5'})
        self.assertEqual(registry[('HKLM', 'Software\\Test\\Path', '')]['value'], 'C:\\Program Files (x86)\\My App\\')
        self.assertEqual(registry[('HKLM', 'Software\\Wow6432Node\\Test\\Key', None)]['type'], 'KEY')
        self.assertEqual(registry[('HKCU', 'Software\\Test', 'Bin')]['value'], '0aff')
        self.assertEqual(registry[('HKLM', 'Software\\Wow6432Node\\Classes\\CLSID\\{X}\\InprocServer32', '')]['value'],
                         'C:\\windows\\syswow64\\msvcp140.dll')
        self.assertNotIn(('HKLM', 'Software\\Off', 'X'), registry)

    def test_conditions(self):
        props = {'VersionNT': '603', 'Name': 'Wine', 'Empty': ''}
        c = msi_tables.Conditions(props, lambda f: 3 if f == 'On' else -1, lambda c: -1)
        self.assertTrue(c.evaluate('VersionNT >= 600'))
        self.assertFalse(c.evaluate('VersionNT < 600'))
        self.assertTrue(c.evaluate('NOT Empty AND (Name ~= "wine" OR Missing)'))
        self.assertTrue(c.evaluate('&On = 3 AND &Off <> 3'))
        self.assertTrue(c.evaluate('Name >< "in"'))
        self.assertFalse(c.evaluate('Missing'))
        self.assertTrue(c.evaluate('((( broken', default=True))

    def test_cab_reader_and_embedded_ranges(self):
        self.assertEqual(dict(msi_tables.cab_members(self.cab)), {k: len(v) for k, v in self.files.items()})
        msi = msi_tables.Msi(self.msi_bytes)
        stream = msi.stream('Data1.cab')
        ranges = msi.cf.ranges(stream)
        joined = b''.join(self.msi_bytes[o:o + n] for o, n in ranges)
        self.assertEqual(joined, msi.cf.read(stream))
        out = tempfile.mkdtemp()
        msi_tables._python_cab_extract(self.cab, out)
        with open(os.path.join(out, 'fapp'), 'rb') as f:
            self.assertEqual(f.read(), b'app')

    def test_build_layout_from_a_zipped_installer(self):
        work = tempfile.mkdtemp()
        installer = os.path.join(work, 'installer.zip')
        with zipfile.ZipFile(installer, 'w') as z:
            z.writestr('pkg/test.msi', self.msi_bytes)
            z.writestr('readme.txt', 'hello')
        with open(installer, 'rb') as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        spec = {'installers': [{'url': 'file://' + installer, 'sha256': sha, 'arch': 'any'}]}
        layout = msi_tables.build_layout('testverb', spec, os.path.join(work, 'cache'),
                                         keep=lambda rel: not rel.endswith('/syswow64/msvcp140.dll'))
        self.assertEqual(layout['schema'], 1)
        self.assertEqual(layout['installers'][0]['sha256'], sha)
        files = {f['to']: f for f in layout['files']}
        self.assertEqual(set(files), {'drive_c/windows/system32/msvcp140.dll',
                                      'drive_c/Program Files (x86)/My App/game.dat'})
        archive = layout['archives'][files['drive_c/windows/system32/msvcp140.dll']['archive']]
        self.assertEqual(archive['hops'][0], 'pkg/test.msi')
        self.assertIn('ranges', archive['hops'][1])
        self.assertEqual(files['drive_c/windows/system32/msvcp140.dll']['member'], 'f64')
        self.assertEqual(layout['unpackedBytes'], 7)
        json.dumps(layout)

    def test_burn_bundle_containers(self):
        ux = stored_cab({'0': b'<BurnManifest><Payload Id="p" FilePath="packages\\x\\a.msi" SourcePath="a0" /></BurnManifest>'})
        payload = stored_cab({'a0': b'data'})
        stub = bytearray(b'MZ' + b'\0' * 0x3E)
        pe = len(stub)
        struct.pack_into('<I', stub, 0x3C, pe)
        stub += b'PE\0\0' + struct.pack('<HHIIIHH', 0x14C, 1, 0, 0, 0, 0, 0)
        section_offset = len(stub) + 40
        burn = struct.pack('<I', msi_tables.BURN_MAGIC) + b'\0' * 20 + struct.pack('<IIIIII', 0, 0, 0, 0, 0, 2)
        burn += struct.pack('<II', len(ux), len(payload))
        stub += b'.wixburn' + struct.pack('<IIII', len(burn), 0, len(burn), section_offset) + b'\0' * 16
        stub += burn
        struct.pack_into('<I', stub, section_offset + 24, len(stub))
        data = bytes(stub) + ux + payload
        found = msi_tables.burn_containers(data)
        self.assertEqual(found, [(len(stub), len(ux)), (len(stub) + len(ux), len(payload))])
        self.assertEqual(msi_tables.burn_names(ux.decode('latin-1')), {'a0': 'packages/x/a.msi'})
        self.assertEqual(msi_tables.embedded_cabinets(data), found)

    def test_choose_packages_prefers_full_framework(self):
        work = tempfile.mkdtemp()
        nodes = []
        for name in ('netfx_Core_x64.msi', 'netfx_Full_x64.msi', 'netfx_Extended_x64.msi'):
            path = os.path.join(work, name)
            with open(path, 'wb') as f:
                f.write(msi_tables.MSI_MAGIC)
            nodes.append(msi_tables.Node(path, [name], name, None))
        chosen = msi_tables.choose_packages(nodes, None)
        self.assertEqual([n.logical for n in chosen], ['netfx_Full_x64.msi'])
        chosen = msi_tables.choose_packages(nodes, ['*core*'])
        self.assertEqual([n.logical for n in chosen], ['netfx_Core_x64.msi'])


if __name__ == '__main__':
    unittest.main()
