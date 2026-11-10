ESP32 Audio 0.1.4 — Windows x64 開發預覽

下載並執行 **ESP32-Audio-Setup-0.1.4.exe**，無須另裝 Python 或 Node.js。
可用 SHA256SUMS.txt 驗證下載檔案完整性。

## 功能

- Windows 聲音透過傳統 ESP32 傳送到支援 LDAC 的耳機。
- 330／660／990 kbps LDAC、AVRCP 音量與靜音、配對及中斷連線。
- 約 30 Hz 即時電平、啟動自動連線、完整 Flash 備份與新板子韌體寫入。
- 明確區分 USB／耳機錯誤，提供修正步驟。

## 使用前確認

- Windows 10／11 x64，傳統 ESP32、至少 4 MB Flash、可傳資料 USB 線。
- ESP32-S3／C3 等不適用此韌體。
- 目前僅提供 LDAC 播放，SBC／AAC 尚未實作；LDAC 為有損編碼。
- 實測 Sony WH-1000XM4，電量目前仍無法取得。
- 應用程式未做程式碼簽章，VB-CABLE 使用原廠簽署套件。

VB-CABLE 來自 https://www.vb-cable.com/ ，屬 donationware，歡迎支持原作者。
公司／專業部署請依 VB-Audio 條款取得授權。安裝可能需管理員權限或重新啟動。
詳細用法及故障排除請參考倉庫 README。
