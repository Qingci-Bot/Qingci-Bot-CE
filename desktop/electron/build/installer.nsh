; Qingci-Bot CE — NSIS 安装器定制
;
; 背景：用户数据（instances/<name>/{config.yaml,data/,plugins/}）由桌面壳放在
; 桌面 EXE 旁（QINGCI_APP_DIR = EXE 所在目录），也就是 $INSTDIR 里面。
; electron-builder 的卸载器在「升级」时会先把 $INSTDIR 整体改名为
; $PLUGINSDIR\old-install，再 RMDir /r $INSTDIR —— 用户配置与数据库一并被删。
;
; 对策：升级流程中先把 instances/ 暂存到 $TEMP，文件装好后再放回原位。
; 仅对升级生效（${isUpdated}）；用户主动卸载仍按原行为清除数据。
;
; 暂存目录名带固定前缀，便于异常中断后人工找回。

!define QINGCI_DATA_STASH "$TEMP\qingci-bot-ce-instances"

!macro customUnInstall
  ; customUnInstall 在删除文件之前执行（uninstaller.nsh 中位于 RMDir 之前）
  ${if} ${isUpdated}
    ${if} ${FileExists} "$INSTDIR\instances\*.*"
      DetailPrint "保留用户数据：$INSTDIR\instances"
      RMDir /r "${QINGCI_DATA_STASH}"
      CreateDirectory "${QINGCI_DATA_STASH}"
      ; 同卷 rename 是 O(1)，实例数据（DB/插件/日志）可能有几百 MB，
      ; 逐个复制会在「卸载旧版本」这一步明显拖时间。跨卷或目录被占用时才退回复制。
      ClearErrors
      Rename "$INSTDIR\instances" "${QINGCI_DATA_STASH}\instances"
      ${if} ${Errors}
        DetailPrint "rename 不可用，改用逐文件复制"
        CopyFiles /SILENT "$INSTDIR\instances\*.*" "${QINGCI_DATA_STASH}\instances"
      ${endif}
    ${endif}
  ${endif}
!macroend

!macro customInstall
  ; 安装完文件后把暂存的用户数据放回（同样优先 rename）
  ${if} ${FileExists} "${QINGCI_DATA_STASH}\instances\*.*"
    DetailPrint "恢复用户数据到 $INSTDIR\instances"
    ClearErrors
    Rename "${QINGCI_DATA_STASH}\instances" "$INSTDIR\instances"
    ${if} ${Errors}
      CreateDirectory "$INSTDIR\instances"
      CopyFiles /SILENT "${QINGCI_DATA_STASH}\instances\*.*" "$INSTDIR\instances"
    ${endif}
    ; 只有确认目标已有内容才删暂存：万一恢复失败，暂存还能被启动时的兜底逻辑救回来
    ${if} ${FileExists} "$INSTDIR\instances\*.*"
      RMDir /r "${QINGCI_DATA_STASH}"
    ${else}
      DetailPrint "恢复失败，保留暂存目录：${QINGCI_DATA_STASH}"
    ${endif}
  ${endif}
!macroend

; ============================================================
; 数据目录选择页
;
; 用户数据（实例的 config.yaml / 数据库 / 插件数据）默认放在安装目录外的
; 独立目录。选在这里而不是 $INSTDIR，是为了让升级/卸载（NSIS 只清理
; $INSTDIR）永远碰不到数据——此前把数据放在安装目录里，升级时被旧版本
; 的卸载器删掉过。
; ============================================================

!include "nsDialogs.nsh"

; 数据目录页只在安装器构建里需要。卸载器构建（BUILD_UNINSTALLER）不插入该页，
; 若把 Function 也编译进去会触发 warning 6010（未引用函数），而 electron-builder
; 以 -WX 调用 makensis，任何警告都会导致构建失败。
!ifndef BUILD_UNINSTALLER

!define QINGCI_DATADIR_REGKEY "Software\Qingci-Bot CE"
!define QINGCI_DATADIR_DEFAULT "$LOCALAPPDATA\Qingci-Bot CE\data"

var DataDirPage
var DataDirInput
var DataDirValue

!macro customPageAfterChangeDir
  Page custom DataDirPageCreate DataDirPageLeave
!macroend

Function DataDirPageCreate
  nsDialogs::Create 1018
  Pop $DataDirPage
  ${If} $DataDirPage == error
    Abort
  ${EndIf}

  ; 升级时沿用上次的选择
  ReadRegStr $DataDirValue HKCU "${QINGCI_DATADIR_REGKEY}" "DataDir"
  ${If} $DataDirValue == ""
    StrCpy $DataDirValue "${QINGCI_DATADIR_DEFAULT}"
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 12u "选择数据目录"
  Pop $0
  ${NSD_CreateLabel} 0 18u 100% 24u "实例的配置、数据库与插件数据将保存在此目录；它与安装目录分离，升级和卸载都不会删除。"
  Pop $0

  ${NSD_CreateDirRequest} 0 46u 78% 12u "$DataDirValue"
  Pop $DataDirInput
  ${NSD_CreateBrowseButton} 80% 46u 20% 12u "浏览..."
  Pop $0
  ${NSD_OnClick} $0 DataDirBrowse

  nsDialogs::Show
FunctionEnd

Function DataDirBrowse
  ${NSD_GetText} $DataDirInput $DataDirValue
  nsDialogs::SelectFolderDialog "选择数据目录" "$DataDirValue"
  Pop $0
  ${If} $0 != error
    ${NSD_SetText} $DataDirInput "$0"
  ${EndIf}
FunctionEnd

Function DataDirPageLeave
  ${NSD_GetText} $DataDirInput $DataDirValue
  ${If} $DataDirValue == ""
    MessageBox MB_ICONEXCLAMATION|MB_OK "请选择数据目录。"
    Abort
  ${EndIf}

  ; 不允许落在安装目录内：那样卸载时会被一并删除，等于白选
  StrCpy $1 "$INSTDIR\"
  StrLen $2 $1
  StrCpy $3 $DataDirValue $2
  ${If} $3 == $1
    MessageBox MB_ICONEXCLAMATION|MB_OK "数据目录不能位于安装目录内部，请另选一个目录。"
    Abort
  ${EndIf}

  ; 记住选择（下次升级沿用），并写给应用读取
  WriteRegStr HKCU "${QINGCI_DATADIR_REGKEY}" "DataDir" "$DataDirValue"
  CreateDirectory "$APPDATA\Qingci-Bot CE"
  FileOpen $0 "$APPDATA\Qingci-Bot CE\datadir.txt" w
  FileWrite $0 "$DataDirValue"
  FileClose $0
FunctionEnd

!endif ; BUILD_UNINSTALLER
