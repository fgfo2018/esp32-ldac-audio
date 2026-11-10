# 從原始碼建置

需要 Windows x64、Python 3.12、Node.js（建議 22 LTS）和 Git。

```powershell
git clone https://github.com/fgfo2018/esp32-ldac-audio.git
cd esp32-ldac-audio
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r desktop/requirements.txt
cd desktop
npm ci
npm run check
python -m unittest discover -s tools -p 'test_*.py'
node tools/check_problems.cjs
powershell -ExecutionPolicy Bypass -File build-engine.ps1
npm run pack
```

輸出：desktop/dist/ESP32-Audio-Setup-0.1.4.exe。
內附通用編碼器 DLL、ESP32 韌體映像和原廠 VB-CABLE 套件，沒有個人配對資料。
Bumble 固定於已驗證的 Git commit，Python 版本列在 requirements.txt。
開發模式在 desktop 執行 `npm start`；使用 Python 原始碼，無須先打包引擎。

## 重建 Sony LDAC DLL

確認 MinGW-w64 GCC 在 PATH，從倉庫根目錄執行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-ldac.ps1
```

來源位於 vendor/libldac，保留 Apache-2.0 LICENSE／NOTICE。
會更新 desktop/backend/ldacBT_enc.dll，之後須重新打包引擎與安裝檔。

## 重建韌體

```powershell
python -m pip install platformio==6.2.0
python -m platformio run --project-dir firmware
powershell -ExecutionPolicy Bypass -File scripts/sync-firmware.ps1
```

平台版本固定於 firmware/platformio.ini。sync-firmware 會更新通用映像及 SHA-256 manifest。
直接 PlatformIO upload 會覆寫 Flash；一般使用者請由桌面程式先備份再寫入。

## 實機測試

一般測試不需要硬體。實機測試請先關閉使用序列埠的程式，填入自己的埠及耳機地址：

```powershell
cd desktop
python tools/check_engine.py --frozen --port COM6 --address AA:BB:CC:DD:EE:FF --stream 2 --seconds 12
python tools/check_meter.py --port COM6 --address AA:BB:CC:DD:EE:FF
```

配對資料保存在被忽略的 desktop/test-data，切勿提交。
Meter 測試往 VB-CABLE 播放測試音，其引擎增益設為零。
使用 --cable-melody 時需另傳 --melody，指定自己的 stereo 16-bit WAV。

## 發布

安裝檔放 GitHub Releases，不提交到 Git 歷史。
SHA256SUMS.txt 供檢查檔案完整性。發布前執行測試、檢查打包來源和授權，
確認沒有使用者設定、配對金鑰或 Flash 備份。
