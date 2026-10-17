; Instalador setup.exe de Astrados Audio Handler (Inno Setup 6.3+).
; Lo compila tools/build_release.sh, que pasa la version y las rutas:
;   ISCC /DAppVersion=1.0.0 /DSourceDir=<carpeta PyInstaller> /DOutputDir=<salida> /DIconFile=<app.ico> /DCreditsFile=<creditos.txt> AstradosAudioHandler.iss
; El AppId no debe cambiar nunca: es lo que permite actualizar sobre una version anterior.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #error "Falta /DSourceDir (carpeta generada por PyInstaller)"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\release"
#endif
#ifndef IconFile
  #define IconFile "..\visual\app.ico"
#endif
#ifndef CreditsFile
  #error "Falta /DCreditsFile (creditos en texto plano, los genera build_release.sh)"
#endif

#define AppName "Astrados Audio Handler"
#define AppExe "AstradosAudioHandler.exe"

[Setup]
AppId={{C0FAC27E-592A-4275-90DB-FE531BEC6CCD}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Astrados
AppPublisherURL=https://github.com/Astrado-DGM/audio_handler_astrados
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Por defecto en Program Files; el usuario puede elegir instalar solo para el.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=AstradosAudioHandler-{#AppVersion}-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Pagina de creditos (recursos graficos, sonidos CC BY 4.0, licencias) antes de instalar.
InfoBeforeFile={#CreditsFile}

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
