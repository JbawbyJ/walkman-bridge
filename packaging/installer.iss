; Walkman Bridge installer (Inno Setup 6)
; Compiled by packaging\build.ps1, which supplies AppVersion, StageDir, OutDir.
;
;   ISCC.exe /DAppVersion=0.1.0 /DStageDir=...\build\app-root /DOutDir=...\dist installer.iss
;
; Per-user install: no admin prompt, no UAC, everything under %LOCALAPPDATA%.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef StageDir
  #error StageDir must be defined (the staged application folder)
#endif
#ifndef OutDir
  #define OutDir "dist"
#endif

#define AppName "Walkman Bridge"
#define AppPublisher "Walkman Bridge"
#define AppExe "WalkmanBridge.exe"

[Setup]
AppId={{8F2B6A41-93C7-4E5D-9A16-2C7E4B0D5A31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
AppMutex=WalkmanBridgeAppMutex

; Per-user: installs without administrator rights.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Walkman Bridge
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
AllowNoIcons=yes

OutputDir={#OutDir}
OutputBaseFilename=WalkmanBridge-Setup-{#AppVersion}
SetupIconFile=walkman-bridge.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

LicenseFile={#StageDir}\LICENSE
InfoBeforeFile=readme-before-install.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
; The whole staged tree: launcher exe, jre\, ffmpeg\, app\, docs.
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Written at runtime, so Inno does not track them.
Type: filesandordirs; Name: "{localappdata}\Walkman Bridge"
Type: filesandordirs; Name: "{app}\app\backend\__pycache__"

[Code]
{ WebView2 is present on Windows 11 and on updated Windows 10, but not
  guaranteed. The app degrades to opening the dashboard in the default browser,
  so this is a heads-up rather than a hard requirement. }
function WebView2Installed(): Boolean;
var
  Value: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Value) or
    RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Value) or
    RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Value);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not WebView2Installed() then
    MsgBox('Microsoft Edge WebView2 was not detected.' + #13#13 +
           'Walkman Bridge will still work - it will open its dashboard in ' +
           'your default web browser instead of its own window.' + #13#13 +
           'To get the app window, install "Microsoft Edge WebView2 Runtime" ' +
           'from Microsoft, then restart Walkman Bridge.',
           mbInformation, MB_OK);
end;
