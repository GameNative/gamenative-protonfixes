"""Sort winetricks verbs into the action classes the app runs, with the data each class needs.

`kind` keeps its old meaning so older app builds keep working; newer builds read `class`:

- component: install a wincomponent the app already ships, then apply the verb's overrides
  (and, for DirectPlay/DirectMusic, the regsvr32 calls winetricks makes).
- registry: Windows version, audio driver and other settings written straight to the prefix.
- download: official files with pinned sha256, unpacked into the prefix.
- offline_installer: an official installer laid out from data/verbs/<verb>.layout.json
  (made by tools/verbs/msi_tables.py), never run.
- exec: an official installer run once per prefix under Wine with a watchdog.
- skip: not applied; `reason` says why.
"""

import hashlib
import json
import os
import re
import shlex

CLASSES = ('component', 'registry', 'download', 'offline_installer', 'exec', 'skip')
HERE = os.path.dirname(os.path.abspath(__file__))
PINS_PATH = os.path.join(HERE, '..', 'verbs', 'pins.json')
LAYOUT_DIR = os.path.join(HERE, '..', '..', 'data', 'verbs')

EXTRA_VERBS = [
    'fontsmooth=rgb', 'fontsmooth=bgr', 'fontsmooth=gray', 'fontsmooth=disable',
    'csmt=on', 'csmt=off', 'csmt=force', 'nocrashdialog', 'renderer=vulkan',
    'dotnet10', 'dotnet20sp2', 'msxml4', 'xna31', 'xna40', 'dotnetdesktop10', 'cncnet_ra2',
]

DIRECT3D = (['d3dcompiler_%d' % n for n in (42, 43, 46, 47)] + ['d3dx9', 'd3dx10', 'd3dx10_43', 'd3dx11_42', 'd3dx11_43']
            + ['d3dx9_%d' % n for n in range(24, 44)])
DIRECTMUSIC = ['directmusic', 'dmband', 'dmcompos', 'dmime', 'dmloader', 'dmscript', 'dmstyle', 'dmsynth', 'dmusic',
               'dmusic32', 'dswave']
COREFONTS = ['corefonts', 'andale', 'arial', 'comicsans', 'courier', 'georgia', 'impact', 'times', 'trebuchet',
             'verdana', 'webdings']
COMPONENTS = dict(
    [(v, 'direct3d') for v in DIRECT3D]
    + [(v, 'directmusic') for v in DIRECTMUSIC]
    + [(v, 'corefonts') for v in COREFONTS]
    + [('xact', 'xaudio'), ('xact_x64', 'xaudio'), ('dsound', 'directsound'), ('directplay', 'directplay'),
       ('vcrun2010', 'vcrun2010'), ('openal', 'openal'), ('cnc_ddraw', 'cnc-ddraw'), ('quartz', 'directshow')]
)
REGSVR_COMPONENTS = {'directplay', 'directmusic'}

D3DX_NUGET = {
    'url': 'https://api.nuget.org/v3-flatcontainer/microsoft.dxsdk.d3dx/9.29.952.8/microsoft.dxsdk.d3dx.9.29.952.8.nupkg',
    'sha256': 'ead0906ae8a26c18a7525da7490127a2110f7c58f18293738283e30e97c6ea4b',
}
D3DX_NUGET_DLLS = {
    'd3dcompiler_43': 'D3DCompiler_43.dll', 'd3dx9_43': 'D3DX9_43.dll',
    'd3dx10_43': 'd3dx10_43.dll', 'd3dx11_43': 'd3dx11_43.dll',
}

