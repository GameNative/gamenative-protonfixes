import importlib.util
import json
import os
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verbs = _load('pf_verbs_test', os.path.join(HERE, '..', 'verbs.py'))
convert = _load('pf_convert_verbs_test', os.path.join(HERE, '..', 'convert.py'))
SHA = 'a' * 64
SHA2 = 'b' * 64

WINETRICKS = textwrap.dedent(r'''
    w_metadata vcrun2019 dlls \
        title="Visual C++ 2015-2019 libraries"

    load_vcrun2019()
    {
        w_override_dlls native,builtin msvcp140 vcruntime140
        w_download https://aka.ms/vs/16/release/vc_redist.x86.exe AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
        w_try_ms_installer "${WINE}" vc_redist.x86.exe ${W_OPT_UNATTENDED:+/q}
        w_download https://aka.ms/vs/16/release/vc_redist.x64.exe BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB
        w_try_ms_installer "${WINE}" vc_redist.x64.exe ${W_OPT_UNATTENDED:+/q}
    }

    w_metadata vcrun2008 dlls \
        title="Visual C++ 2008 libraries"

    load_vcrun2008()
    {
        w_download https://download.microsoft.com/vcredist_x86.exe 1111111111111111111111111111111111111111111111111111111111111111
        w_override_dlls native,builtin msvcr90
        w_try_ms_installer "${WINE}" "${file1}" ${W_OPT_UNATTENDED:+/q}
    }

    w_metadata dotnet40 dlls \
        title="MS .NET 4.0"

    load_dotnet40()
    {
        w_download https://download.microsoft.com/dotNetFx40_Full_x86_x64.exe 2222222222222222222222222222222222222222222222222222222222222222
        w_call remove_mono internal
        w_try_ms_installer "${WINE}" dotNetFx40_Full_x86_x64.exe ${W_OPT_UNATTENDED:+/q}
        w_override_dlls native mscoree
        "${WINE}" reg add "HKLM\\Software\\Microsoft\\NET Framework Setup\\NDP\\v4\\Full" /v Install /t REG_DWORD /d 0001 /f
        "${WINE}" reg add "HKLM\\Software\\Microsoft\\.NETFramework" /v OnlyUseLatestCLR /t REG_DWORD /d 0001 /f
    }

    w_metadata remove_mono settings \
        title="Remove mono"

    load_remove_mono()
    {
        echo nothing
    }

    w_metadata ubisoftconnect apps \
        title="Ubisoft Connect"

    load_ubisoftconnect()
    {
        w_download https://ubistatic3-a.akamaihd.net/orbit/launcher_installer/UbisoftConnectInstaller.exe
        w_try "${WINE}" UbisoftConnectInstaller.exe ${W_OPT_UNATTENDED:+ /S}
    }

    w_metadata directplay dlls \
        title="MS DirectPlay"

    load_directplay()
    {
        w_download_to directx9 https://example.invalid/directx_feb2010_redist.exe 3333333333333333333333333333333333333333333333333333333333333333
        w_try_cabextract -d "${W_TMP}" -L -F dxnt.cab "${W_CACHE}"/directx9/directx_feb2010_redist.exe
        w_try_cabextract -d "${W_SYSTEM32_DLLS}" -L -F 'dplayx.dll' "${W_TMP}/dxnt.cab"
        w_override_dlls native dplayx
        w_try_regsvr32 dplayx.dll
    }

    w_metadata d3dx9_43 dlls \
        title="MS d3dx9_43.dll"

    load_d3dx9_43()
    {
        w_download_to directx9 https://example.invalid/directx_Jun2010_redist.exe 4444444444444444444444444444444444444444444444444444444444444444
        w_try_cabextract -d "${W_TMP}" -L -F "*d3dx9_43*x86*" "${W_CACHE}"/directx9/directx_Jun2010_redist.exe
        for x in "${W_TMP}"/*.cab; do
            w_try_cabextract -d "${W_SYSTEM32_DLLS}" -L -F "d3dx9_43.dll" "${x}"
        done
        w_override_dlls native d3dx9_43
    }

    w_metadata d3dcompiler_47 dlls \
        title="MS d3dcompiler_47.dll"

    load_d3dcompiler_47()
    {
        w_download https://download.microsoft.com/standalonesdk/2630bae9681db6a9f6722366f47d055c.cab
        w_try_cabextract -d "${W_TMP}" -L -F "fila319" "${W_CACHE}/${W_PACKAGE}/2630bae9681db6a9f6722366f47d055c.cab"
        w_try mv "${W_TMP}/fila319" "${W_SYSTEM32_DLLS}/d3dcompiler_47.dll"
        w_override_dlls native d3dcompiler_47
    }

    w_metadata gdiplus dlls \
        title="MS GDI+"

    load_gdiplus()
    {
        w_download https://example.invalid/windows6.1-kb976932-x86.exe 5555555555555555555555555555555555555555555555555555555555555555
        w_try_cabextract -d "${W_TMP}" -L -F "x86_*gdiplus.dll" "${W_CACHE}/${W_PACKAGE}/windows6.1-kb976932-x86.exe"
        w_try cp "${W_TMP}/x86_gdiplus.dll" "${W_SYSTEM32_DLLS}/gdiplus.dll"
    }

    w_metadata sound=alsa settings \
        title="Set sound driver to ALSA"
    w_metadata videomemorysize=default settings \
        title="Let Wine detect amount of video card memory"
    w_metadata win81 settings \
        title="Set Windows version to Windows 8.1"

    load_sound()
    {
        echo "Setting sound driver to $1"
    }

    load_videomemorysize()
    {
        echo "Setting video memory size to $1"
    }

    load_win81()
    {
        w_set_winver win81
    }
''')


