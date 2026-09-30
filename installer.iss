; Instalador do Windows Optimizer (Inno Setup 6).
; Pré-requisito: rodar build_exe.bat antes (gera dist\WindowsOptimizer\).
; Gerar: build_installer.bat  ->  installer_output\WindowsOptimizer-Setup-<versao>.exe

#define AppName "Windows Optimizer"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppExe "WindowsOptimizer.exe"

[Setup]
AppId={{8F3C2A71-5B4E-4D2A-9C1F-6E7B0A3D9E54}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Pedro
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=installer_output
OutputBaseFilename=WindowsOptimizer-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; O .exe foi gerado com Python 64 bits e exige Windows 10/11.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Instala para todos os usuários (Program Files); o usuário pode optar por instalar só para si.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\WindowsOptimizer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; Atualização pelo próprio programa (instalador aberto com /SILENT /RELAUNCH=1): reabre o programa
; no fim, como o usuário que o estava usando (sem herdar a elevação do instalador).
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: RelaunchRequested

; Logs e backups (em %LOCALAPPDATA%\WindowsOptimizer quando a pasta do programa não é gravável)
; são mantidos na desinstalação de propósito: os backups permitem desfazer otimizações.
[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\backups"

[Code]
const
  SYNCHRONIZE = $00100000;

function OpenProcess(dwDesiredAccess: DWORD; bInheritHandle: BOOL; dwProcessId: DWORD): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(hHandle: THandle; dwMilliseconds: DWORD): DWORD;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(hObject: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';

{ Atualização pelo próprio programa: ele passa /WAITPID=<pid> e fecha em seguida.
  Espera (até 60 s) esse processo terminar antes de mexer nos arquivos, para não
  disputar com o programa que ainda está fechando. }
function InitializeSetup: Boolean;
var
  Pid: Integer;
  Handle: THandle;
begin
  Pid := StrToIntDef(ExpandConstant('{param:WAITPID|0}'), 0);
  if Pid > 0 then
  begin
    Handle := OpenProcess(SYNCHRONIZE, False, Pid);
    if Handle <> 0 then
    begin
      WaitForSingleObject(Handle, 60000);
      CloseHandle(Handle);
    end;
  end;
  Result := True;
end;

function RelaunchRequested: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;
