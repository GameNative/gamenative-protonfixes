import hashlib
import importlib.util
import json
import os
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location('pf_convert', os.path.join(HERE, '..', 'convert.py'))
convert = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(convert)


def conv(src, game_id='1', store='steam', umu_id=None):
    return convert.convert_fix(textwrap.dedent(src), game_id, store, umu_id)


WINETRICKS_SNIPPET = r'''
helper_directx_Jun2010()
{
    w_download_to directx9 https://example.invalid/directx_Jun2010_redist.exe 053f
    DIRECTX_NAME=directx_Jun2010_redist.exe
}

w_metadata d3dx9_43 dlls \
    title="MS d3dx9_43.dll" \
    publisher="Microsoft"

load_d3dx9_43()
{
    dllname=d3dx9_43
    helper_directx_Jun2010
    w_try_cabextract -d "${W_TMP}" -L -F "*${dllname}*x86*" "${W_CACHE}"/directx9/${DIRECTX_NAME}
    for x in "${W_TMP}"/*.cab; do
        w_try_cabextract -d "${W_SYSTEM32_DLLS}" -L -F "${dllname}.dll" "${x}"
    done
    if test "${W_ARCH}" = "win64"; then
        w_try_cabextract -d "${W_TMP}" -L -F "*${dllname}*x64*" "${W_CACHE}"/directx9/${DIRECTX_NAME}
        for x in "${W_TMP}"/*x64.cab; do
            w_try_cabextract -d "${W_SYSTEM64_DLLS}" -L -F "${dllname}.dll" "${x}"
        done
    fi
    w_override_dlls native ${dllname}
}

w_metadata arial fonts \
    title="MS Arial"

load_arial()
{
    w_download_to corefonts "https://example.invalid/arial32.exe" 8529
    w_try_cabextract -d "${W_TMP}" "${W_CACHE}"/corefonts/arial32.exe
    w_try_cp_font_files "${W_TMP}" "${W_FONTSDIR_UNIX}" "Arial*.TTF"
    w_register_font arial.ttf "Arial"
}

w_metadata hidewineexports=enable settings \
    title="Enable hiding Wine exports"

load_hidewineexports()
{
    case ${arg} in
        enable)
            _W_registry_value="\"Y\""
            ;;
        *) w_die "Unexpected argument, ${arg}";;
    esac

    cat > "${W_TMP}"/set-wineexports.reg <<_EOF_
REGEDIT4

[HKEY_CURRENT_USER\\Software\\Wine]
"HideWineExports"=${_W_registry_value}

_EOF_
    w_try_regedit "${W_TMP}"/set-wineexports.reg
}

w_metadata win7 settings \
    title="Set Windows version to Windows 7"

load_win7()
{
    w_set_winver win7
}

w_metadata vcrun2019 dlls \
    title="Visual C++ 2015-2019"

load_vcrun2019()
{
    w_override_dlls native,builtin msvcp140 vcruntime140
    w_download https://example.invalid/vc_redist.x86.exe abcd
    w_try_cd "${W_CACHE}"/"${W_PACKAGE}"
    w_try_ms_installer "${WINE}" vc_redist.x86.exe ${W_OPT_UNATTENDED:+/q}
}

w_metadata physx dlls \
    title="PhysX"

load_physx()
{
    w_download https://example.invalid/PhysX.exe 9b42
    w_try_cd "${W_CACHE}/${W_PACKAGE}"
    w_try "${WINE}" PhysX.exe ${W_OPT_UNATTENDED:+/s}
}
'''


