; Teams Transcriber installer
; Compile via:  ISCC.exe /DAppVersion=x.y.z installer\teams-transcriber.iss
; Or via the orchestrator:  python scripts\build_installer.py

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{C9F4C7E0-7E0A-4E80-9B5D-3E2A0F4F1B33}
AppName=Teams Transcriber
AppVersion={#AppVersion}
AppPublisher=Brian Lewis
DefaultDirName={localappdata}\Programs\TeamsTranscriber
DefaultGroupName=Teams Transcriber
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=TeamsTranscriberSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\TeamsTranscriber.exe
WizardStyle=modern
SetupIconFile=icon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional shortcuts:"; Flags: checked

[Files]
Source: "..\dist\TeamsTranscriber\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Teams Transcriber"; Filename: "{app}\TeamsTranscriber.exe"
Name: "{group}\Uninstall Teams Transcriber"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Teams Transcriber"; Filename: "{app}\TeamsTranscriber.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TeamsTranscriber.exe"; Description: "Launch Teams Transcriber"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove the installed binaries. User data under %LOCALAPPDATA%\TeamsTranscriber
; (recordings, db, settings) is intentionally preserved.
Type: filesandordirs; Name: "{app}"
