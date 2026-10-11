# Skipped and not-yet-working items

Generated from data/ on 2026-10-11. Re-check each item when the blocker changes.

## Verbs the app skips on purpose

| verb | reason | used by |
|---|---|---|
| allfonts | installs every winetricks font (over 1 GB of downloads) | steam/1259970 |
| directx9 | winetricks marks it a deprecated no-op; use the individual d3dx verbs |  |
| eossdk | the EOS SDK archive is 580 MB and its installer only adds the Epic overlay service | steam/252950 |
| gui | opens the interactive winetricks GUI | umu/winetricks-gui |
| hidewineexports=disable | needs the wine-staging HideWineExports patch, which our Wine builds do not have |  |
| hidewineexports=enable | needs the wine-staging HideWineExports patch, which our Wine builds do not have | gog/2053169534, steam/230410, steam/292030, steam/333980, steam/3669060, steam/633230, steam/678950, steam/816020 |
| icodecs | the Indeo codec installer needs a GUI and registers drivers under Wine | gog/1207658883, humble/ageofwonders, steam/356500, steam/61500, steam/6270, zoomplatform/08d0018e-5ee1-40b7-95ff-35a06ba69191 |
| ie8 | Internet Explorer 8 needs its own installer and the Windows 7 SP1 update | umu/umu-heroesofnewerthreborn |
| mdx | Managed DirectX needs its assemblies registered in the .NET GAC by the DirectX installer | steam/200940 |
| powershell | PowerShell Core needs its MSI run under Wine | umu/umu-starcitizen |
| webview2 | the WebView2 runtime installer does not work under Wine on Android | egs/4d0ff75b922447649057c237c0bd1545, umu/umu-306130, umu/umu-zwift |

## Installer verbs without an offline layout (fail and log on device)

| verb | note | used by |
|---|---|---|
| dotnet20 | runs an installer under Wine: NetFx64.exe /q /c:install.exe /q | gog/1207664613, steam/269330 |
| dotnet20sp2 | runs an installer under Wine: WINE NetFx20SP2_x86.exe /q |  |
| dotnet35 | runs an installer under Wine: WINE dotnetfx35.exe /lang:ENU | steam/1695793 |
| dotnet35sp1 | runs an installer under Wine: WINE dotnetfx35.exe /lang:ENU | steam/1112400, steam/23460 |

## Steps marked unsupported inside documents

| document | reason |
|---|---|
| steam/1097150 | runtime_app 1826330: anti-cheat runtime, no-op on arm64ec |
| steam/1199570 | desktop-only: Proton media converter blank.mka is not shipped |
| steam/1282690 | desktop-only: GPU vendor hiding has no meaning on Android GPUs |
| steam/1313860 | desktop-only: uses the alternative arial.ttf shipped with Proton |
| steam/1382330 | pins the game to the Intel P-cores (taskset) from the host CPU topology |
| steam/1491460 | desktop-only: Proton media converter blank.mka is not shipped |
| steam/1506830 | desktop-only: uses the alternative arial.ttf shipped with Proton |
| steam/1549250 | desktop-only: changes Proton's steam.exe launch command |
| steam/1588990 | desktop-only: Proton media converter blank.mka is not shipped |
| steam/1895130 | desktop-only: Proton media converter blank.mka is not shipped |
| steam/1985810 | desktop-only: changes Proton's steam.exe launch command |
| steam/2322010 | condition not expressible: os.environ.get("SteamDeck", 0) == "1" |
| steam/283640 | desktop-only: Proton media converter blank.mka is not shipped |
| steam/311210 | desktop-only: changes Proton's steam.exe launch command |
| steam/3495730 | desktop-only: gamescope is not used |
| steam/352130 | desktop-only: Proton steam.exe launcher shim |
| steam/3754990 | desktop-only: gamescope is not used |
| steam/3932890 | runtime_app 1161040: anti-cheat runtime, no-op on arm64ec |
| steam/440900 | runtime_app 1161040: anti-cheat runtime, no-op on arm64ec |
| steam/455490 | desktop-only: Proton media converter blank.mka is not shipped |
| steam/754530 | not convertible |
| steam/787810 | desktop-only: Proton media converter blank.mka is not shipped |

## Known runtime limits

- `hidewineexports`: no effect until proton-wine carries the wine-staging HideWineExports patch.
- `runtime_app` (EAC 1826330 / BattlEye 1161040): no-op; anti-cheat modules cannot run on arm64ec.
- VC++ 2015-2022 layouts keep Wine builtin msvcp140 unless `PROTONFIX_VC2022_NATIVE=1` (GameNative PR #1571 crash).
- `exec` verbs (vcrun2008, vcrun2013, physx, ubisoftconnect, cncnet_ra2) run real installers under a watchdog; they may hang under FEX and then time out.
- 7z layouts with `dictSizeBytes` above a third of device RAM are refused (dotnet452-48 use a 512 MB dictionary).
- Desktop-only Valve flags are ignored: GPU hiding, noopwr, noxalia, nvapi, gamedrive, core caps above the device core count.
- `tools.json` URLs point at the current Termux package versions and break when Termux updates; rerun tools/verbs/device_tools.py.
- Nothing in this repo has been verified on a device yet (regsvr32/exec steps, tool downloads, range cabinets, PICS token for the tool-list apps).
