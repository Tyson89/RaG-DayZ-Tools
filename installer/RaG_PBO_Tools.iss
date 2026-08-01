#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef AppVersionNumeric
  #define AppVersionNumeric "1.0.0.0"
#endif

[Setup]
AppId={{C9E0772F-DB8C-4525-8B38-683344112F2D}
AppName=RaG PBO Tools
AppVersion={#AppVersion}
AppVerName=RaG PBO Tools {#AppVersion}
AppPublisher=RaG Tyson
AppPublisherURL=https://github.com/Tyson89/RaG-DayZ-Tools
AppSupportURL=https://github.com/Tyson89/RaG-DayZ-Tools/issues
AppUpdatesURL=https://github.com/Tyson89/RaG-DayZ-Tools/releases
LicenseFile=LICENSE.txt
VersionInfoVersion={#AppVersionNumeric}
DefaultDirName={localappdata}\Programs\RaG PBO Tools
DefaultGroupName=RaG PBO Tools
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist\installer
OutputBaseFilename=RaG_PBO_Tools_Setup
SetupIconFile=assets\installer.ico
UninstallDisplayIcon={app}\RaG_Tools_Updater\RaG_Tools_Updater.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
RestartApplications=no
SourceDir=..

[Types]
Name: "full"; Description: "Full installation"
Name: "custom"; Description: "Custom installation"; Flags: iscustom

[Components]
Name: "builder"; Description: "RaG PBO Builder"; Types: full custom
Name: "extractor"; Description: "RaG Game Data Extractor"; Types: full custom
Name: "inspector"; Description: "RaG PBO Inspector"; Types: full custom
Name: "relocator"; Description: "RaG Mod Relocator"; Types: full custom
Name: "publisher"; Description: "RaG Workshop Publisher"; Types: full custom
Name: "updater"; Description: "RaG Tools Updater"; Types: full custom; Flags: fixed

[Tasks]
Name: "desktopbuilder"; Description: "RaG PBO Builder"; GroupDescription: "Create desktop shortcuts:"; Flags: unchecked; Components: builder
Name: "desktopextractor"; Description: "RaG Game Data Extractor"; GroupDescription: "Create desktop shortcuts:"; Flags: unchecked; Components: extractor
Name: "desktopinspector"; Description: "RaG PBO Inspector"; GroupDescription: "Create desktop shortcuts:"; Flags: unchecked; Components: inspector
Name: "desktoprelocator"; Description: "RaG Mod Relocator"; GroupDescription: "Create desktop shortcuts:"; Flags: unchecked; Components: relocator
Name: "desktoppublisher"; Description: "RaG Workshop Publisher"; GroupDescription: "Create desktop shortcuts:"; Flags: unchecked; Components: publisher
Name: "desktopupdater"; Description: "RaG Tools Updater"; GroupDescription: "Create desktop shortcuts:"; Flags: unchecked; Components: updater

[Files]
Source: "dist\RaG_PBO_Builder\*"; DestDir: "{app}\RaG_PBO_Builder"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: builder
Source: "dist\RaG_Game_Data_Extractor\*"; DestDir: "{app}\RaG_Game_Data_Extractor"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: extractor
Source: "dist\RaG_PBO_Inspector\*"; DestDir: "{app}\RaG_PBO_Inspector"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: inspector
Source: "dist\RaG_Mod_Relocator\*"; DestDir: "{app}\RaG_Mod_Relocator"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: relocator
Source: "dist\RaG_Workshop_Publisher\*"; DestDir: "{app}\RaG_Workshop_Publisher"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: publisher
Source: "dist\RaG_Tools_Updater\*"; DestDir: "{app}\RaG_Tools_Updater"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: updater
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\RaG PBO Builder"; Filename: "{app}\RaG_PBO_Builder\RaG_PBO_Builder.exe"; Components: builder
Name: "{autoprograms}\RaG Game Data Extractor"; Filename: "{app}\RaG_Game_Data_Extractor\RaG_Game_Data_Extractor.exe"; Components: extractor
Name: "{autoprograms}\RaG PBO Inspector"; Filename: "{app}\RaG_PBO_Inspector\RaG_PBO_Inspector.exe"; Components: inspector
Name: "{autoprograms}\RaG Mod Relocator"; Filename: "{app}\RaG_Mod_Relocator\RaG_Mod_Relocator.exe"; Components: relocator
Name: "{autoprograms}\RaG Workshop Publisher"; Filename: "{app}\RaG_Workshop_Publisher\RaG_Workshop_Publisher.exe"; Components: publisher
Name: "{autoprograms}\RaG Tools Updater"; Filename: "{app}\RaG_Tools_Updater\RaG_Tools_Updater.exe"; Components: updater
Name: "{autodesktop}\RaG PBO Builder"; Filename: "{app}\RaG_PBO_Builder\RaG_PBO_Builder.exe"; Tasks: desktopbuilder; Components: builder
Name: "{autodesktop}\RaG Game Data Extractor"; Filename: "{app}\RaG_Game_Data_Extractor\RaG_Game_Data_Extractor.exe"; Tasks: desktopextractor; Components: extractor
Name: "{autodesktop}\RaG PBO Inspector"; Filename: "{app}\RaG_PBO_Inspector\RaG_PBO_Inspector.exe"; Tasks: desktopinspector; Components: inspector
Name: "{autodesktop}\RaG Mod Relocator"; Filename: "{app}\RaG_Mod_Relocator\RaG_Mod_Relocator.exe"; Tasks: desktoprelocator; Components: relocator
Name: "{autodesktop}\RaG Workshop Publisher"; Filename: "{app}\RaG_Workshop_Publisher\RaG_Workshop_Publisher.exe"; Tasks: desktoppublisher; Components: publisher
Name: "{autodesktop}\RaG Tools Updater"; Filename: "{app}\RaG_Tools_Updater\RaG_Tools_Updater.exe"; Tasks: desktopupdater; Components: updater

[Run]
Filename: "{app}\RaG_PBO_Builder\RaG_PBO_Builder.exe"; Description: "Launch RaG PBO Builder"; Flags: nowait postinstall skipifsilent