class FixConversionTests(unittest.TestCase):
    def test_declarative_fix(self):
        steps, coverage, _ = conv('''
            from protonfixes import util
            def main():
                util.protontricks('d3dcompiler_47')
                util.winedll_override('dinput8', util.OverrideOrder.NATIVE_BUILTIN)
                util.winedll_override('dcomp', util.OverrideOrder.DISABLED)
                util.set_environment('PULSE_LATENCY_MSEC', '60')
                util.append_argument('-dx11')
                util.disable_esync()
                util.regedit_add('HKLM\\\\Software\\\\Foo', 'Bar', 'REG_DWORD', '0x10')
        ''')
        self.assertEqual(coverage, 'full')
        self.assertEqual(steps[0], {'op': 'verb', 'name': 'd3dcompiler_47'})
        self.assertEqual(steps[1], {'op': 'dll_override', 'overrides': {'dinput8': 'n,b', 'dcomp': 'd'}})
        self.assertEqual(steps[2], {'op': 'env', 'set': {'PULSE_LATENCY_MSEC': '60'}})
        self.assertEqual(steps[3], {'op': 'launch_args', 'append': '-dx11'})
        self.assertEqual(steps[4], {'op': 'sync', 'esync': False})
        self.assertEqual(steps[5], {'op': 'registry', 'hive': 'HKLM', 'key': 'Software\\Foo',
                                    'values': {'Bar': {'type': 'REG_DWORD', 'value': '16'}}})

    def test_early_phase(self):
        steps, coverage, _ = conv('''
            import os
            from protonfixes import util
            def early():
                os.environ['PROTON_DLL_COPY'] = '*'
            def main():
                util.protontricks('win10')
        ''')
        self.assertEqual(coverage, 'full')
        self.assertEqual(steps[0], {'op': 'env', 'set': {'PROTON_DLL_COPY': '*'}, 'phase': 'early'})
        self.assertNotIn('phase', steps[1])

    def test_guarded_fix(self):
        steps, coverage, _ = conv('''
            import os
            import glob
            from protonfixes import util
            def main():
                install_dir = glob.escape(util.get_game_install_path())
                if os.path.exists(install_dir + '/Bin/ue4ss'):
                    util.winedll_override('dwmapi', util.OverrideOrder.NATIVE_BUILTIN)
                if 'NOSTEAM' in os.environ:
                    util.replace_command('-steam', '')
                if os.environ.get('PROTON_USE_WAYLAND') == '1':
                    util.set_environment('X', '1')
        ''')
        self.assertEqual(coverage, 'full')
        self.assertEqual(steps[0]['when'], [{'fileExists': '${install}/Bin/ue4ss'}])
        self.assertEqual(steps[1]['when'], [{'nosteam': True}])
        self.assertEqual(steps[2]['when'], [{'wayland': True}])

    def test_host_predicates(self):
        steps, coverage, _ = conv('''
            import os
            from protonfixes import util
            def is_one(name):
                return os.environ.get(name, '') == '1'
            def main():
                if is_one('PROTON_USE_WAYLAND') or is_one('PROTON_ENABLE_WAYLAND'):
                    util.winedll_override('libGLESv2', util.OverrideOrder.DISABLED)
                if os.environ.get('XDG_CURRENT_DESKTOP') != 'gamescope':
                    return
                util.set_environment('A', '1')
        ''')
        self.assertEqual(coverage, 'full')
        self.assertEqual(steps, [
            {'op': 'dll_override', 'overrides': {'libGLESv2': 'd'}, 'when': [{'wayland': True}]},
            {'op': 'env', 'set': {'A': '1'}, 'when': [{'gamescope': True}]},
        ])

    def test_early_return_guard(self):
        steps, coverage, _ = conv('''
            import os
            from protonfixes import util
            def main():
                if os.path.exists('modorganizer2'):
                    return
                util.replace_command('Launcher.exe', 'Game.exe')
        ''')
        self.assertEqual(steps, [{'op': 'replace_command', 'from': 'Launcher.exe', 'to': 'Game.exe',
                                  'when': [{'fileMissing': '${install}/modorganizer2'}]}])

    def test_write_file_fix(self):
        steps, coverage, _ = conv('''
            import os
            from protonfixes import util
            def main():
                configpath = os.path.join(util.protonprefix(), 'drive_c/users/steamuser/My Documents/Game')
                if not os.path.exists(configpath):
                    os.makedirs(configpath)
                configgame = os.path.join(configpath, 'settings.ini')
                if not os.path.isfile(configgame):
                    with open(configgame, 'w+', encoding='utf-8') as f:
                        f.write('Language=en')
        ''')
        self.assertEqual(coverage, 'full')
        self.assertEqual(steps[0], {'op': 'file', 'action': 'mkdir', 'to': '${drive_c}/users/${user}/Documents/Game'})
        self.assertEqual(steps[1], {'op': 'write_file', 'path': '${drive_c}/users/${user}/Documents/Game/settings.ini',
                                    'content': 'Language=en', 'ifMissing': True, 'encoding': 'utf-8'})

    def test_legacy_user_folders(self):
        steps, _, _ = conv('''
            from pathlib import Path
            from protonfixes import util
            def main():
                p = f'{util.protonprefix()}/drive_c/users/steamuser/Local Settings/Application Data/X'
                Path(p).mkdir(parents=True, exist_ok=True)
                Path(f'{util.protonprefix()}/drive_c/users/steamuser/Application Data/Y').mkdir()
                Path(f'{util.protonprefix()}/drive_c/users/steamuser/Saved Games/Z').mkdir()
        ''')
        self.assertEqual([s['to'] for s in steps], [
            '${drive_c}/users/${user}/AppData/Local/X',
            '${drive_c}/users/${user}/AppData/Roaming/Y',
            '${drive_c}/users/${user}/Saved Games/Z',
        ])

    def test_append_if_absent(self):
        steps, coverage, _ = conv('''
            import os
            from protonfixes import util
            def main():
                fix_installed = False
                mms_path = os.path.join(util.protonprefix(), 'drive_c/mms.cfg')
                if os.path.isfile(mms_path):
                    with open(mms_path, encoding='utf-8') as f:
                        for line in f:
                            if 'Override' in line:
                                fix_installed = True
                if not fix_installed:
                    with open(mms_path, 'a', encoding='utf-8') as f:
                        f.write('\\n')
                        f.write('Override=1')
        ''')
        self.assertEqual(coverage, 'full')
        self.assertEqual(steps, [{'op': 'append_line', 'path': '${drive_c}/mms.cfg', 'line': 'Override=1',
                                  'ifAbsent': True}])

    def test_dict_lookup_fix(self):
        src = '''
            from protonfixes import util
            NAMES = {'umu-1': 'Warlords', 'umu-2': 'Underworld'}
            def main_with_id(game_id):
                exe_name = NAMES.get(game_id)
                if exe_name is None:
                    return
                util.replace_command(f'{exe_name}.exe', f'{exe_name}_og.exe')
        '''
        s1, c1, _ = conv(src, 'umu-1', 'gog', 'umu-1')
        s2, c2, _ = conv(src, 'umu-2', 'gog', 'umu-2')
        s3, c3, _ = conv(src, 'umu-3', 'gog', 'umu-3')
        self.assertEqual(s1, [{'op': 'replace_command', 'from': 'Warlords.exe', 'to': 'Warlords_og.exe'}])
        self.assertEqual(s2, [{'op': 'replace_command', 'from': 'Underworld.exe', 'to': 'Underworld_og.exe'}])
        self.assertEqual(s3, [])

    def test_ini_and_resolution(self):
        steps, coverage, _ = conv('''
            from protonfixes import util
            def main():
                resolution = util.get_resolution()
                if not resolution:
                    return
                w, h = resolution
                opts = """
                [VIDEO]
                X=""" + str(w) + """
                Y=""" + str(h) + """
                """
                util.set_ini_options(opts, 'system/Gothic.ini', 'cp1251')
        ''')
        self.assertEqual(coverage, 'full')
        self.assertEqual(steps[0]['op'], 'ini')
        self.assertEqual(steps[0]['values'], {'X': '${screen_w}', 'Y': '${screen_h}'})
        self.assertEqual(steps[0]['encoding'], 'cp1251')
        self.assertEqual(steps[0]['path'], '${install}/system/Gothic.ini')

    def test_download_fix(self):
        steps, coverage, _ = conv('''
            import os, shutil, urllib.request, zipfile, hashlib, glob
            from protonfixes import util
            def main():
                install_dir = glob.escape(util.get_game_install_path())
                if not os.path.isfile(os.path.join(install_dir, 'nw.exe')):
                    url = 'https://example.invalid/pkg.zip'
                    hashsum = 'AB12'
                    name = os.path.basename(url)
                    urllib.request.urlretrieve(url, name)
                    with open(name, 'rb') as f:
                        got = hashlib.sha256(f.read()).hexdigest()
                    if hashsum == got:
                        shutil.move(os.path.join(install_dir, 'a.dll'), os.path.join(install_dir, 'a.dll.bak'))
                        with zipfile.ZipFile(name, 'r') as z:
                            z.extractall(install_dir)
        ''')
        self.assertEqual(coverage, 'full')
        self.assertEqual(steps, [{'op': 'download', 'url': 'https://example.invalid/pkg.zip', 'sha256': 'ab12',
                                  'to': '${install}', 'extract': True, 'backup': ['a.dll'],
                                  'when': [{'fileMissing': '${install}/nw.exe'}]}])

    def test_regedit_import(self):
        steps, coverage, _ = conv('''
            import os, subprocess
            from protonfixes import util
            REG = r"""Windows Registry Editor Version 5.00
            [HKEY_CURRENT_USER\\Software\\Wine\\X11 Driver]
            "UseTakeFocus"="N"
            [HKEY_LOCAL_MACHINE\\Software\\Classes\\nxl]
            @="URL:nxl protocol"
            "Size"=dword:00002b48
            """
            def main():
                prefix = util.protonprefix()
                reg_file = os.path.join(str(prefix), 'drive_c', 'x.reg')
                with open(reg_file, 'w', encoding='ascii') as f:
                    f.write(REG)
                env = dict(util.protonmain.g_session.env)
                subprocess.call(['wine', 'regedit', '/S', 'C:\\\\x.reg'], env=env)
                os.unlink(reg_file)
        ''')
        self.assertEqual(coverage, 'full')
        self.assertEqual(steps, [
            {'op': 'registry', 'hive': 'HKCU', 'key': 'Software\\Wine\\X11 Driver',
             'values': {'UseTakeFocus': {'type': 'REG_SZ', 'value': 'N'}}},
            {'op': 'registry', 'hive': 'HKLM', 'key': 'Software\\Classes\\nxl',
             'values': {'': {'type': 'REG_SZ', 'value': 'URL:nxl protocol'},
                        'Size': {'type': 'REG_DWORD', 'value': '11080'}}},
        ])

    def test_unsupported_fix(self):
        steps, coverage, _ = conv('''
            import threading
            from protonfixes import util
            def watch():
                pass
            def main():
                threading.Thread(target=watch, daemon=True).start()
        ''')
        self.assertEqual(coverage, 'none')
        self.assertEqual(steps[0]['op'], 'unsupported')
        self.assertIn('threading.Thread', steps[0]['source'])

    def test_import_saves(self):
        steps, coverage, _ = conv('''
            from protonfixes import util
            def main():
                util.import_saves_folder(2203230, f'My Documents/X/{util.get_steam_account_id()}', True)
                util.import_saves_folder(1881420, 'Saved Games/VG2')
        ''')
        self.assertEqual(coverage, 'full')
        self.assertEqual(steps, [
            {'op': 'import_saves', 'fromAppid': 2203230, 'path': 'Documents/X/${steam_id64}', 'copy': True},
            {'op': 'import_saves', 'fromAppid': 1881420, 'path': 'Saved Games/VG2', 'copy': False},
        ])

    def test_runtime_apps_and_cpu_topology(self):
        steps, coverage, _ = conv('''
            from protonfixes import util
            def main():
                util.install_eac_runtime()
                util.install_battleye_runtime()
                util.set_cpu_topology_limit(16)
                util.set_cpu_topology_nosmt()
        ''')
        self.assertEqual(coverage, 'full')
        self.assertEqual(steps, [
            {'op': 'runtime_app', 'appid': 1826330},
            {'op': 'runtime_app', 'appid': 1161040},
            {'op': 'cpu_topology', 'limit': 16, 'nosmt': False},
            {'op': 'cpu_topology', 'nosmt': True},
        ])

    def test_partial_fix_keeps_supported_steps(self):
        steps, coverage, _ = conv('''
            import subprocess
            from protonfixes import util
            def main():
                util.replace_command('SotV_Launcher.exe', 'hd/SotV1.exe')
                subprocess.call('xrandr --auto', shell=True)
        ''')
        self.assertEqual(coverage, 'partial')
        self.assertEqual(steps[0]['op'], 'replace_command')
        self.assertEqual(steps[1]['op'], 'unsupported')
        self.assertEqual(steps[1]['reason'], 'runs a shell command: xrandr --auto')

    def test_chdir_becomes_cwd(self):
        steps, coverage, _ = conv('''
            import os
            from protonfixes import util
            def main():
                util.replace_command('SotV_Launcher.exe', 'hd/SotV1.exe')
                os.chdir('hd')
        ''')
        self.assertEqual(coverage, 'full')
        self.assertEqual(steps[1], {'op': 'cwd', 'path': '${install}/hd'})

    def test_opaque_condition_makes_dependent_steps_unsupported(self):
        steps, coverage, _ = conv('''
            import os
            from protonfixes import util
            def main():
                if util.get_cpu_count() > 8:
                    util.set_environment('A', '1')
                util.set_environment('B', '1')
        ''')
        self.assertEqual(coverage, 'partial')
        ops = [s['op'] for s in steps]
        self.assertEqual(ops, ['unsupported', 'env'])
        self.assertEqual(steps[0]['step'], {'op': 'env', 'set': {'A': '1'}})
        self.assertNotIn('when', steps[1])

    def test_patch_libcuda_is_a_no_op(self):
        steps, coverage, _ = conv('''
            import os
            from protonfixes import util
            def main():
                if util.patch_libcuda():
                    os.symlink('security.dll', 'x.dll')
                util.protontricks('vcrun2022')
        ''')
        self.assertEqual((steps, coverage), ([{'op': 'verb', 'name': 'vcrun2022'}], 'full'))

    def test_side_effect_in_assignment_is_reported(self):
        steps, coverage, _ = conv('''
            from subprocess import run
            from protonfixes import util
            def main():
                wine = f'{util.protondir()}/files/bin/wine64'
                retc = run([wine, 'start', 'setup.exe'], check=False).returncode
        ''')
        self.assertEqual(coverage, 'none')
        self.assertEqual(steps[0]['op'], 'unsupported')
        self.assertIn('run(', steps[0]['source'])

    def test_checkinstalled_and_lowered_env(self):
        steps, coverage, _ = conv('''
            import os
            from protonfixes import util
            def main():
                if str.lower(os.getenv('NO_X', '')) in ['1', 'yes']:
                    return
                if not util.checkinstalled('cnc_ddraw'):
                    util.protontricks('cnc_ddraw')
        ''')
        self.assertEqual(coverage, 'full')
        self.assertEqual(steps, [{'op': 'verb', 'name': 'cnc_ddraw', 'when': [
            {'not': {'env': 'NO_X', 'equals': '1'}}, {'not': {'env': 'NO_X', 'equals': 'yes'}},
            {'not': {'verbInstalled': 'cnc_ddraw'}}]}])

    def test_replace_command_forms(self):
        f = convert.replace_command_step
        self.assertEqual(f('launcher\\.exe$', 'xblades.exe'),
                         {'op': 'replace_command', 'from': 'launcher\\.exe$', 'to': 'xblades.exe', 'regex': True})
        self.assertEqual(f('Game\\.exe', 'G2.exe'), {'op': 'replace_command', 'from': 'Game.exe', 'to': 'G2.exe'})
        self.assertEqual(f('a+b.exe', 'c.exe'), {'op': 'replace_command', 'from': 'a+b.exe', 'to': 'c.exe', 'regex': True})
        self.assertEqual(f('^-steam$', ''), {'op': 'replace_command', 'from': '^-steam$', 'to': '', 'regex': True})
        self.assertEqual(f('launcher/Launcher.exe', 'binaries/Darktide.exe'),
                         {'op': 'replace_command', 'from': 'Launcher.exe', 'to': '../binaries/Darktide.exe'})
        self.assertEqual(f('-issteam', ''), {'op': 'replace_command', 'from': '-issteam', 'to': ''})
        self.assertEqual(f('Game/Game', 'Game/Game/Bin/G.exe'),
                         {'op': 'replace_command', 'from': 'Game/Game', 'to': 'Game/Game/Bin/G.exe', 'regex': True})

    def test_sed_and_shell_file_ops(self):
        steps, coverage, _ = conv('''
            import glob, os, subprocess
            from protonfixes import util
            def main():
                d = glob.escape(util.get_game_install_path())
                subprocess.call([f'sed -i \\'s/"frame": true/"frame": false/\\' "{d}/package.json"'], shell=True)
                if not os.path.isfile('player.ini'):
                    subprocess.call(['touch', 'player.ini'])
                    with open('player.ini', 'w+', encoding='utf-8') as f:
                        f.write('800 600')
        ''')
        self.assertEqual(coverage, 'full')
        self.assertEqual(steps, [
            {'op': 'text_replace', 'path': '${install}/package.json', 'from': '"frame": true', 'to': '"frame": false',
             'note': 'sed replaces the first match on each line upstream'},
            {'op': 'write_file', 'path': '${install}/player.ini', 'content': '800 600', 'ifMissing': True,
             'encoding': 'utf-8'},
        ])

    def test_xml_options(self):
        steps, coverage, _ = conv('''
            from protonfixes import util
            def main():
                util.set_xml_options('<runtime>', """
                <gcServer enabled = "true" />
            """, 'Bin64/Game.exe.config')
        ''')
        self.assertEqual(coverage, 'full')
        self.assertEqual(steps, [{'op': 'xml', 'path': '${install}/Bin64/Game.exe.config', 'xpath': '//runtime',
                                  'value': '<gcServer enabled = "true" />', 'backup': True}])

    def test_host_user_name(self):
        steps, _, _ = conv('''
            import getpass
            from protonfixes import util
            def main():
                util.set_environment('HOME_DESKTOP', '/home/' + getpass.getuser() + '/Desktop')
        ''')
        self.assertEqual(steps, [{'op': 'env', 'set': {'HOME_DESKTOP': '/home/${user}/Desktop'}}])

    def test_sequencing_hazard_detection(self):
        steps = [
            {'op': 'file', 'action': 'copy', 'from': '${install}/a', 'to': '${install}/a.bak',
             'when': [{'fileMissing': '${install}/a.bak'}]},
            {'op': 'append_line', 'path': '${install}/a', 'line': 'x', 'ifAbsent': True,
             'when': [{'fileMissing': '${install}/a.bak'}]},
        ]
        self.assertEqual(convert.sequencing_hazards(steps), [(0, 1, {'fileMissing': '${install}/a.bak'})])
        self.assertEqual(convert.sequencing_hazards(steps[1:]), [])


