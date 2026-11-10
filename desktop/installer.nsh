!macro customInstall
  IfSilent driver_skip
  MessageBox MB_YESNO|MB_ICONQUESTION "安裝 VB-CABLE 虛擬音效卡？$\r$\n$\r$\nVB-CABLE 由 VB-Audio 提供，屬於 donationware，歡迎支持原作者：https://www.vb-cable.com/$\r$\n需要系統管理員權限，完成後可能需要重新啟動 Windows。$\r$\n既有 VB-CABLE 將保留且不列為本程式所有。" IDNO driver_skip
  nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\resources\assets\driver.ps1" -Mode install'
  Pop $0
  StrCmp $0 "0" driver_skip
  MessageBox MB_OK|MB_ICONINFORMATION "音效卡安裝未完成。你可以稍後從 ESP32 Audio 的音效卡頁面再次安裝。"
  driver_skip:
!macroend
!macro customUnInstall
  ReadEnvStr $0 "ProgramData"
  IfFileExists "$0\ESP32Audio\driver.json" 0 driver_uninstall_done
  IfSilent driver_uninstall_done
  nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\resources\assets\driver.ps1" -Mode remove'
  Pop $0
  StrCmp $0 "0" driver_uninstall_done
  MessageBox MB_OK|MB_ICONINFORMATION "虛擬音效卡尚未移除，驅動記錄已保留。請使用 VB-CABLE 官方安裝程式移除，或重新安裝 ESP32 Audio 後重試。"
  driver_uninstall_done:
!macroend
