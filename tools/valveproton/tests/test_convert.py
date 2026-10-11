import importlib.util
import json
import os
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location('valve_convert', os.path.join(HERE, '..', 'convert.py'))
convert = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(convert)

SAMPLE = textwrap.dedent('''
    import os
    import platform

    def default_compat_config():
        ret = set()
        if "SteamAppId" in os.environ:
            appid = os.environ["SteamAppId"]
            if appid in [
                    "100", #Game One - CW bug 1
                    "200", #Game Two
                    ]:
                ret.add("heapdelayfree")
                ret.add("noopwr")

            if appid in ["300"]:
                try:
                    with open('/proc/modules') as f:
                        if not f:
                            ret.add("disablenvapi")
                except OSError:
                    ret.add("disablenvapi")

        ret.add("gamedrive")

        if "STEAM_COMPAT_APP_ID" in os.environ:
            appid = os.environ["STEAM_COMPAT_APP_ID"]
            if appid in ["400"]:
                ret.add("nohardwarescheduling")
        return ret

    default_cpu_limit = {
        "500" : "8",  # Five Hundred
        "600" : "P",  # Six Hundred
    }

    fex_application_profiles = {
        "700" : { # Seven
            "setup*": {"X87ReducedPrecision": "0"}
        }
    }

    class Session:
        def __init__(self):
            self.env = dict(os.environ)
            self.dlloverrides = {
                "steam.exe": "b",
                "winebth.sys": "d",
            }
            if os.environ.get("SteamGameId", 0) == "800":
                del self.dlloverrides["steam.exe"]
            if os.environ.get("SteamGameId", 0) not in ["900"]:
                self.dlloverrides["opencl"] = "n,d"
            if "WINE_HIDE_AMD_GPU" not in self.env and appid in ["1000"]:
                self.env["WINE_HIDE_AMD_GPU"] = "1"

        def init_wine(self):
            if os.environ.get("PROTON_MEDIACONV_NO_VIDEO",
                "1" if os.environ.get("SteamGameId", 0) in (
                    "1100", # Eleven
                ) else "0") != "0":
                self.env["X"] = "a"

        def init_session(self):
            if os.environ.get("SteamGameId", 0) in ["1200"]:
                self.env["OPENSSL_ia32cap"] = "~0x20000000"
            if os.environ.get("SteamGameId", 0) == "1300":
                self.dlloverrides["gameinput"] = "d"
            if "PROTON_LIMIT_RESOLUTIONS" not in self.env:
                if os.environ.get("SteamGameId", 0) in ["1400"]:
                    self.env["PROTON_LIMIT_RESOLUTIONS"] = "16"
                elif os.environ.get("SteamGameId", 0) in ["1500"]:
                    self.env["PROTON_LIMIT_RESOLUTIONS"] = "32"
            if (os.environ.get("SteamGameId", 0) == "1600" and
                os.environ.get("SteamDeck", 0) == "1"):
                self.env["PROTON_DISABLE_HIDRAW"] = "0x054C/0x05C4"
            if os.environ.get("SteamGameId", 0) == "1700":
                self.env.setdefault("WINE_MONO_HIDETYPES", "1")
            if platform.machine() == 'aarch64':
                if os.environ.get("SteamGameId", 0) == "1800":
                    self.env["MIMALLOC_DISABLE_REDIRECT"] = "1"

        def run(self):
            if os.environ.get("SteamGameId", 0) in ["1900"]:
                argv = ["steam.exe"]
            else:
                argv = ["other"]

    class CompatData:
        def create_fonts_symlinks(self):
            ALTERNATIVES = {
                ('2000', 'arial.ttf'),    # Twenty
            }

        def setup(self):
            if os.environ.get("SteamGameId", 0) == "2100":
                srcdir = os.path.join("a", "b")
                for x in os.listdir(srcdir):
                    pass
''')


def docs():
    col, defaults = convert.collect(SAMPLE)
    return convert.build_docs(col), col, defaults


def steps(d, appid):
    return d[appid]['steps']