class AliasTests(unittest.TestCase):
    def test_symlinked_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            pf = os.path.join(tmp, 'pf')
            os.makedirs(os.path.join(pf, 'gamefixes-steam'))
            os.makedirs(os.path.join(pf, 'gamefixes-gog'))
            with open(os.path.join(pf, 'gamefixes-steam', '100.py'), 'w') as f:
                f.write('"""Game fix for Test Game"""\nfrom protonfixes import util\n'
                        'def main():\n    util.protontricks("d3dx9_43")\n')
            with open(os.path.join(pf, 'gamefixes-steam', 'default.py'), 'w') as f:
                f.write('def main():\n    pass\n')
            os.symlink('100.py', os.path.join(pf, 'gamefixes-steam', '200.py'))
            os.symlink('../gamefixes-steam/100.py', os.path.join(pf, 'gamefixes-gog', 'umu-100.py'))
            db = os.path.join(tmp, 'umu-database.csv')
            with open(db, 'w') as f:
                f.write('TITLE,STORE,CODENAME,UMU_ID,COMMON ACRONYM (Optional),NOTE (Optional),EXE_STRINGS (Optional)\n'
                        'Test Game,gog,1454587428,umu-100,,,\n')
            wt = os.path.join(tmp, 'winetricks')
            with open(wt, 'w') as f:
                f.write(WINETRICKS_SNIPPET)
            out = os.path.join(tmp, 'out')
            docs, verbs = convert.generate(pf, [(wt, 'winetricks')], db, out, 'abc1234', '2026-01-01T00:00:00Z',
                                           'def5678', [], per_file_commits=False)
            with open(os.path.join(out, 'index.json')) as f:
                index = json.load(f)
            self.assertEqual(sorted(index['entries']), ['gog/1454587428', 'steam/100', 'steam/200'])
            with open(os.path.join(out, 'steam', '100.json')) as f:
                canonical = json.load(f)
            with open(os.path.join(out, 'gog', '1454587428.json')) as f:
                alias = json.load(f)
            self.assertEqual(canonical['aliases'], [{'store': 'gog', 'id': '1454587428'},
                                                    {'store': 'steam', 'id': '200'}])
            self.assertEqual(alias['canonical'], {'store': 'steam', 'id': '100'})
            self.assertEqual(alias['umuId'], 'umu-100')
            self.assertEqual(alias['steps'], canonical['steps'])
            self.assertEqual(canonical['name'], 'Test Game')
            self.assertEqual(index['entries']['gog/1454587428']['file'], 'gog/1454587428.json')
            self.assertIn('d3dx9_43', verbs)
            with open(os.path.join(out, 'verbs.json')) as f:
                self.assertEqual(json.load(f)['winetricksCommit'], 'def5678')
            self.assertTrue(os.path.isfile(os.path.join(out, 'REPORT.md')))
            first = index['generatedAt']
            convert.generate(pf, [(wt, 'winetricks')], db, out, 'abc1234', '2026-01-01T00:00:00Z', 'def5678', [],
                             per_file_commits=False)
            with open(os.path.join(out, 'index.json')) as f:
                self.assertEqual(json.load(f)['generatedAt'], first)


