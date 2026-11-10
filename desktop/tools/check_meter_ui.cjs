/* Real Chromium RAF cadence and meter attack/decay, with synthetic input only. */
const {app,BrowserWindow}=require('electron');
const fs=require('node:fs'),path=require('node:path');
app.whenReady().then(async()=>{
 let window;
 try{
  window=new BrowserWindow({show:true,width:500,height:130,webPreferences:{backgroundThrottling:false,contextIsolation:true,sandbox:true}});
  await window.loadURL('data:text/html,<div role="meter"><i id="level"></i></div>');
  await window.webContents.executeJavaScript(fs.readFileSync(path.join(__dirname,'../ui/meter.js'),'utf8'));
  const result=await window.webContents.executeJavaScript(`(async()=>{
   const meter=new AudioMeter(),element=document.querySelector('#level');
   meter.attach(element,true);await new Promise(r=>setTimeout(r,400));
   let input=.8;const feed=setInterval(()=>meter.update(input),1000/30);
   await new Promise(r=>setTimeout(r,250));const loud=meter.value;
   input=.001;await new Promise(r=>setTimeout(r,300));const quiet=meter.value;
   clearInterval(feed);
   let frames=0;const start=performance.now();const original=meter.paint.bind(meter);
   meter.paint=()=>{frames++;original()};
   await new Promise(r=>setTimeout(r,1200));
   const fps=frames*1000/(performance.now()-start),silence=meter.value;
   meter.update(.9);await new Promise(r=>setTimeout(r,90));meter.attach(element,false);
   return {fps,loud,quiet,silence,disconnected:meter.value,transform:element.style.transform};
  })()`);
  if(result.fps<15||result.loud<=result.quiet||result.silence>.002||result.disconnected!==0)throw Error(JSON.stringify(result));
  console.log(JSON.stringify(result));app.exit(0);
 }catch(error){console.error(error);app.exit(1)}
});
