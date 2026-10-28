; Inno Setup script → Lisanote-<ver>-windows-setup.exe   (iscc /DAPPVER=1.0.0 packaging\lisanote.iss)
#ifndef APPVER
  #define APPVER "1.0.0"
#endif
[Setup]
AppId={{8C1F2E44-6B0A-4D6C-9E3B-1A7F2C5D9B10}
AppName=Lisanote
AppVersion={#APPVER}
AppPublisher=Lisanote
AppPublisherURL=https://github.com/MartinDean07/lisanote
DefaultDirName={localappdata}\Programs\Lisanote
DefaultGroupName=Lisanote
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Lisanote-{#APPVER}-windows-setup
SetupIconFile=..\build\lisanote.ico
UninstallDisplayIcon={app}\Lisanote.exe
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\Lisanote\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Lisanote"; Filename: "{app}\Lisanote.exe"
Name: "{userdesktop}\Lisanote"; Filename: "{app}\Lisanote.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\Lisanote.exe"; Description: "Start Lisanote"; Flags: nowait postinstall skipifsilent