class GenerateTests(unittest.TestCase):
    STEAM_DEFAULT = '''import sys
from protonfixes import util


def main() -> None:
    def use_steam_commands() -> None:
        pf_alias_list = list(filter(lambda item: item.startswith('-pf_'), sys.argv))
        for pf_alias in pf_alias_list:
            alias, sep, param = pf_alias.partition('=')
            if sep != '=':
                continue
            sys.argv.remove(pf_alias)
            if alias == '-pf_tricks':
                util.protontricks(param)
            elif alias == '-pf_dxvk_set':
                dxvk_opt, dxvk_sep, dxvk_val = param.partition('=')
                if dxvk_sep == '=':
                    util.set_dxvk_option(dxvk_opt, dxvk_val)

    use_steam_commands()
'''

    def run_generate(self, tmp, overrides):
        pf = os.path.join(tmp, 'pf')
        os.makedirs(os.path.join(pf, 'gamefixes-steam'))
        os.makedirs(os.path.join(pf, 'gamefixes-gog'))
        src = 'import threading\nfrom protonfixes import util\ndef main():\n    threading.Thread(target=main).start()\n'
        with open(os.path.join(pf, 'gamefixes-steam', '100.py'), 'w') as f:
            f.write(src)
        with open(os.path.join(pf, 'gamefixes-steam', '200.py'), 'w') as f:
            f.write(src)
        with open(os.path.join(pf, 'gamefixes-steam', 'default.py'), 'w') as f:
            f.write(self.STEAM_DEFAULT)
        with open(os.path.join(pf, 'gamefixes-gog', 'default.py'), 'w') as f:
            f.write('from protonfixes import util\ndef main():\n    util.set_environment("A", "1")\n')
        db = os.path.join(tmp, 'umu-database.csv')
        with open(db, 'w') as f:
            f.write('TITLE,STORE,CODENAME,UMU_ID\n')
        wt = os.path.join(tmp, 'winetricks')
        with open(wt, 'w') as f:
            f.write(WINETRICKS_SNIPPET)
        ov = os.path.join(tmp, 'overrides')
        for key, data in overrides.items():
            os.makedirs(os.path.join(ov, os.path.dirname(key)), exist_ok=True)
            with open(os.path.join(ov, key + '.json'), 'w') as f:
                json.dump(data, f)
        out = os.path.join(tmp, 'out')
        os.makedirs(os.path.join(out, 'valve'))
        with open(os.path.join(out, 'valve', 'keep.json'), 'w') as f:
            f.write('{}')
        convert.generate(pf, [(wt, 'winetricks')], db, out, 'abc1234', '', 'def5678', [],
                         per_file_commits=False, overrides_dir=ov)
        return src, out

    def test_overrides_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = 'import threading\nfrom protonfixes import util\ndef main():\n    threading.Thread(target=main).start()\n'
            sha = hashlib.sha256(src.encode('utf-8')).hexdigest()
            _, out = self.run_generate(tmp, {
                'steam/100': {'upstreamSha256': sha, 'steps': [{'op': 'env', 'set': {'X': '1'}}]},
                'steam/200': {'upstreamSha256': '0' * 64, 'steps': []},
            })
            with open(os.path.join(out, 'steam', '100.json')) as f:
                applied = json.load(f)
            with open(os.path.join(out, 'steam', '200.json')) as f:
                stale = json.load(f)
            self.assertEqual(applied['steps'], [{'op': 'env', 'set': {'X': '1'}}])
            self.assertEqual(applied['coverage'], 'full')
            self.assertEqual(applied['override'], 'tools/protonfixes/overrides/steam/100.json')
            self.assertEqual(stale['coverage'], 'none')
            self.assertNotIn('override', stale)
            with open(os.path.join(out, 'REPORT.md')) as f:
                report = f.read()
            self.assertIn('| steam/100 | applied | 1 | none → full |', report)
            self.assertIn('| steam/200 | stale | 0 |', report)
            with open(os.path.join(out, 'steam', 'default.json')) as f:
                steam_default = json.load(f)
            self.assertEqual(steam_default['steps'], [])
            self.assertEqual(steam_default['launchOptions'], [
                {'name': '-pf_tricks', 'step': {'op': 'verb', 'name': '{value}'}},
                {'name': '-pf_dxvk_set', 'split': '=', 'step': {'op': 'dxvk_config', 'set': {'{key}': '{value}'}}},
            ])
            with open(os.path.join(out, 'gog', 'default.json')) as f:
                self.assertEqual(json.load(f)['steps'], [{'op': 'env', 'set': {'A': '1'}}])
            with open(os.path.join(out, 'index.json')) as f:
                index = json.load(f)
            self.assertEqual(sorted(index['defaults']), ['gog', 'steam'])
            self.assertNotIn('steam/default', index['entries'])
            self.assertTrue(os.path.isfile(os.path.join(out, 'valve', 'keep.json')))

    def test_bad_override_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self.run_generate(tmp, {'steam/100': {'upstreamSha256': '', 'steps': [{'op': 'nope'}]}})


