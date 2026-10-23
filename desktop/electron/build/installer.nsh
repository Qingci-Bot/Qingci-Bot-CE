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
    RMDir /r "${QINGCI_DATA_STASH}"
  ${endif}
!macroend
