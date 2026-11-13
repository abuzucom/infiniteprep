; InfinitePrep per-user Windows installer (D83a1b).
;
; Build: makensis /DVERSION=<version> /DSOURCE_DIR=<bundle folder>
;        /DOUTPUT_FILE=<setup.exe path> /DICON_FILE=<logo .ico> installer.nsi
;
; The installer needs no admin rights. Program files go to
; %LocalAppData%\Programs\InfinitePrep (D264), apart from the per-user
; data root %LocalAppData%\InfinitePrep, so removing the program never
; touches the library. The uninstaller keeps all data unless a box is
; checked (D265, D266). Music files and exports are never touched.

Unicode true
SetCompressor zlib

!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

!ifndef VERSION
  !error "Pass /DVERSION=<version>."
!endif
!ifndef SOURCE_DIR
  !error "Pass /DSOURCE_DIR=<bundle folder>."
!endif
!ifndef OUTPUT_FILE
  !error "Pass /DOUTPUT_FILE=<setup.exe path>."
!endif
!ifndef ICON_FILE
  !error "Pass /DICON_FILE=<logo .ico>. packaging/make_icon.py ico writes it."
!endif

!define APP_NAME "InfinitePrep"
!define APP_EXE "InfinitePrep.exe"
!define UNINSTALLER "Uninstall.exe"
!define PUBLISHER "InfinitePrep"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\InfinitePrep"
; The per-user data root that platformdirs uses on Windows: the library,
; settings, recovery backups, waveform cache, and downloaded fpcalc.
!define DATA_ROOT "$LOCALAPPDATA\InfinitePrep"
!define FORGET_CREDENTIALS_FLAG "--forget-credentials"

Name "${APP_NAME} ${VERSION}"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\${APP_NAME}"
RequestExecutionLevel user

!define MUI_ABORTWARNING
; setup.exe and Uninstall.exe carry the logo (D393). MUI reads both before the first page.
!define MUI_ICON "${ICON_FILE}"
!define MUI_UNICON "${ICON_FILE}"
!insertmacro MUI_PAGE_LICENSE "${SOURCE_DIR}\LICENSE"
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

UninstPage custom un.OptionsPage un.OptionsPageLeave
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Var DeleteDataCheckbox
Var DeleteKeysCheckbox
Var DeleteData
Var DeleteKeys

; An earlier installation must go first (D269). Its uninstaller runs
; silently, which keeps the library, settings, and saved keys.
Function .onInit
  ReadRegStr $0 HKCU "${UNINSTALL_KEY}" "InstallLocation"
  StrCmp $0 "" done
  IfFileExists "$0\${UNINSTALLER}" 0 done
  IfSilent remove_earlier
  MessageBox MB_OKCANCEL|MB_ICONQUESTION \
    "InfinitePrep is already installed in $0.$\r$\n$\r$\nSelect OK to uninstall it and continue. Your library, settings, and saved keys stay. Select Cancel to stop setup." \
    IDOK remove_earlier
  Abort
remove_earlier:
  ExecWait '"$0\${UNINSTALLER}" /S _?=$0' $1
  StrCmp $1 "0" earlier_removed
  MessageBox MB_OK|MB_ICONSTOP \
    "Setup could not remove the earlier installation. Uninstall InfinitePrep from Settings, Apps, then run setup again." \
    /SD IDOK
  Abort
earlier_removed:
  Delete "$0\${UNINSTALLER}"
  RMDir "$0"
done:
FunctionEnd

Section "InfinitePrep (required)" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR"
  File /r "${SOURCE_DIR}\*.*"
  WriteUninstaller "$INSTDIR\${UNINSTALLER}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "${PUBLISHER}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\${UNINSTALLER}"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\${UNINSTALLER}" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

; /o leaves the desktop shortcut unchecked by default.
Section /o "Desktop shortcut" SecDesktop
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
SectionEnd

Function un.onInit
  StrCpy $DeleteData ${BST_UNCHECKED}
  StrCpy $DeleteKeys ${BST_UNCHECKED}
  ; RMDir /r below removes the install folder. Refuse any folder that is not
  ; an InfinitePrep install, so a wrong path can never delete other files.
  ${GetFileName} "$INSTDIR" $0
  StrCmp $0 "${APP_NAME}" 0 refuse
  IfFileExists "$INSTDIR\${APP_EXE}" accepted refuse
refuse:
  MessageBox MB_OK|MB_ICONSTOP \
    "The folder $INSTDIR does not look like an InfinitePrep installation. Uninstall stopped and changed nothing." \
    /SD IDOK
  Abort
accepted:
FunctionEnd

; Both boxes start unchecked. A silent uninstall never shows this page, so
; it keeps the data and the saved keys.
Function un.OptionsPage
  !insertmacro MUI_HEADER_TEXT "Uninstall InfinitePrep" "Choose what else to remove."
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 30u "InfinitePrep never deletes your music files or exports. Check a box to also remove data that InfinitePrep keeps for you."
  Pop $0
  ${NSD_CreateCheckbox} 0 36u 100% 12u "Also delete my InfinitePrep library, settings, backups, and cache"
  Pop $DeleteDataCheckbox
  ${NSD_CreateCheckbox} 0 52u 100% 12u "Also delete my saved API keys"
  Pop $DeleteKeysCheckbox
  nsDialogs::Show
FunctionEnd

Function un.OptionsPageLeave
  ${NSD_GetState} $DeleteDataCheckbox $DeleteData
  ${NSD_GetState} $DeleteKeysCheckbox $DeleteKeys
FunctionEnd

Section "Uninstall"
  ; The app removes its own keyring entries, so this runs before the files go.
  ${If} $DeleteKeys == ${BST_CHECKED}
    ExecWait '"$INSTDIR\${APP_EXE}" ${FORGET_CREDENTIALS_FLAG}'
  ${EndIf}
  Delete "$SMPROGRAMS\${APP_NAME}.lnk"
  Delete "$DESKTOP\${APP_NAME}.lnk"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  ${If} $DeleteData == ${BST_CHECKED}
    RMDir /r "${DATA_ROOT}"
  ${EndIf}
SectionEnd
