# RaG DayZ Tools

Free Windows toolkit for DayZ modders, terrain makers, and server owners.

- **Version:** 1.0.2 Stable
- **Author:** RaG Tyson
- **Documentation:** [RaG DayZ Tools Wiki](https://github.com/Tyson89/RaG-DayZ-Tools/wiki)
- **Downloads:** [GitHub Releases](https://github.com/Tyson89/RaG-DayZ-Tools/releases)
- **Support:** [GitHub Issues](https://github.com/Tyson89/RaG-DayZ-Tools/issues)
- **License:** Freeware — Proprietary / All Rights Reserved

## Included Tools

| Tool | Purpose | Guide |
|---|---|---|
| `RaG_PBO_Builder.exe` | Build, binarize, validate, sign, and package DayZ addons | [PBO Builder](https://github.com/Tyson89/RaG-DayZ-Tools/wiki/PBO-Builder) |
| `RaG_Game_Data_Extractor.exe` | Extract official DayZ data into correct virtual paths | [Game Data Extractor](https://github.com/Tyson89/RaG-DayZ-Tools/wiki/Game-Data-Extractor) |
| `RaG_PBO_Inspector.exe` | Inspect, search, compare, preview, and extract PBO archives | [PBO Inspector](https://github.com/Tyson89/RaG-DayZ-Tools/wiki/PBO-Inspector) |
| `RaG_Mod_Relocator.exe` | Preview and safely rewrite virtual mod-path references | [Mod Relocator](https://github.com/Tyson89/RaG-DayZ-Tools/wiki/Mod-Relocator) |
| `RaG_Workshop_Publisher.exe` | Validate and update existing DayZ Workshop items | [Workshop Publisher](https://github.com/Tyson89/RaG-DayZ-Tools/wiki/Workshop-Publisher) |
| `RaG_Tools_Updater.exe` | Check, verify, and install complete suite updates | [Tools Updater](https://github.com/Tyson89/RaG-DayZ-Tools/wiki/Tools-Updater) |

## Download

Download `RaG_PBO_Tools_Setup.exe` from [latest release](https://github.com/Tyson89/RaG-DayZ-Tools/releases/latest).

Installer supports individual tools or complete suite. Every tool includes green **Check for Update** button opening dedicated updater.

## Documentation

Full guides live in [Wiki](https://github.com/Tyson89/RaG-DayZ-Tools/wiki):

- [Builder Preflight](https://github.com/Tyson89/RaG-DayZ-Tools/wiki/Builder-Preflight)
- [Terrain and WRP](https://github.com/Tyson89/RaG-DayZ-Tools/wiki/Terrain-and-WRP)
- [CLI and Automation](https://github.com/Tyson89/RaG-DayZ-Tools/wiki/CLI-and-Automation)
- [Installation, Signing, and Safety](https://github.com/Tyson89/RaG-DayZ-Tools/wiki/Installation-and-Safety)
- [Building From Source](https://github.com/Tyson89/RaG-DayZ-Tools/wiki/Building-from-Source)

## Requirements

- Windows
- DayZ Tools for Binarize, CfgConvert, ImageToPAA, signing, and Workshop Publisher features
- Desktop Steam plus owned DayZ for Workshop publishing

Compiled releases do not require Python.

## PBO Builder Exclusions

- **Exclude file extensions** controls final PBO contents. Matching files remain available during Binarize and CfgConvert so `#include` headers work.
- **Exclude folder names** ignores matching folders and everything inside them throughout the build.
- Default excluded folders: `source`, `temp`.
- CLI equivalents: `--exclude-extensions` and `--exclude-folders`.

## Important

- Never share `.biprivatekey`; distribute matching `.bikey` only.
- Back up source before path relocation.
- Re-sign modified PBOs.
- Workshop Publisher updates existing items only.
- Download releases only from this repository.

## Source Builds

```powershell
python -m pip install -r requirements.txt
.\build_rag_pbo_builder.ps1
```

See [Building From Source](https://github.com/Tyson89/RaG-DayZ-Tools/wiki/Building-from-Source) for every tool and installer.

## License

Freeware, proprietary, all rights reserved. Use is permitted for personal and authorized DayZ modding. Redistribution, modification, decompilation, reverse engineering, resale, or inclusion in another project requires written permission.

See [LICENSE.txt](LICENSE.txt).

Software provided as-is without warranty.