OFFLINE = {
    'vcrun2005', 'vcrun2012', 'vcrun2015', 'vcrun2017', 'vcrun2019', 'vcrun2022',
    'dotnet20', 'dotnet20sp2', 'dotnet35', 'dotnet35sp1', 'dotnet40', 'dotnet45', 'dotnet452', 'dotnet462',
    'dotnet472', 'dotnet48', 'dotnetcore3', 'dotnet6', 'dotnet7', 'dotnet8', 'dotnet9', 'dotnet10',
    'dotnetdesktop6', 'dotnetdesktop8', 'dotnetdesktop9', 'dotnetdesktop10', 'xna31', 'xna40', 'msxml4',
}
DOTNET_FRAMEWORK = {'dotnet20', 'dotnet20sp2', 'dotnet35', 'dotnet35sp1', 'dotnet40', 'dotnet45', 'dotnet452',
                    'dotnet462', 'dotnet472', 'dotnet48'}
NDP4 = {
    'dotnet40': ('4.0.30319', None),
    'dotnet45': ('4.5.50709', 378389),
    'dotnet452': ('4.5.51209', 379893),
    'dotnet462': ('4.6.01590', 394802),
    'dotnet472': ('4.7.03062', 461808),
    'dotnet48': ('4.8.03761', 528040),
}
REQUIRES = {
    'xna40': [['dotnet40', 'dotnet45', 'dotnet452', 'dotnet462', 'dotnet472', 'dotnet48']],
    'xna31': [['dotnet20sp2', 'dotnet35sp1']],
}
MSI_WINXP = {'VersionNT': '501', 'VersionNT64': '502', 'WindowsBuild': '3790', 'ServicePackLevel': '2'}
MSI_WIN7 = {'VersionNT': '601', 'VersionNT64': '601', 'WindowsBuild': '7601', 'ServicePackLevel': '1'}
MSI_PROPERTIES = dict(
    [(v, MSI_WINXP) for v in ('dotnet20', 'dotnet20sp2', 'dotnet35', 'dotnet35sp1', 'dotnet40')]
    + [(v, MSI_WIN7) for v in ('dotnet45', 'dotnet452', 'dotnet462', 'dotnet472', 'dotnet48')]
)
WAYBACK = re.compile(r'^(https?://web\.archive\.org/web/\d+)(/)')
VC_BUILTIN = {'files': ['msvcp140*.dll'], 'overrides': ['msvcp140*'], 'env': 'PROTONFIX_VC2022_NATIVE'}
VC_RUNTIMES_KEY = 'Software\\Microsoft\\VisualStudio\\14.0\\VC\\Runtimes\\%s'

EXEC_ARGS = {
    'vcrun2008': ['/qb!'],
    'vcrun2013': ['/install', '/passive', '/norestart'],
    'ubisoftconnect': ['/S'],
    'cncnet_ra2': ['/silent', '/verysilent', '/norestart', '/suppressmsgboxes'],
    'openal': ['/silent'],
    'physx': ['/s'],
}
EXEC_ENTRY = {'openal': 'oalinst.exe'}
EXEC_TIMEOUT = {'ubisoftconnect': 1200}
EXEC_GAME_COPY = {'ubisoftconnect'}

