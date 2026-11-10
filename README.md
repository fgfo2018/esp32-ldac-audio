# ESP32 LDAC Audio

透過 ESP32，將 Windows 電腦的聲音傳送到支援 LDAC 的藍牙耳機。
提供 macOS 原生淺色風格的 Electron 介面、耳機配對、音量控制及新板子韌體寫入。

[下載 Windows 安裝檔](https://github.com/fgfo2018/esp32-ldac-audio/releases/latest) ·
[建置說明](docs/BUILDING.md) · [故障排除](docs/TROUBLESHOOTING.md) · [更新紀錄](CHANGELOG.md)

## 運作方式

Windows 音訊 → WASAPI loopback → PC 上的 LDAC 編碼器 → USB-UART HCI → ESP32 Bluetooth Classic → 耳機。

編碼在電腦進行，ESP32 提供藍牙控制器。這不是原生 USB Audio Class 音效卡；
虛擬音訊路由由 VB-CABLE 提供。LDAC 是有損編碼，990 kbps 也不代表無損。

## 功能

- 耳機搜尋、配對、連線與中斷連線，保留配對資料。
- LDAC 330／660／990 kbps，實驗性自動音質調整。
- 支援 AVRCP 的耳機音量、靜音控制。
- 約 30 Hz 即時音訊電平，隨音量變化，靜音歸零。
- 啟動程式／插入裝置後自動連線；手動中斷後暫停重連。
- 新 ESP32 板型偵測、完整 Flash 備份、橋接韌體寫入與驗證。
- 明確區分 USB 未連接、耳機未回應、序列埠占用等問題，提供修正步驟。
- 耳機電量欄位，無法讀取時明確標示，不會估算虛構百分比。
- VB-CABLE 安裝及移除管理，原本存在的驅動會保留。

## 系統與硬體

- Windows 10／11 x64；目前實測 Windows 10 19045。
- 傳統 ESP32、Bluetooth Classic、至少 4 MB Flash，例如 ESP32-D0WD-V3。
- 可傳資料的 USB 線，USB-UART 晶片須能穩定運作於 2 Mbps。
- 支援 LDAC 的耳機，目前實測 Sony WH-1000XM4。

ESP32-S2／S3／C3 等板子不能直接使用此韌體。目前只提供 LDAC 播放；
SBC／AAC 僅偵測能力，尚未提供播放；不支援僅有 LE Audio 的耳機。

## 安裝與使用

1. 從 [Releases](https://github.com/fgfo2018/esp32-ldac-audio/releases) 下載 `ESP32-Audio-Setup-0.1.4.exe` 並執行。
2. 可選擇一併安裝 **VB-CABLE by VB-Audio**；需要管理員權限，部分環境需要重新啟動 Windows。
3. 接上 ESP32，在「裝置與韌體」選擇正確序列埠。新板子使用「備份並設定新板子」，完整備份成功後才寫入。
4. 耳機進入配對模式，在「藍牙配對」搜尋並連線；已配對耳機之後可直接連線。
5. Windows 系統或應用程式輸出設為 **CABLE Input (VB-Audio Virtual Cable)**，程式音訊來源也選相同 CABLE Input。
6. 按「開始播放」。使用系統預設播放裝置時，原本的喇叭可能同時出聲。

應用程式安裝檔未做程式碼簽章，VB-CABLE 使用原廠簽署套件。
990 kbps 需要內附的 2 Mbps 韌體，實際穩定性受距離、干擾、USB 晶片及電腦排程影響。
330／660／990 kbps 已通過短時間測試，尚未保證所有耳機或長時間使用皆穩定。

## 已知限制

WH-1000XM4 在目前實測環境仍未回報電量，會顯示「電量無法取得」。
電量讀取提供 Fast Pair／Sony MDR v1 實作，依裝置實際支援情況而定。
手動中斷後，重新按連線、切換自動連線設定或重開程式可恢復自動連線。

## 資料與移除

不擷取麥克風、不錄製或上傳播放音訊。設定、耳機配對金鑰、韌體備份與紀錄
保存在本機使用者資料夾，不放進倉庫或安裝檔。更換 ESP32 通常需重新配對。

從 Windows 已安裝應用程式移除 ESP32 Audio。只有本程式新安裝且識別相符的
VB-CABLE 才會詢問是否移除；既有驅動及靜默移除時的驅動會保留。
其他應用程式若仍使用 VB-CABLE，請保留它。

## 開發與授權

[從原始碼建置](docs/BUILDING.md)。包含 Electron UI、Python 引擎、ESP32 韌體、
Sony 編碼器來源、測試及打包設定。

[授權說明](LICENSING.md)。VB-CABLE 來自 [VB-Audio](https://www.vb-cable.com/)，
屬 donationware，歡迎支持原作者；公司／專業部署請依原作者條款取得授權。
其他元件保留各自授權。本專案與 Sony、Espressif、Google、VB-Audio 無關聯。
