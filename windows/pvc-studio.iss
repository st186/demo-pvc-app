; Inno Setup script: packages the folder made by build_env.ps1 into one
; PVC-Studio-Setup.exe. Built by .github/workflows/windows.yml:
;   iscc /DBuildDir=<folder> /DAppVersion=<x.y.z> windows\pvc-studio.iss

#ifndef AppVersion
  #define AppVersion "0.3.0"
#endif
#ifndef BuildDir
  #define BuildDir "..\build\PVC Studio"
#endif

[Setup]
AppId={{D1B4D6F2-5E3A-4C3B-9A7E-2F1C7B9E4A10}
AppName=PVC Studio
AppVersion={#AppVersion}
AppVerName=PVC Studio {#AppVersion}
AppPublisher=PVC Studio
DefaultDirName={localappdata}\Programs\PVC Studio
DefaultGroupName=PVC Studio
DisableProgramGroupPage=yes
DisableDirPage=auto
; Per-user install: no admin rights or UAC prompt needed.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=PVC-Studio-Setup-{#AppVersion}
SetupIconFile=pvc.ico
UninstallDisplayIcon={app}\pvc.ico
UninstallDisplayName=PVC Studio
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; A running PVC Studio is stopped by StopRunningApp() in [Code] below; the
; Restart Manager can't close its windowless server and would only prompt.
CloseApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#BuildDir}\*"; DestDir: "{app}"; Excludes: "uninstall.bat"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\PVC Studio"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m pvccard.desktop"; WorkingDir: "{app}"; IconFilename: "{app}\pvc.ico"; Comment: "PVC cards and passport photos"
Name: "{autodesktop}\PVC Studio"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m pvccard.desktop"; WorkingDir: "{app}"; IconFilename: "{app}\pvc.ico"; Comment: "PVC cards and passport photos"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m pvccard.desktop"; WorkingDir: "{app}"; Description: "Start PVC Studio now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Models downloaded later (Best edges) and compiled files.
Type: filesandordirs; Name: "{app}\models"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{localappdata}\PVC Studio"

[Code]
// PVC Studio's server runs as a windowless pythonw.exe, which Windows'
// Restart Manager can't ask to close, so Setup used to stop with "unable to
// automatically close all applications". Stop it ourselves first: only
// processes whose executable lives inside this install folder.
procedure StopRunningApp();
var
  ResultCode: Integer;
  Dir: String;
begin
  Dir := ExpandConstant('{app}');
  if not DirExists(Dir) then
    Exit;
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass -Command "Get-Process pythonw,python -ErrorAction SilentlyContinue | ' +
    'Where-Object { $_.Path -and $_.Path.StartsWith(''' + Dir + '\'', [StringComparison]::OrdinalIgnoreCase) } | ' +
    'Stop-Process -Force; Start-Sleep -Milliseconds 800"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningApp();
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    StopRunningApp();
end;
