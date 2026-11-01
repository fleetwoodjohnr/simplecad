; Windows installer for SimpleCAD (Inno Setup 6). Built by packaging/build.py,
; which passes AppVersion, SourceDir, IconFile, OutputDir and OutputName.
#define AppName "SimpleCAD"

[Setup]
AppId={{6A8E2C5D-3F41-4B8A-9E6C-5D1F2A7B9C30}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=SimpleCAD
AppPublisherURL=https://github.com/fleetwoodjohnr/simplecad
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\SimpleCAD.exe
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user by default, so installing needs no administrator; the user may choose all users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
; An update run over an existing install closes the running app first.
CloseApplications=yes

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\SimpleCAD.exe"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\SimpleCAD.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Registry]
Root: HKA; Subkey: "Software\Classes\.scad3"; ValueType: string; ValueName: ""; ValueData: "SimpleCAD.Project"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\SimpleCAD.Project"; ValueType: string; ValueName: ""; ValueData: "SimpleCAD project"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\SimpleCAD.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\SimpleCAD.exe"" ""%1"""

[Run]
Filename: "{app}\SimpleCAD.exe"; Description: "Start SimpleCAD"; Flags: nowait postinstall skipifsilent