class VerbParsingTests(unittest.TestCase):
    def setUp(self):
        self.wt = convert.Winetricks()
        self.wt.add_script(WINETRICKS_SNIPPET, 'winetricks')

    def test_dll_verb_from_nested_cabs(self):
        v = self.wt.resolve('d3dx9_43')
        self.assertEqual(v['kind'], 'dll')
        self.assertEqual(v['overrides'], {'d3dx9_43': 'n'})
        self.assertEqual(len(v['files']), 2)
        x86, x64 = v['files']
        self.assertEqual(x86['url'], 'https://example.invalid/directx_Jun2010_redist.exe')
        self.assertEqual(x86['sha256'], '053f')
        self.assertEqual(x86['nested'], ['*d3dx9_43*x86*'])
        self.assertEqual(x86['entry'], 'd3dx9_43.dll')
        self.assertEqual(x86['install'], [{'arch': 'x86', 'name': 'd3dx9_43.dll'}])
        self.assertEqual(x64['nested'], ['*d3dx9_43*x64*'])
        self.assertEqual(x64['install'], [{'arch': 'x64', 'name': 'd3dx9_43.dll'}])

    def test_font_verb(self):
        v = self.wt.resolve('arial')
        self.assertEqual(v['kind'], 'font')
        self.assertEqual(v['files'][0]['entry'], 'Arial*.TTF')
        self.assertEqual(v['files'][0]['install'], [{'arch': 'any', 'name': '*'}])
        self.assertEqual(v['fontRegistry'], {'Arial (TrueType)': 'arial.ttf'})

    def test_settings_verbs(self):
        v = self.wt.resolve('hidewineexports=enable')
        self.assertEqual(v['kind'], 'registry')
        self.assertEqual(v['registry'], [{'hive': 'HKCU', 'key': 'Software\\Wine',
                                          'values': {'HideWineExports': {'type': 'REG_SZ', 'value': 'Y'}}}])
        self.assertEqual(self.wt.resolve('win7'), {'title': 'Set Windows version to Windows 7',
                                                    'category': 'settings', 'source': 'winetricks',
                                                    'kind': 'winver', 'version': 'win7'})

    def test_installer_verbs(self):
        v = self.wt.resolve('vcrun2019')
        self.assertEqual(v['kind'], 'premark')
        self.assertEqual(v['overrides'], {'msvcp140': 'n,b', 'vcruntime140': 'n,b'})
        p = self.wt.resolve('physx')
        self.assertEqual(p['kind'], 'unsupported')
        self.assertIn('installer', p['reason'])
        self.assertEqual(self.wt.resolve('nosuchverb')['kind'], 'unsupported')


if __name__ == '__main__':
    unittest.main()