class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.docs, self.col, self.defaults = docs()

    def test_compat_flags_and_desktop_marking(self):
        s = steps(self.docs, '100')
        self.assertEqual(s[0], {'op': 'compat_config', 'flags': ['heapdelayfree', 'noopwr'], 'desktopOnly': ['noopwr']})
        self.assertEqual(self.docs['100']['name'], 'Game One')
        self.assertEqual(self.docs['200']['name'], 'Game Two')
        self.assertEqual(steps(self.docs, '400')[0]['flags'], ['nohardwarescheduling'])

    def test_conditional_flag_keeps_note(self):
        s = steps(self.docs, '300')[0]
        self.assertEqual(s['flags'], ['disablenvapi'])
        self.assertIn('disablenvapi', s['flagNotes'])
        self.assertEqual(self.docs['300']['coverage'], 'none')

    def test_global_flags_and_default_dlls(self):
        self.assertEqual(self.defaults['compatFlags'], ['gamedrive'])
        self.assertEqual(self.defaults['dllOverrides'], {'steam.exe': 'b', 'winebth.sys': 'd'})
        self.assertNotIn('gamedrive', json.dumps(self.docs))

    def test_cpu_limits(self):
        self.assertEqual(steps(self.docs, '500'), [{'op': 'cpu_topology', 'limit': 8}])
        p = steps(self.docs, '600')[0]
        self.assertEqual((p['op'], p['value']), ('cpu_topology', 'P'))
        self.assertNotIn('limit', p)
        self.assertEqual(self.docs['600']['name'], 'Six Hundred')

    def test_fex_profile(self):
        s = steps(self.docs, '700')[0]
        self.assertEqual(s['op'], 'fex_app_config')
        self.assertEqual(s['appOverrides'], {'setup*': {'X87ReducedPrecision': '0'}})

    def test_session_tables(self):
        self.assertEqual(steps(self.docs, '1200'), [{'op': 'env', 'set': {'OPENSSL_ia32cap': '~0x20000000'}}])
        self.assertEqual(steps(self.docs, '1300'), [{'op': 'dll_override', 'overrides': {'gameinput': 'd'}}])
        self.assertEqual(steps(self.docs, '1400'), [{'op': 'env', 'set': {'PROTON_LIMIT_RESOLUTIONS': '16'}}])
        self.assertEqual(steps(self.docs, '1500'), [{'op': 'env', 'set': {'PROTON_LIMIT_RESOLUTIONS': '32'}}])
        self.assertEqual(steps(self.docs, '1700'), [{'op': 'env', 'set': {'WINE_MONO_HIDETYPES': '1'}}])

    def test_aarch64_only_env(self):
        s = steps(self.docs, '1800')
        self.assertEqual(s, [{'op': 'env', 'set': {'MIMALLOC_DISABLE_REDIRECT': '1'}, 'note': 'aarch64 hosts only'}])

    def test_desktop_only_and_conditions_are_unsupported(self):
        self.assertEqual(steps(self.docs, '800')[0]['op'], 'unsupported')
        self.assertTrue(steps(self.docs, '800')[0]['reason'].startswith('desktop-only'))
        gpu = steps(self.docs, '1000')[0]
        self.assertEqual(gpu['op'], 'unsupported')
        self.assertEqual(gpu['step'], {'op': 'env', 'set': {'WINE_HIDE_AMD_GPU': '1'}})
        deck = steps(self.docs, '1600')[0]
        self.assertTrue(deck['reason'].startswith('condition not expressible'))
        media = steps(self.docs, '1100')[0]
        self.assertEqual(media['step']['set'], {'PROTON_MEDIACONV_NO_VIDEO': '1'})
        self.assertEqual(self.docs['1100']['name'], 'Eleven')
        self.assertTrue(steps(self.docs, '1900')[0]['reason'].startswith('desktop-only'))
        self.assertEqual(steps(self.docs, '2000')[0]['op'], 'unsupported')
        self.assertEqual(steps(self.docs, '2100')[0]['reason'], 'not convertible')

    def test_all_except_is_reported(self):
        self.assertEqual([u['ids'] for u in self.col.unparsed if u['kind'] == 'all-except'], [['900']])
        self.assertNotIn('900', self.docs)

    def test_generate_writes_index_and_prunes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'valve')
            os.makedirs(out)
            with open(os.path.join(out, '1.json'), 'w') as f:
                f.write('{}')
            d, index = convert.generate(SAMPLE, out, 'proton_11.0', 'abc', None)
            self.assertFalse(os.path.exists(os.path.join(out, '1.json')))
            entry = index['entries']['steam/100']
            self.assertEqual(entry['file'], 'valve/100.json')
            with open(os.path.join(out, '100.json'), encoding='utf-8') as f:
                data = f.read()
            import hashlib
            self.assertEqual(entry['sha256'], hashlib.sha256(data.encode('utf-8')).hexdigest())
            first = index['generatedAt']
            _, again = convert.generate(SAMPLE, out, 'proton_11.0', 'abc', None)
            self.assertEqual(again['generatedAt'], first)
            doc = json.loads(data)
            self.assertEqual((doc['schema'], doc['store'], doc['id']), (1, 'steam', '100'))

    def test_never_executes_input(self):
        hostile = 'raise SystemExit(3)\n' + SAMPLE
        col, _ = convert.collect(hostile)
        self.assertIn('100', col.apps)


if __name__ == '__main__':
    unittest.main()
