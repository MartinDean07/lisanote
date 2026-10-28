; Inno Setup script → Livescribe-<ver>-windows-setup.exe   (iscc /DVER=1.0.0 packaging\livescribe.iss)
#ifndef VER
  #define VER "1.0.0"
#endif
[Setup]
AppId={{8C1F2E44-6B0A-4D6C-9E3B-1A7F2C5D9B10}
AppName=Livescribe
AppVersion={#VER}
AppPublisher=Livescribe
AppPublisherURL=https://github.com/MartinDean07/livescribe
DefaultDirName={localappdata}\Programs\Livescribe
DefaultGroupName=Livescribe
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Livescribe-{#VER}-windows-setup
SetupIconFile=..\build\livescribe.ico
UninstallDisplayIcon={app}\Livescribe.exe
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\Livescribe\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Livescribe"; Filename: "{app}\Livescribe.exe"
Name: "{userdesktop}\Livescribe"; Filename: "{app}\Livescribe.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\Livescribe.exe"; Description: "Start Livescribe"; Flags: nowait postinstall skipifsilent
