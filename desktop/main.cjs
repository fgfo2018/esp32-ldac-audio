const {app,BrowserWindow,ipcMain,dialog,shell,Tray,Menu,nativeImage}=require('electron');
const {spawn}=require('node:child_process');
const fs=require('node:fs');const path=require('node:path');const readline=require('node:readline');
let window,worker,tray,quitting=false,stopping=false,critical=false,seq=0;let restartTimer=null,restartCount=0,reconnectPaused=false;const pending=new Map();let settings={autoConnect:false,autoStart:false,tray:true,address:'',port:'',quality:2,adaptive:false,capture:'',volumeLimit:20};
const assets=()=>app.isPackaged?path.join(process.resourcesPath,'assets'):path.join(__dirname,'resources');
function send(event){if(window&&!window.isDestroyed())window.webContents.send('audio:event',event)}
function record(event){
 if(!['status','log','engineExit','engineRecovering'].includes(event.type))return;
 try{const file=path.join(app.getPath('userData'),'runtime.log');if(fs.existsSync(file)&&fs.statSync(file).size>1024*1024)fs.renameSync(file,file+'.previous');fs.appendFileSync(file,JSON.stringify({time:new Date().toISOString(),...event})+'\n')}catch{}
}
function startWorker(){
 const exe=app.isPackaged?path.join(process.resourcesPath,'backend','esp32-audio-engine','esp32-audio-engine.exe'):'python';
 const args=app.isPackaged?[]:[path.join(__dirname,'backend','engine.py')];
 const child=spawn(exe,[...args,'--assets',assets(),'--data',app.getPath('userData')],{windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 worker=child;
 readline.createInterface({input:child.stdout}).on('line',line=>{
  try{
   const e=JSON.parse(line);
   if(e.id){const p=pending.get(e.id);if(p){pending.delete(e.id);clearTimeout(p.timer);e.error?p.reject(Error(e.error)):p.resolve(e.result)}}
   else{
    record(e);
    if(e.type==='ready')rpc('settings',{...settings,_autoPaused:reconnectPaused}).then(()=>send(e)).catch(error=>send({type:'log',message:error.message}));
    else send(e);
   }
  }catch{send({type:'log',message:line})}
 });
 child.stderr.on('data',d=>{const e={type:'log',message:d.toString()};record(e);send(e)});
 child.stdin.on('error',error=>{record({type:'log',message:'Engine input: '+error.message})});
 child.on('exit',code=>{
  if(worker!==child)return;
  for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('音訊引擎已停止'))}pending.clear();worker=null;
  const event={type:'engineExit',code};record(event);send(event);
  if(stopping){app.quit();return}
  if(!quitting&&restartCount<3){restartCount++;send({type:'engineRecovering'});record({type:'engineRecovering'});restartTimer=setTimeout(()=>{restartTimer=null;if(!quitting)startWorker()},restartCount*1500)}
 });
 child.on('error',error=>{record({type:'log',message:'Engine process: '+error.message});send({type:'engineExit',message:error.message})});
}
function rpc(method,params={}){return new Promise((resolve,reject)=>{
 if(!worker||!worker.stdin.writable||worker.stdin.destroyed)return reject(Error('音訊引擎正在恢復，請稍候'));
 const id=++seq;const timer=setTimeout(()=>{pending.delete(id);reject(Error('操作逾時，請查看診斷紀錄'))},240000);
 pending.set(id,{resolve,reject,timer});
 try{worker.stdin.write(JSON.stringify({id,method,params})+'\n',error=>{if(error){clearTimeout(timer);pending.delete(id);reject(Error('音訊引擎連線已中斷'))}})}
 catch(error){clearTimeout(timer);pending.delete(id);reject(error)}
})}
function manualDisconnect(){reconnectPaused=true;return rpc('disconnect')}
function saveSettings(){fs.writeFileSync(path.join(app.getPath('userData'),'settings.json'),JSON.stringify(settings,null,2));app.setLoginItemSettings({openAtLogin:!!settings.autoStart});rpc('settings',settings).catch(()=>{})}
async function create(){
 try{settings={...settings,...JSON.parse(fs.readFileSync(path.join(app.getPath('userData'),'settings.json'),'utf8'))}}catch{}
 window=new BrowserWindow({width:1220,height:850,minWidth:980,minHeight:720,show:!process.env.ESP32_AUDIO_SMOKE,frame:false,backgroundColor:'#f5f7fa',webPreferences:{preload:path.join(__dirname,'preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
 window.loadFile(path.join(__dirname,'ui','index.html'));window.webContents.setWindowOpenHandler(()=>({action:'deny'}));window.webContents.on('will-navigate',e=>e.preventDefault());
 window.on('close',e=>{if(!quitting&&settings.tray){e.preventDefault();window.hide()}});
 tray=new Tray(nativeImage.createFromPath(path.join(assets(),'app.png')).resize({width:16,height:16}));
 tray.setToolTip('ESP32 Audio');tray.setContextMenu(Menu.buildFromTemplate([{label:'開啟 ESP32 Audio',click:()=>window.show()},{label:'中斷連線',click:()=>manualDisconnect().catch(error=>send({type:'log',message:error.message}))},{label:'結束',click:()=>{quitting=true;app.quit()}}]));tray.on('double-click',()=>window.show());
 startWorker();window.webContents.on('did-finish-load',()=>rpc('settings',{...settings,_autoPaused:reconnectPaused}).catch(()=>{}));
 if(process.env.ESP32_AUDIO_SMOKE){
   window.webContents.on('did-finish-load',()=>setTimeout(async()=>{
     try{
       const out=path.resolve(process.env.ESP32_AUDIO_SMOKE);fs.mkdirSync(out,{recursive:true});
       for(const name of ['home','bluetooth','chip','speaker','settings']){
         await window.webContents.executeJavaScript(`page=${JSON.stringify(name)};render();`);
         await new Promise(r=>setTimeout(r,150));
         const img=await window.webContents.capturePage();fs.writeFileSync(path.join(out,name+'.png'),img.toPNG());
       }
       if(process.env.ESP32_AUDIO_ERROR_SMOKE){
         const cases={usb:{ports:[],problem:null},headphones:{ports:[{port:'COM6',name:'USB-SERIAL CH340'}],problem:{message:'ConnectionError(hci/PAGE_TIMEOUT_ERROR [0x4])'}}};
         for(const [name,fixture] of Object.entries(cases)){
           await window.webContents.executeJavaScript(`page='home';state={...state,${Object.entries(fixture).map(([k,v])=>k+':'+JSON.stringify(v)).join(',')},portsLoaded:true,status:'error'};render();`);
           await new Promise(r=>setTimeout(r,150));
           fs.writeFileSync(path.join(out,'error-'+name+'.png'),(await window.webContents.capturePage()).toPNG());
           const error=await window.webContents.executeJavaScript(`({title:document.querySelector('.problem b')?.textContent,steps:document.querySelectorAll('.problem li').length,width:document.body.scrollWidth,viewport:innerWidth})`);
           if(!error.title||error.steps<2||error.width>error.viewport)throw Error('Error banner check failed: '+JSON.stringify(error));
         }
       }
       const check=await window.webContents.executeJavaScript(`({title:document.title,pages:document.querySelectorAll('nav button').length,bodyWidth:document.body.scrollWidth,viewport:innerWidth,engine:document.querySelector('#engine-status').textContent})`);
       fs.writeFileSync(path.join(out,'smoke.json'),JSON.stringify(check,null,2));
     }catch(e){console.error(e)}finally{quitting=true;app.quit()}
   },3000));
 }
}
ipcMain.on('window',(_,action)=>{if(action==='minimize')window.minimize();if(action==='close')window.close()});
ipcMain.handle('audio:call',async(_,method,params)=>{
 const allowed=new Set(['snapshot','scan','connect','disconnect','control','probe','flash','backup','restore','driverStatus','driverInstall','driverRemove','settings','soundSettings','exportLogs','forget']);if(!allowed.has(method))throw Error('不支援的操作');
 if(method==='settings'){if(params.autoConnect===true&&!settings.autoConnect)reconnectPaused=false;settings={...settings,...params};saveSettings();return settings}
 if(method==='disconnect')return manualDisconnect();
 if(method==='connect')reconnectPaused=false;
 if(method==='scan')reconnectPaused=true;
 if(method==='forget'){const r=await dialog.showMessageBox(window,{type:'question',buttons:['取消','取消配對'],defaultId:0,message:'移除此耳機的配對資料？',detail:'下次連線需要讓耳機重新進入配對模式。'});if(r.response!==1)return null;if(settings.address===params.address){settings.address='';saveSettings()}}
 if(method==='soundSettings'){await shell.openExternal('ms-settings:sound');return true}
 if(method==='exportLogs'){const result=await dialog.showSaveDialog(window,{defaultPath:'ESP32-Audio-Diagnostics.json'});if(result.canceled)return null;const log=await rpc('exportLogs');fs.writeFileSync(result.filePath,JSON.stringify(log,null,2));return result.filePath}
 if(method==='backup'){const result=await dialog.showSaveDialog(window,{defaultPath:'ESP32-firmware-backup.bin',filters:[{name:'Firmware',extensions:['bin']}]});if(result.canceled)return null;params={...params,path:result.filePath}}
 if(method==='restore'){const result=await dialog.showOpenDialog(window,{filters:[{name:'Firmware',extensions:['bin']}],properties:['openFile']});if(result.canceled)return null;const confirm=await dialog.showMessageBox(window,{type:'warning',buttons:['取消','還原韌體'],defaultId:0,message:'還原會覆寫選定 ESP32 的韌體',detail:'請確認備份來自相容板子。'});if(confirm.response!==1)return null;params={...params,path:result.filePaths[0]}}
 if(method==='flash'){const r=await dialog.showMessageBox(window,{type:'question',buttons:['取消','備份並寫入'],defaultId:0,message:'設定這塊 ESP32 作為藍牙音訊橋接器？',detail:'會先偵測板型與備份完整 Flash，備份成功才寫入。'});if(r.response!==1)return null}
 if(['backup','flash','restore'].includes(method)){
   if(critical)throw Error('已有韌體操作進行中');critical=true;
   try{return await rpc(method,params)}finally{critical=false}
 }
 return rpc(method,params);
});
app.on('before-quit',event=>{
 if(critical){event.preventDefault();quitting=false;dialog.showMessageBoxSync(window,{type:'info',message:'裝置正在備份或寫入，請等待完成後再結束程式。'});return}
 quitting=true;clearTimeout(restartTimer);
 if(worker){event.preventDefault();if(stopping)return;stopping=true;rpc('shutdown').catch(()=>{});setTimeout(()=>{worker?.kill();app.quit()},15000)}
});
app.on('window-all-closed',()=>{if(!settings.tray)app.quit()});
if(!app.requestSingleInstanceLock())app.quit();else{app.on('second-instance',()=>window?.show());app.whenReady().then(create)}
