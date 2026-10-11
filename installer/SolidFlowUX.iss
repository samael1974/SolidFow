; SolidFlow UX — self-contained Windows installer
; Built with Inno Setup 6. No administrator privileges required.

#define MyAppName "SolidFlow UX"
#define MyAppVersion "0.4.0-beta.13-test.5"
#define MyAppPublisher "SolidFlow UX Project"
#define MyAppURL "https://github.com/samael1974/FREECAD-IMPLEMENTAZIONE-UX"

[Setup]
AppId={{B7892BB3-447D-4DF2-AB4E-04665B47DA8D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={userappdata}\FreeCAD\v1-1\Mod\SolidFlowUX
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=SolidFlowUX-Setup-v{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName} {#MyAppVersion}
CreateUninstallRegKey=yes
Uninstallable=yes
CloseApplications=no
RestartApplications=no
SetupLogging=yes

[Files]
; Runtime synchronized from the local installation on 2026-09-19.
Source: "..\SolidFlowUX\Init.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\InitGui.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\solidflow_ui.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\solidflow_features.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\solidflow_smart.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\solidflow_sketch.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\solidflow_import.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\solidflow_viewbar.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\solidflow_beta5.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\solidflow_beta6.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\solidflow_mesh.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\solidflow_appearance.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\solidflow_beta7.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\solidflow_profiles.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\solidflow_fillet.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\solidflow_paths.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\solidflow_workflows.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\solidflow_beta12.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\manifest.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\solidflow_diagnostics.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\SolidFlowUX-Diagnostica.FCMacro"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\INSTALLAZIONE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SolidFlowUX\README.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\SolidFlowUX\CHANGELOG.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "SolidFlowUX-preinstall.ps1"; Flags: dontcopy

[InstallDelete]
Type: files; Name: "{app}\solidflow_beta4.py"
Type: files; Name: "{app}\solidflow_beta9.py"
Type: files; Name: "{app}\solidflow_patterns.py"
Type: filesandordirs; Name: "{app}\__pycache__"

[Icons]
Name: "{userprograms}\SolidFlow UX\Apri cartella SolidFlow UX"; Filename: "{app}"
Name: "{userprograms}\SolidFlow UX\Disinstalla SolidFlow UX"; Filename: "{uninstallexe}"

[Code]
function FreeCADIsRunning(): Boolean;
var
  ResultCode: Integer;
  TempFile: String;
begin
  TempFile := ExpandConstant('{tmp}\solidflow_freecad_process.txt');
  Result := False;
  if Exec(ExpandConstant('{cmd}'),
          '/C tasklist /FI "IMAGENAME eq FreeCAD.exe" | find /I "FreeCAD.exe" > "' + TempFile + '"',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Result := (ResultCode = 0);
  end;
  DeleteFile(TempFile);
end;

function InitializeSetup(): Boolean;
begin
  if FreeCADIsRunning() then
  begin
    MsgBox('FreeCAD è aperto.' + #13#10 + #13#10 +
           'Chiudi completamente FreeCAD prima di installare o aggiornare SolidFlow UX.',
           mbError, MB_OK);
    Result := False;
    exit;
  end;
  Result := True;
end;

procedure RunPreInstallMaintenance();
var
  ScriptPath, Params: String;
  ResultCode: Integer;
begin
  ExtractTemporaryFile('SolidFlowUX-preinstall.ps1');
  ScriptPath := ExpandConstant('{tmp}\SolidFlowUX-preinstall.ps1');
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ScriptPath + '"';

  if not Exec('powershell.exe', Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    MsgBox('Non è stato possibile avviare la manutenzione pre-installazione.' + #13#10 +
           'L''installazione proseguirà, ma controlla che non esista una seconda copia in FreeCAD\Mod\SolidFlowUX.',
           mbError, MB_OK);
    exit;
  end;

  if ResultCode <> 0 then
  begin
    MsgBox('La manutenzione pre-installazione ha restituito un errore.' + #13#10 +
           'L''installazione proseguirà, ma controlla la cartella FreeCAD\Mod\SolidFlowUX.',
           mbError, MB_OK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  CacheDir: String;
begin
  if CurStep = ssInstall then
    RunPreInstallMaintenance();

  if CurStep = ssPostInstall then
  begin
    CacheDir := AddBackslash(ExpandConstant('{app}')) + '__pycache__';
    if DirExists(CacheDir) then
      DelTree(CacheDir, True, True, True);
  end;
end;

function InitializeUninstall(): Boolean;
begin
  if FreeCADIsRunning() then
  begin
    MsgBox('Chiudi FreeCAD prima di disinstallare SolidFlow UX.', mbError, MB_OK);
    Result := False;
    exit;
  end;
  Result := True;
end;
