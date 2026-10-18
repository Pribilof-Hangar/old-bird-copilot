; Old Bird Copilot - Windows installer (Inno Setup 6). Built by build.ps1, which passes /DAppVersion=x.y.z.
; Per-user install: no administrator rights needed. Settings and flight recordings live in
; %APPDATA%\Old Bird Copilot and are kept on update and (by default) on uninstall.

#ifndef AppVersion
  #error Pass the version: ISCC /DAppVersion=1.2.3 OldBirdCopilot.iss
#endif

#define AppName "Old Bird Copilot"
#define AppExe "OldBirdCopilot.exe"

[Setup]
; Never change AppId: Windows uses it to recognise updates and the uninstaller.
AppId={{0225D0CD-8A64-43E9-8E7B-97D524C63776}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Old Bird Copilot
AppComments=Freeware spoken copilot callouts for Microsoft Flight Simulator 2024. For entertainment only.
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/ultra
SolidCompression=yes
OutputDir=..\release
OutputBaseFilename=OldBirdCopilot-Setup-{#AppVersion}
; Ask to close a running copy (the app holds this mutex) before installing or uninstalling.
AppMutex=LandingCallouts.SingleInstance
CloseApplications=yes
InfoBeforeFile=installer_info.txt

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\voice\*"; DestDir: "{app}\voice"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\Read Me First.txt"; DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[InstallDelete]
; Older files in the voice folder are replaced cleanly on update.
Type: filesandordirs; Name: "{app}\voice"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Spoken copilot callouts for MSFS 2024"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent

[Code]
// Uninstall keeps the user's settings and flight recordings unless they say otherwise.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\{#AppName}');
    if DirExists(DataDir) and not UninstallSilent then
      if MsgBox('Also remove your Old Bird Copilot settings and flight recordings?' + #13#10 + #13#10 +
                DataDir + #13#10 + #13#10 +
                'Choose No to keep them (for example if you plan to reinstall).',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