SKIP = {
    'gdiplus': 'taken from the Windows 7 SP1 update (several hundred MB); Wine\'s builtin is used',
    'usp10': 'taken from the Windows 7 SP1 update (several hundred MB); Wine\'s builtin is used',
    'oleaut32': 'taken from the Windows 7 SP1 update (several hundred MB); Wine\'s builtin is used',
    'crypt32': 'taken from the Windows 7 SP1 update (several hundred MB); Wine\'s builtin is used',
    'wininet': 'taken from the Windows 7 SP1 update (several hundred MB); Wine\'s builtin is used',
    'urlmon': 'taken from the Windows 7 SP1 update (several hundred MB); Wine\'s builtin is used',
    'msxml3': 'the 64-bit DLL is taken from the Windows 7 SP1 update; Wine\'s builtin is used',
    'ie8': 'Internet Explorer 8 needs its own installer and the Windows 7 SP1 update',
    'icodecs': 'the Indeo codec installer needs a GUI and registers drivers under Wine',
    'webview2': 'the WebView2 runtime installer does not work under Wine on Android',
    'gui': 'opens the interactive winetricks GUI',
    'allfonts': 'installs every winetricks font (over 1 GB of downloads)',
    'powershell': 'PowerShell Core needs its MSI run under Wine',
    'hidewineexports=enable': 'needs the wine-staging HideWineExports patch, which our Wine builds do not have',
    'hidewineexports=disable': 'needs the wine-staging HideWineExports patch, which our Wine builds do not have',
    'eossdk': 'the EOS SDK archive is 580 MB and its installer only adds the Epic overlay service',
    'mdx': 'Managed DirectX needs its assemblies registered in the .NET GAC by the DirectX installer',
    'directx9': 'winetricks marks it a deprecated no-op; use the individual d3dx verbs',
    'sound=oss': 'not an audio driver on Android',
    'sound=coreaudio': 'not an audio driver on Android',
}
AUDIO_DRIVERS = {'sound=alsa': 'alsa', 'sound=pulse': 'pulseaudio', 'sound=disabled': 'disabled'}
REGISTRY_REMOVALS = {
    'videomemorysize=default': [{'hive': 'HKCU', 'key': 'Software\\Wine\\Direct3D', 'names': ['VideoMemorySize']}],
}
WINVER_VERBS = {'win11', 'win10', 'win81', 'win8', 'win7', 'vista', 'winxp', 'win2k', 'win98'}
OVERRIDE_MODES = {'native': 'n', 'builtin': 'b', 'native,builtin': 'n,b', 'builtin,native': 'b,n', 'disabled': 'd'}
SHA256 = re.compile(r'^[0-9a-fA-F]{64}$')


def load_pins(path=None):
    path = path or PINS_PATH
    try:
        with open(path, encoding='utf-8') as f:
            return {k: v.lower() for k, v in json.load(f).get('sha256', {}).items()}
    except (OSError, ValueError):
        return {}


def function_lines(wt, verb):
    body = wt.funcs.get('load_' + verb.split('=', 1)[0]) if wt is not None else None
    lines = []
    buf = ''
    for line in body or []:
        stripped = line.strip()
        if stripped.startswith('#'):
            continue
        if stripped.endswith('\\'):
            buf += stripped[:-1] + ' '
            continue
        lines.append(buf + stripped)
        buf = ''
    if buf:
        lines.append(buf)
    return lines


def _tokens(line):
    try:
        return shlex.split(line, posix=True, comments=True)
    except ValueError:
        return []


def _arch_of(url):
    name = url.split('?')[0].rsplit('/', 1)[-1].lower()
    is64 = re.search(r'(x64|amd64|win64)', name) is not None
    is32 = re.search(r'(x86|win32)', name) is not None
    if is64 != is32:
        return 'x64' if is64 else 'x86'
    return 'any'


def downloads(lines):
    out = []
    for line in lines:
        tokens = _tokens(line)
        if tokens[:1] == ['w_try']:
            tokens = tokens[1:]
        if not tokens or tokens[0] not in ('w_download', 'w_download_to'):
            continue
        args = tokens[2:] if tokens[0] == 'w_download_to' else tokens[1:]
        if not args or not args[0].startswith('http'):
            continue
        sha = args[1].lower() if len(args) > 1 and SHA256.match(args[1]) else None
        entry = {'url': args[0], 'sha256': sha, 'arch': _arch_of(args[0])}
        if entry not in out:
            out.append(entry)
    return out


def overrides(lines):
    out = {}
    for line in lines:
        tokens = _tokens(line)
        if len(tokens) >= 3 and tokens[0] == 'w_override_dlls' and tokens[1] in OVERRIDE_MODES:
            for dll in tokens[2:]:
                out[dll] = OVERRIDE_MODES[tokens[1]]
    return out


