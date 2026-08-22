; Apex study-build installer.
;
; Packages the Apex desktop application together with the patched TORCS 1.3.9
; runtime that build-windows.ps1 produced, so a participant on Windows can run
; the whole capture workflow locally instead of driving over a remote desktop.
;
; This is deliberately NOT a modified copy of the upstream torcs64.nsi: that
; script enumerates every payload file by hand and has a matching validator,
; which a study build with extra presets and a bundled application would fight.
; Compiled by build-windows.ps1, which supplies both defines:
;
;   makensis /DPAYLOAD_DIR=<staged tree> /DOUTPUT_FILE=<setup exe> apexstudy.nsi

!ifndef PAYLOAD_DIR
    !error "PAYLOAD_DIR is required: point it at the staged payload tree."
!endif
!ifndef OUTPUT_FILE
    !error "OUTPUT_FILE is required: point it at the installer to write."
!endif

!define PRODUCT_NAME "Apex Study Build"
!define PRODUCT_PUBLISHER "University of Bristol x IBM summer project"
!define PRODUCT_EXE "Apex.exe"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\ApexStudyBuild"
; Written on install and required before the uninstaller deletes anything, so a
; mistyped install directory can never take an unrelated folder with it.
!define PAYLOAD_MARKER "apex-study-build.txt"

!include "MUI2.nsh"

Name "${PRODUCT_NAME}"
OutFile "${OUTPUT_FILE}"
Unicode true
; Per-user install: no UAC prompt, and TORCS keeps its own profile under
; %LOCALAPPDATA% anyway, so nothing here needs administrator rights.
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\Apex"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
ShowInstDetails show
ShowUnInstDetails show
SetCompressor /SOLID lzma

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${PRODUCT_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Start Apex now"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Section "Apex and TORCS" SecMain
    SetOutPath "$INSTDIR"
    SetOverwrite on
    File /r "${PAYLOAD_DIR}\*"

    ; Fail loudly rather than leaving a half-usable install behind.
    IfFileExists "$INSTDIR\${PRODUCT_EXE}" +3 0
        MessageBox MB_ICONSTOP "Payload incomplete: ${PRODUCT_EXE} is missing."
        Abort
    IfFileExists "$INSTDIR\torcs-runtime\wtorcs.exe" +3 0
        MessageBox MB_ICONSTOP "Payload incomplete: torcs-runtime\wtorcs.exe is missing."
        Abort
    IfFileExists "$INSTDIR\granite-runtime\llama-server.exe" +3 0
        MessageBox MB_ICONSTOP "Payload incomplete: granite-runtime\llama-server.exe is missing."
        Abort
    IfFileExists "$INSTDIR\ffmpeg\ffmpeg.exe" +3 0
        MessageBox MB_ICONSTOP "Payload incomplete: ffmpeg\ffmpeg.exe is missing."
        Abort

    FileOpen $0 "$INSTDIR\${PAYLOAD_MARKER}" w
    FileWrite $0 "${PRODUCT_NAME}$\r$\n"
    FileClose $0

    CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
    CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\Apex.lnk" "$INSTDIR\${PRODUCT_EXE}"
    CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall Apex.lnk" "$INSTDIR\uninstall.exe"
    CreateShortCut "$DESKTOP\Apex.lnk" "$INSTDIR\${PRODUCT_EXE}"

    WriteUninstaller "$INSTDIR\uninstall.exe"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${PRODUCT_NAME}"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
    WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$\"$INSTDIR\${PRODUCT_EXE}$\""
    WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
    WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
    IfFileExists "$INSTDIR\${PAYLOAD_MARKER}" +3 0
        MessageBox MB_ICONSTOP "$INSTDIR does not look like an ${PRODUCT_NAME} install; nothing was removed."
        Abort

    Delete "$DESKTOP\Apex.lnk"
    Delete "$SMPROGRAMS\${PRODUCT_NAME}\Apex.lnk"
    Delete "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall Apex.lnk"
    RMDir "$SMPROGRAMS\${PRODUCT_NAME}"

    RMDir /r "$INSTDIR"
    DeleteRegKey HKCU "${UNINSTALL_KEY}"

    ; Captured runs live in the participant's Apex workspace, not here, so they
    ; deliberately survive uninstalling the application.
SectionEnd