def _winetricks():
    wt = convert.Winetricks()
    wt.add_script(WINETRICKS, 'winetricks')
    return wt


class VerbClassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wt = _winetricks()
        cls.layouts = tempfile.mkdtemp()
        cls.pins = {'https://download.microsoft.com/standalonesdk/2630bae9681db6a9f6722366f47d055c.cab': SHA2,
                    'https://ubistatic3-a.akamaihd.net/orbit/launcher_installer/UbisoftConnectInstaller.exe': SHA}
        names = ['vcrun2019', 'vcrun2008', 'dotnet40', 'ubisoftconnect', 'directplay', 'd3dx9_43', 'd3dcompiler_47',
                 'gdiplus', 'sound=alsa', 'videomemorysize=default', 'win81', 'nosuchverb', 'hidewineexports=enable']
        resolved = {n: {k: v for k, v in cls.wt.resolve(n).items() if v is not None} for n in names}
        cls.v = verbs.classify_all(resolved, cls.wt, cls.pins, cls.layouts, {})

    def test_every_verb_gets_a_known_class(self):
        for name, verb in self.v.items():
            self.assertIn(verb['class'], verbs.CLASSES, name)
            self.assertIn('kind', verb, name)

    def test_component_verbs(self):
        directplay = self.v['directplay']
        self.assertEqual(directplay['class'], 'component')
        self.assertEqual(directplay['component'], 'directplay')
        self.assertEqual(directplay['regsvr32'], [{'arch': 'x86', 'name': 'dplayx.dll'}])
        self.assertEqual(directplay['kind'], 'dll')
        d3dx = self.v['d3dx9_43']
        self.assertEqual(d3dx['component'], 'direct3d')
        self.assertNotIn('regsvr32', d3dx)
        self.assertEqual([p['entry'] for p in d3dx['preferred']],
                         ['build/native/release/bin/x86/D3DX9_43.dll', 'build/native/release/bin/x64/D3DX9_43.dll'])
        self.assertTrue(all(p['sha256'] == verbs.D3DX_NUGET['sha256'] for p in d3dx['preferred']))
        self.assertEqual(self.v['d3dcompiler_47']['files'][0]['sha256'], SHA2)

    def test_offline_installer_for_vc_runtimes(self):
        vc = self.v['vcrun2019']
        self.assertEqual(vc['class'], 'offline_installer')
        self.assertEqual([(i['arch'], i['sha256']) for i in vc['installers']], [('x86', SHA), ('x64', SHA2)])
        self.assertEqual(vc['overrides'], {'msvcp140': 'n,b', 'vcruntime140': 'n,b'})
        self.assertEqual(vc['builtinUnless']['env'], 'PROTONFIX_VC2022_NATIVE')
        self.assertEqual(vc['builtinUnless']['files'], ['msvcp140*.dll'])
        self.assertEqual({r['key'] for r in vc['fallbackRegistry']},
                         {'Software\\Microsoft\\VisualStudio\\14.0\\VC\\Runtimes\\x86',
                          'Software\\Microsoft\\VisualStudio\\14.0\\VC\\Runtimes\\x64'})
        self.assertNotIn('layout', vc)
        self.assertEqual(vc['kind'], 'premark')

    def test_offline_installer_records_an_existing_layout(self):
        with open(os.path.join(self.layouts, 'vcrun2019.layout.json'), 'w') as f:
            f.write('{"schema": 1, "requiresTools": ["7zz", "cabextract"], "dictSizeBytes": 67108864}\n')
        try:
            resolved = {k: v for k, v in self.wt.resolve('vcrun2019').items() if v is not None}
            vc = verbs.classify('vcrun2019', resolved, self.wt, self.pins, self.layouts)
            self.assertEqual(vc['layout']['path'], 'verbs/vcrun2019.layout.json')
            self.assertEqual(len(vc['layout']['sha256']), 64)
            self.assertEqual(vc['requiresTools'], ['7zz', 'cabextract'])
            self.assertEqual(vc['dictSizeBytes'], 67108864)
        finally:
            os.remove(os.path.join(self.layouts, 'vcrun2019.layout.json'))

    def test_dotnet_removes_mono_and_writes_ndp_keys(self):
        net = self.v['dotnet40']
        self.assertEqual(net['class'], 'offline_installer')
        self.assertTrue(net['removeMono'])
        self.assertEqual(net['overrides']['mscoree'], 'n')
        self.assertEqual(net['installers'][0]['arch'], 'any')
        keys = {(r['key'], name) for r in net['registry'] for name in r['values']}
        self.assertIn(('Software\\Microsoft\\NET Framework Setup\\NDP\\v4\\Full', 'Install'), keys)
        self.assertIn(('Software\\Microsoft\\.NETFramework', 'OnlyUseLatestCLR'), keys)
        self.assertIn(('Software\\Wow6432Node\\Microsoft\\NET Framework Setup\\NDP\\v4\\Full', 'Version'), keys)
        added = [r for r in net['registry'] if r['key'] == 'Software\\Microsoft\\.NETFramework'][0]
        self.assertEqual(added['values']['OnlyUseLatestCLR'], {'type': 'REG_DWORD', 'value': '1'})

    def test_exec_verbs(self):
        vc = self.v['vcrun2008']
        self.assertEqual(vc['class'], 'exec')
        self.assertEqual(vc['exec'][0]['args'], ['/qb!'])
        self.assertEqual(vc['overrides'], {'msvcr90': 'n,b'})
        ubi = self.v['ubisoftconnect']
        self.assertEqual(ubi['exec'][0]['sha256'], SHA)
        self.assertTrue(ubi['exec'][0]['gameCopy'])
        self.assertEqual(ubi['timeoutSec'], 1200)

    def test_registry_and_skip_verbs(self):
        self.assertEqual(self.v['sound=alsa']['class'], 'registry')
        self.assertEqual(self.v['sound=alsa']['setting'], {'audioDriver': 'alsa'})
        self.assertEqual(self.v['videomemorysize=default']['remove'][0]['names'], ['VideoMemorySize'])
        self.assertEqual(self.v['win81']['class'], 'registry')
        self.assertEqual(self.v['win81']['version'], 'win81')
        self.assertEqual(self.v['gdiplus']['class'], 'skip')
        self.assertIn('Windows 7 SP1', self.v['gdiplus']['reason'])
        self.assertEqual(self.v['hidewineexports=enable']['class'], 'skip')
        self.assertEqual(self.v['nosuchverb']['class'], 'skip')

    def test_service_pack_verbs_use_byte_ranges(self):
        resolved = {k: v for k, v in self.wt.resolve('gdiplus').items() if v is not None}
        entry = resolved['files'][0]
        record = {'url': entry['url'], 'sha256': entry['sha256'], 'offset': 10, 'length': 20,
                  'rangeSha256': 'b' * 64, 'header': 'TVNDRg==', 'members': [entry['entry']],
                  'extractedSha256': 'c' * 64, 'extractedSize': 5, 'folderBytes': 99}
        ranged = verbs.classify('gdiplus', resolved, self.wt, self.pins, self.layouts,
                                {verbs.range_key(entry): record})
        self.assertEqual(ranged['class'], 'download')
        self.assertNotIn('reason', ranged)
        f = ranged['files'][0]
        self.assertEqual(f['hops'], [
            {'type': 'range', 'url': entry['url'], 'sha256': entry['sha256'], 'offset': 10, 'length': 20,
             'rangeSha256': 'b' * 64, 'header': 'TVNDRg=='},
            {'type': 'cab', 'member': entry['entry']},
        ])
        self.assertEqual((f['extractedSha256'], f['extractedSize']), ('c' * 64, 5))
        large = dict(record, tooLarge=True)
        skipped = verbs.classify('gdiplus', resolved, self.wt, self.pins, self.layouts,
                                 {verbs.range_key(entry): large})
        self.assertEqual((skipped['class'], skipped['reason']), ('skip', 'slice too large'))

    def test_wayback_urls_use_the_raw_form(self):
        self.assertEqual(verbs.raw_url('https://web.archive.org/web/20120325002813/https://download.microsoft.com/x.msi'),
                         'https://web.archive.org/web/20120325002813id_/https://download.microsoft.com/x.msi')
        self.assertEqual(verbs.raw_url('https://web.archive.org/web/2010id_/https://x'), 'https://web.archive.org/web/2010id_/https://x')
        self.assertEqual(verbs.raw_url('https://download.microsoft.com/x.msi'), 'https://download.microsoft.com/x.msi')
        self.assertEqual(self.v['dotnet40']['msiProperties']['VersionNT'], '501')

    def test_unpinned_downloads_are_reported(self):
        self.assertEqual(verbs.unpinned(self.v), [])
        broken = {'x': {'class': 'download', 'files': [{'url': 'https://example.invalid/a', 'sha256': None}]},
                  'y': {'class': 'skip', 'files': [{'url': 'https://example.invalid/b', 'sha256': None}]}}
        self.assertEqual(verbs.unpinned(broken), [('x', 'https://example.invalid/a')])

    def test_build_verbs_adds_the_extra_verbs_and_classes(self):
        built = convert.build_verbs(self.wt, ['vcrun2019'])
        self.assertIn('vcrun2019', built)
        for name in verbs.EXTRA_VERBS:
            self.assertIn(name, built)
        self.assertTrue(all('class' in v for v in built.values()))


class ShippedTableTests(unittest.TestCase):
    def test_shipped_verbs_json_is_classified_and_pinned(self):
        path = os.path.join(HERE, '..', '..', '..', 'data', 'verbs.json')
        if not os.path.isfile(path):
            self.skipTest('data/verbs.json is not in this checkout')
        with open(path, encoding='utf-8') as f:
            table = json.load(f)['verbs']
        for name, verb in table.items():
            self.assertIn(verb.get('class'), verbs.CLASSES, name)
            if verb['class'] == 'skip':
                self.assertTrue(verb.get('reason'), name)
        self.assertEqual(verbs.unpinned(table), [])
        for name, verb in table.items():
            if verb['class'] == 'offline_installer':
                self.assertTrue(verb['installers'], name)


if __name__ == '__main__':
    unittest.main()