def reg_adds(lines):
    out = []
    types = {'REG_DWORD': 'REG_DWORD', 'REG_SZ': 'REG_SZ', 'REG_EXPAND_SZ': 'REG_EXPAND_SZ'}
    for line in lines:
        tokens = _tokens(line)
        if len(tokens) < 4 or tokens[1:3] != ['reg', 'add']:
            continue
        path = tokens[3].replace('\\\\', '\\')
        hive, _, key = path.partition('\\')
        hive = {'HKLM': 'HKLM', 'HKEY_LOCAL_MACHINE': 'HKLM', 'HKCU': 'HKCU', 'HKEY_CURRENT_USER': 'HKCU'}.get(hive.upper())
        opts = {flag: tokens[i + 1] for i, flag in enumerate(tokens[:-1]) if flag in ('/v', '/t', '/d')}
        name, kind, data = opts.get('/v'), types.get(opts.get('/t', 'REG_SZ')), opts.get('/d')
        if hive and name and kind and data is not None:
            if kind == 'REG_DWORD':
                data = str(int(data, 0) if not data.isdigit() else int(data))
            out.append({'hive': hive, 'key': key, 'values': {name: {'type': kind, 'value': data}}})
    return out


def _with_pins(entries, pins):
    for entry in entries:
        if not entry.get('sha256') and entry.get('url') in pins:
            entry['sha256'] = pins[entry['url']]
        entry['url'] = raw_url(entry.get('url', ''))
    return entries


def raw_url(url):
    """A Wayback Machine URL in its `id_` form, which serves the archived bytes unchanged."""
    return WAYBACK.sub(lambda m: m.group(1) + 'id_/', url) if url and 'id_/' not in url else url


def _nuget_files(verb):
    dll = D3DX_NUGET_DLLS.get(verb)
    if not dll:
        return None
    return [
        {'url': D3DX_NUGET['url'], 'sha256': D3DX_NUGET['sha256'], 'formats': ['zip'],
         'entry': 'build/native/release/bin/%s/%s' % (arch, dll), 'install': [{'arch': arch, 'name': verb + '.dll'}]}
        for arch in ('x86', 'x64')
    ]


def _regsvr(resolved):
    seen = []
    for item in resolved.get('regsvr32', []):
        if item not in seen:
            seen.append(item)
    return seen


def _layout_sha(verb, layout_dir):
    path = os.path.join(layout_dir, '%s.layout.json' % verb)
    if not os.path.isfile(path):
        return None
    with open(path, 'rb') as f:
        return {'path': 'verbs/%s.layout.json' % verb, 'sha256': hashlib.sha256(f.read()).hexdigest()}


def classify(name, resolved, wt=None, pins=None, layout_dir=None):
    """Adds `class` and that class's data to one resolved verb; never drops what older builds read."""
    pins = pins if pins is not None else {}
    layout_dir = layout_dir or LAYOUT_DIR
    verb = dict(resolved)
    _with_pins(verb.get('files', []), pins)
    lines = function_lines(wt, name)
    if name in SKIP:
        verb.update({'class': 'skip', 'reason': SKIP[name]})
        return verb
    if name in COMPONENTS:
        verb['class'] = 'component'
        verb['component'] = COMPONENTS[name]
        regsvr = _regsvr(resolved)
        if verb['component'] in REGSVR_COMPONENTS and regsvr:
            verb['regsvr32'] = regsvr
        else:
            verb.pop('regsvr32', None)
        preferred = _nuget_files(name)
        if preferred:
            verb['preferred'] = preferred
        if name == 'openal':
            verb['exec'] = _exec_entries(name, lines, pins)
            verb['overrides'] = {'openal32': 'n,b'}
        return verb
    if name in AUDIO_DRIVERS:
        verb.update({'class': 'registry', 'setting': {'audioDriver': AUDIO_DRIVERS[name]}})
        return verb
    if name in REGISTRY_REMOVALS:
        verb.update({'class': 'registry', 'remove': REGISTRY_REMOVALS[name]})
        return verb
    if name in WINVER_VERBS:
        verb.update({'class': 'registry', 'version': verb.get('version') or name})
        return verb
    if name in OFFLINE:
        return _offline(name, verb, lines, pins, layout_dir)
    if name in EXEC_ARGS:
        verb['class'] = 'exec'
        verb['exec'] = _exec_entries(name, lines, pins)
        found = overrides(lines)
        if found and not verb.get('overrides'):
            verb['overrides'] = found
        if EXEC_TIMEOUT.get(name):
            verb['timeoutSec'] = EXEC_TIMEOUT[name]
        return verb
    kind = verb.get('kind')
    if kind in ('dll', 'font'):
        verb['class'] = 'download'
    elif kind in ('registry', 'winver'):
        verb['class'] = 'registry'
    else:
        verb['class'] = 'skip'
        verb.setdefault('reason', 'not supported')
    return verb


