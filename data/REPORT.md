# protonfixes import report

- umu-protonfixes commit: `7e57c0cb4e6d5918d3825cfdd5e3cf00d28700a0`
- winetricks commit: `f3890f670867b5ffbc3938726db45c0f7d16c8ba`
- upstream fix files: 468 (354 real files, 114 symlinks; `__init__.py` is not imported; each `default.py` becomes `<store>/default.json`)
- documents written: 468 (354 canonical, 114 aliases)
- coverage: full 468, partial 0, none 0
- coverage from the converter alone (before overrides): full 454, partial 4, none 10
- hand-ported overrides: 13 files, 13 applied to 17 documents
- full documents whose verbs are all supported too: 402

## Documents per store

| store | documents | canonical | aliases | full | partial | none |
|---|---|---|---|---|---|---|
| egs | 26 | 6 | 20 | 26 | 0 | 0 |
| gog | 77 | 27 | 50 | 77 | 0 | 0 |
| humble | 1 | 0 | 1 | 1 | 0 | 0 |
| steam | 313 | 294 | 19 | 313 | 0 | 0 |
| ubisoft | 7 | 0 | 7 | 7 | 0 | 0 |
| umu | 37 | 25 | 12 | 37 | 0 | 0 |
| zoomplatform | 7 | 2 | 5 | 7 | 0 | 0 |

## Unsupported step reasons (canonical documents)

| count | reason |
|---|---|
| 1 | pins the game to the Intel P-cores (taskset) from the host CPU topology |

## Partial and none documents

| document | coverage | name | reasons |
|---|---|---|---|

## Hand-ported overrides (`tools/protonfixes/overrides/`)

| override | status | documents | coverage (converter → override) |
|---|---|---|---|
| steam/1382330 | applied | 1 | none → full |
| steam/1873170 | applied | 1 | full → full |
| steam/2229850 | applied | 1 | full → full |
| steam/2246340 | applied | 1 | none → full |
| steam/257420 | applied | 1 | full → full |
| steam/312060 | applied | 1 | partial → full |
| steam/345610 | applied | 2 | none → full |
| steam/3590 | applied | 1 | partial → full |
| steam/39210 | applied | 1 | partial → full |
| steam/409090 | applied | 1 | partial → full |
| steam/429720 | applied | 1 | none → full |
| steam/452440 | applied | 4 | none → full |
| umu/umu-endfield | applied | 1 | none → full |

`stale` means the upstream file changed since the override was written (its `upstreamSha256` no longer matches), so the converter output is used until the override is reviewed.

## Winetricks verbs

### dll (68)

`cnc_ddraw`*, `crypt32`*, `d3dcompiler_42`*, `d3dcompiler_43`*, `d3dcompiler_46`*, `d3dcompiler_47`*, `d3dx10`*, `d3dx10_43`, `d3dx11_42`, `d3dx11_43`*, `d3dx9`*, `d3dx9_24`, `d3dx9_25`, `d3dx9_26`, `d3dx9_27`, `d3dx9_28`, `d3dx9_29`, `d3dx9_30`, `d3dx9_31`*, `d3dx9_32`, `d3dx9_33`, `d3dx9_34`, `d3dx9_35`, `d3dx9_36`, `d3dx9_37`, `d3dx9_38`, `d3dx9_39`, `d3dx9_40`, `d3dx9_41`*, `d3dx9_42`*, `d3dx9_43`*, `d3dxof`*, `dgvoodoo2`*, `directmusic`*, `directplay`*, `dmband`*, `dmcompos`, `dmime`*, `dmloader`*, `dmscript`, `dmstyle`*, `dmsynth`*, `dmusic`*, `dsound`*, `dswave`*, `gdiplus`*, `l3codecx`*, `mfc100`, `mfc110`, `mfc120`, `mfc140`*, `mfc42`*, `mfc80`*, `mfc90`*, `msxml3`*, `msxml6`, `ole32`*, `oleaut32`*, `quartz`, `ucrtbase2019`*, `urlmon`*, `usp10`*, `vcrun6`, `wininet`*, `xact`*, `xact_x64`*, `xaudio29`*, `xliveless`*

### font (22)

`allfonts`*, `andale`, `arial`*, `cjkfonts`, `comicsans`, `corefonts`*, `courier`*, `fakechinese`*, `fakejapanese`, `fakejapanese_ipamona`*, `fakekorean`, `georgia`, `impact`, `lucida`*, `pptfonts`*, `segoe_script`*, `sourcehansans`*, `tahoma`*, `times`, `trebuchet`*, `verdana`*, `webdings`

### registry (16)

`csmt=force`, `csmt=off`, `csmt=on`, `fontsmooth=bgr`, `fontsmooth=disable`, `fontsmooth=gray`, `fontsmooth=rgb`, `hidewineexports=enable`*, `nocrashdialog`, `renderer=gdi`, `renderer=gl`, `renderer=no3d`, `renderer=vulkan`, `videomemorysize=1024`, `videomemorysize=2048`, `videomemorysize=512`

### winver (9)

