; 记录仪视频转码工具 —— Inno Setup 安装脚本
;
; 双击生成的 setup.exe 即可安装：向导式界面、可选目录、自动建开始菜单与桌面快捷方式、
; 可从「控制面板 → 应用和功能」正常卸载。
;
; 构建前提（build_assets 目录内容）：
;   记录仪视频转码工具.exe      <- PyInstaller 产物
;   ffmpeg\ffmpeg.exe            <- 内置，让程序完全自包含
;   ffmpeg\ffprobe.exe
;
; 构建命令：
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\dashcam_transcoder.iss

#define AppName        "记录仪视频转码工具"
#define AppVersion     "1.0.0"
#define AppPublisher   "czm-curtis"
#define AppURL         "https://github.com/chaiziming/dashcam-transcoder"
#define AppExeName     "记录仪视频转码工具.exe"
#define BuildAssets    "D:\vin-mosaic-app\build_assets"

[Setup]
AppId={{A1B2C3D4-5E6F-4A7B-8C9D-0E1F2A3B4C5D}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=D:\vin-mosaic-app\dist_installer
OutputBaseFilename=dashcam-transcoder-{#AppVersion}-setup
SetupIconFile={#BuildAssets}\app.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; 主程序
Source: "{#BuildAssets}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; 内置 ffmpeg：用户装完即可用，无需自行安装 ffmpeg
Source: "{#BuildAssets}\ffmpeg\ffmpeg.exe"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion
Source: "{#BuildAssets}\ffmpeg\ffprobe.exe"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion
; 使用说明
Source: "D:\vin-mosaic-app\docs_使用说明.md"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
    Flags: nowait postinstall skipifsilent

[Code]
// 安装前检查：ffmpeg 缺失就明确报错，而不是装出一个跑不了的程序
function InitializeSetup(): Boolean;
var
  FfmpegExe: String;
begin
  Result := True;
  FfmpegExe := ExpandConstant('{#BuildAssets}\ffmpeg\ffmpeg.exe');
  if not FileExists(FfmpegExe) then
  begin
    MsgBox('构建产物不完整：未找到 ffmpeg.exe。' + #13#10 +
           '请先准备 build_assets\ffmpeg 目录。', mbCriticalError, '构建失败');
    Result := False;
  end;
end;

// 安装后自检：确认 ffmpeg 真的落到了安装目录
procedure CurStepChanged(CurStep: TSetupStep);
var
  FFmpegDest: String;
begin
  if CurStep = ssPostInstall then
  begin
    FFmpegDest := ExpandConstant('{app}\ffmpeg\ffmpeg.exe');
    if not FileExists(FFmpegDest) then
      MsgBox('警告：未能安装 ffmpeg，程序可能无法正常工作。', mbError, '安装警告');
  end;
end;

// 卸载时清理预览缓存（临时目录，可能有数百 MB）
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ProxyDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    ProxyDir := ExpandConstant('{localappdata}\Temp\vin_mosaic_proxy');
    if DirExists(ProxyDir) then
      DelTree(ProxyDir, True, True, True);
  end;
end;