/* Turn device failures into concrete next steps; keep raw details expandable. */
function connectionProblem(state){
 const detail=state.problem?.detail||state.problem?.message||'';
 const make=(title,steps,action,label)=>({title,steps,action,label,detail});
 if(state.engineState==='recovering')return make('音訊引擎正在恢復',['請稍候，程式會重新啟動音訊引擎。','原本的耳機配對與設定會保留。'],'refresh','重新檢查');
 if(state.engineState==='stopped')return make('音訊引擎無法啟動',['請結束程式後重新開啟。','若仍失敗，請到「設定」匯出診斷紀錄。'],'goto-settings','查看診斷');
 if(state.portsLoaded&&!state.ports.length)return make('找不到 ESP32，請檢查 USB 連接',['將 USB 線插緊，確認板子有供電。','若仍未偵測到，換一條可傳資料的 USB 線，或改插電腦的另一個 USB 埠。',state.settings.autoConnect?'已開啟自動連線，偵測到裝置後會重新連線。':'裝置出現後，按「開始播放」重新連線。'],'refresh','重新偵測裝置');
 if(!state.problem)return null;
 if(/PAGE_TIMEOUT|耳機未回應/.test(detail))return make('ESP32 已偵測到，但耳機未回應',['確認耳機已開機並靠近 ESP32。','若耳機連著手機或其他電腦，請暫時中斷那邊的連線。','已配對耳機可直接重試；首次使用請讓耳機進入配對模式。'],'connect','重試連線');
 if(/Access is denied|PermissionError|拒絕存取|使用中|port.*busy/i.test(detail))return make('USB 序列埠無法開啟',['關閉 Arduino 序列監控、其他 ESP32 音訊程式或使用此 USB 埠的工具。','重新插入 ESP32 後再試。'],'connect','重試連線');
 if(/AUTHENTICATION|PIN_OR_KEY_MISSING|認證|驗證失敗/i.test(detail))return make('耳機配對資料無法驗證',['先確認沒有其他裝置正在連接耳機。','若重試仍失敗，到「藍牙配對」取消此耳機配對，再讓耳機進入配對模式重新配對。'],'goto-bluetooth','前往藍牙配對');
 if(/USB.*中斷|USB transport|could not open port|找不到指定的檔案|已連接的 ESP32/i.test(detail))return make('ESP32 的 USB 連線已中斷',['重新插緊 USB 線。','若電腦已偵測到板子，確認選擇的是目前的序列埠，再重試連線。'],'goto-bluetooth','檢查裝置與連線');
 if(/韌體通訊|990 kbps 需要|音訊橋接韌體/i.test(detail))return make('ESP32 韌體尚未準備好',['先到「裝置與韌體」重新偵測板子。','依該頁提示更新相容的音訊橋接韌體，再重新連線。'],'goto-chip','檢查韌體');
 if(/所選播放裝置已變更/.test(detail))return make('原本的音訊來源已不存在',['回主畫面重新選擇「音訊來源」。','再按「開始播放」。'],'goto-home','選擇音訊來源');
 if(/未提供 LDAC/.test(detail))return make('耳機已配對，但尚未提供 LDAC',['若耳機支援 LDAC，請在耳機 App 開啟音質優先，並關閉不相容的多點連線設定。','目前程式尚未提供 SBC／AAC 播放，調整後再重連。'],'goto-bluetooth','查看耳機');
 return make('操作未完成',['請先檢查 ESP32 USB 連接與耳機電源，再重試。','若持續發生，請到「設定」匯出診斷紀錄。'],'goto-settings','查看診斷');
}