`vista`, `win10`*, `win11`, `win2k`, `win7`*, `win8`, `win81`, `win98`, `winxp`*

### premark (9)

`vcrun2005`, `vcrun2008`*, `vcrun2010`*, `vcrun2012`, `vcrun2013`*, `vcrun2015`*, `vcrun2017`*, `vcrun2019`*, `vcrun2022`*

### unsupported (40)

| verb | used by fixes | reason |
|---|---|---|
| cncnet_ra2 | yes | runs an installer under Wine: start.exe /exec /WCACHE/cncnet_ra2/CnCNet5_YR_Installer.exe |
| directx9 | no | winetricks marks it a deprecated no-op; use the individual d3dx verbs |
| dotnet10 | no | runs an installer under Wine: dotnet-runtime-10.0.0-win-x86.exe /quiet |
| dotnet20 | yes | runs an installer under Wine: NetFx64.exe /q /c:install.exe /q |
| dotnet20sp2 | no | runs an installer under Wine: WINE NetFx20SP2_x86.exe /q |
| dotnet35 | yes | runs an installer under Wine: WINE dotnetfx35.exe /lang:ENU |
| dotnet35sp1 | yes | runs an installer under Wine: WINE dotnetfx35.exe /lang:ENU |
| dotnet40 | yes | runs an installer under Wine: WINE dotNetFx40_Full_x86_x64.exe /q |
| dotnet45 | yes | runs an installer under Wine: WINE dotnetfx45_full_x86_x64.exe /q |
| dotnet452 | no | runs an installer under Wine: WINE NDP452-KB2901907-x86-x64-AllOS-ENU.exe /q |
| dotnet462 | yes | runs an installer under Wine: WINE NDP462-KB3151800-x86-x64-AllOS-ENU.exe /sfxlang:1027 |
| dotnet472 | no | runs an installer under Wine: WINE NDP472-KB4054530-x86-x64-AllOS-ENU.exe /sfxlang:1027 |
| dotnet48 | yes | runs an installer under Wine: WINE ndp48-x86-x64-allos-enu.exe /sfxlang:1027 |
| dotnet6 | no | runs an installer under Wine: dotnet-runtime-6.0.36-win-x86.exe /quiet |
| dotnet7 | no | runs an installer under Wine: dotnet-runtime-7.0.20-win-x86.exe /quiet |
| dotnet8 | no | runs an installer under Wine: dotnet-runtime-8.0.12-win-x86.exe /quiet |
| dotnet9 | no | runs an installer under Wine: dotnet-runtime-9.0.14-win-x86.exe /quiet |
| dotnetcore3 | yes | runs an installer under Wine: dotnet-runtime-3.1.10-win-x86.exe /quiet |
| dotnetdesktop10 | yes | runs an installer under Wine: windowsdesktop-runtime-10.0.0-win-x86.exe /quiet |
| dotnetdesktop6 | no | runs an installer under Wine: windowsdesktop-runtime-6.0.36-win-x86.exe /quiet |
| dotnetdesktop8 | yes | runs an installer under Wine: windowsdesktop-runtime-8.0.12-win-x86.exe /quiet |
| dotnetdesktop9 | yes | runs an installer under Wine: windowsdesktop-runtime-9.0.7-win-x86.exe /quiet |
| eossdk | yes | the EOS SDK archive is 580 MB and its installer only adds the Epic overlay service |
| gui | yes | opens the interactive winetricks GUI |
| hidewineexports=disable | no | needs the wine-staging HideWineExports patch, which our Wine builds do not have |
| icodecs | yes | the Indeo codec installer needs a GUI and registers drivers under Wine |
| ie8 | yes | Internet Explorer 8 needs its own installer and the Windows 7 SP1 update |
| mdx | yes | Managed DirectX needs its assemblies registered in the .NET GAC by the DirectX installer |
| msxml4 | no | runs an installer under Wine: msiexec /i msxml.msi |
| openal | yes | runs an installer under Wine: /WTMP/oalinst.exe /silent |
| physx | yes | runs an installer under Wine: PhysX_9.23.1019_SystemSoftware.exe /s |
| powershell | yes | PowerShell Core needs its MSI run under Wine |
| sound=alsa | yes | audio driver is fixed on Android |
| sound=disabled | no | audio driver is fixed on Android |
| sound=pulse | no | audio driver is fixed on Android |
| ubisoftconnect | yes | runs an installer under Wine: UbisoftConnectInstaller.exe /S |
| videomemorysize=default | no | registry: registry value deletion |
| webview2 | yes | the WebView2 runtime installer does not work under Wine on Android |
| xna31 | yes | runs an installer under Wine: msiexec /quiet /i |
| xna40 | yes | runs an installer under Wine: msiexec /quiet /i |

`*` = referenced by at least one fix.

## Non-Steam fixes without a store id in umu-database (umu id used as id)

- gamefixes-zoomplatform/umu-12810.py
- gamefixes-egs/umu-50620.py
- gamefixes-gog/umu-50620.py
- gamefixes-gog/umu-955050.py