def _exec_entries(name, lines, pins):
    out = []
    for d in _with_pins(downloads(lines), pins):
        entry = {'url': d['url'], 'sha256': d['sha256'], 'arch': d['arch'], 'args': EXEC_ARGS.get(name, [])}
        if name in EXEC_ENTRY:
            entry['entry'] = EXEC_ENTRY[name]
        if name in EXEC_GAME_COPY:
            entry['gameCopy'] = True
        out.append(entry)
    return out


def _offline(name, verb, lines, pins, layout_dir):
    verb['class'] = 'offline_installer'
    verb['installers'] = [{'url': d['url'], 'sha256': d['sha256'], 'arch': d['arch']}
                          for d in _with_pins(downloads(lines), pins)]
    found = overrides(lines)
    if found:
        verb['overrides'] = found
    registry = reg_adds(lines)
    if name in NDP4:
        version, release = NDP4[name]
        values = {'Install': {'type': 'REG_DWORD', 'value': '1'}, 'Version': {'type': 'REG_SZ', 'value': version},
                  'TargetVersion': {'type': 'REG_SZ', 'value': '4.0.0'}}
        if release:
            values['Release'] = {'type': 'REG_DWORD', 'value': str(release)}
        for view in ('Software', 'Software\\Wow6432Node'):
            registry.append({'hive': 'HKLM', 'key': view + '\\Microsoft\\NET Framework Setup\\NDP\\v4\\Full',
                             'values': dict(values)})
    if registry:
        verb['registry'] = registry
    if name in DOTNET_FRAMEWORK:
        verb['removeMono'] = True
        verb.setdefault('overrides', {})['mscoree'] = 'n'
    match = re.match(r'^vcrun(\d{4})$', name)
    if match and int(match.group(1)) >= 2015:
        verb['builtinUnless'] = dict(VC_BUILTIN)
        installed = {'Installed': {'type': 'REG_DWORD', 'value': '1'}, 'Major': {'type': 'REG_DWORD', 'value': '14'}}
        verb['fallbackRegistry'] = [{'hive': 'HKLM', 'key': VC_RUNTIMES_KEY % arch, 'values': dict(installed)}
                                    for arch in ('x86', 'x64')]
    if name in REQUIRES:
        verb['requires'] = REQUIRES[name]
    if name in MSI_PROPERTIES:
        verb['msiProperties'] = dict(MSI_PROPERTIES[name])
    layout = _layout_sha(name, layout_dir)
    if layout:
        verb['layout'] = layout
    return verb


def classify_all(verbs, wt=None, pins=None, layout_dir=None):
    pins = load_pins() if pins is None else pins
    return {name: classify(name, v, wt, pins, layout_dir) for name, v in verbs.items()}


def unpinned(verbs):
    """Every download URL in the table that has no sha256."""
    out = []
    for name, verb in sorted(verbs.items()):
        if verb.get('class') == 'skip':
            continue
        for group in ('files', 'preferred', 'installers', 'exec'):
            for entry in verb.get(group, []):
                if entry.get('url') and not (entry.get('sha256') and SHA256.match(entry['sha256'])):
                    out.append((name, entry['url']))
    return out
