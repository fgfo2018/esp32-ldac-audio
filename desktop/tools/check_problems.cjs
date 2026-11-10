const fs=require('fs'),vm=require('vm'),assert=require('assert');
const ctx=vm.createContext({});vm.runInContext(fs.readFileSync('ui/problems.js','utf8'),ctx);
const state={ports:[{port:'COM6'}],portsLoaded:true,status:'error',settings:{autoConnect:true}};
function check(problem,expected,extra={}){
 ctx.state={...state,...extra,problem:problem?{message:problem}:null};
 const result=vm.runInContext('connectionProblem(state)',ctx);
 assert(result.title.includes(expected),result.title);assert(result.steps.length>=2);
 return result;
}
check(null,'找不到 ESP32',{ports:[]});
check('ConnectionError(hci/PAGE_TIMEOUT_ERROR [0x4])','耳機未回應');
check("PermissionError(13, 'Access is denied')",'USB 序列埠');
check('AUTHENTICATION_FAILURE','配對資料');
check('USB transport terminated','USB 連線已中斷');
check('無法與音訊橋接韌體通訊','韌體');
check('所選播放裝置已變更','音訊來源');
check('配對成功；此耳機目前未提供 LDAC','LDAC');
check(null,'正在恢復',{engineState:'recovering'});
ctx.state={...state,status:'streaming',problem:null};assert.equal(vm.runInContext('connectionProblem(state)',ctx),null);
console.log('PASS: actionable USB/Bluetooth/port/pairing/firmware/audio/engine errors and recovered state');
