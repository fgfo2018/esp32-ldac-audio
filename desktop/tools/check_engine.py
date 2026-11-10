"""Bounded integration checks for the real source or bundled JSON engine."""
import argparse,json,os,queue,subprocess,sys,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--frozen',action='store_true');p.add_argument('--port',required=True);p.add_argument('--address');p.add_argument('--stream',type=int,choices=[0,1,2]);p.add_argument('--seconds',type=int,default=12);p.add_argument('--cable-melody',action='store_true');p.add_argument('--melody',help='Stereo 16-bit WAV path for --cable-melody');args=p.parse_args()
cmd=[str(ROOT/'backend-dist/esp32-audio-engine/esp32-audio-engine.exe')] if args.frozen else [sys.executable,'-u',str(ROOT/'backend/engine.py')]
proc=subprocess.Popen(cmd+['--assets',str(ROOT/'resources'),'--data',str(ROOT/'test-data')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',env={**os.environ,'PYTHONUNBUFFERED':'1','PYTHONIOENCODING':'utf-8','ESP32_AUDIO_DEBUG':'1'})
events=queue.Queue()
def read(stream,kind):
 for line in stream:
  print(kind,line.strip(),flush=True)
  if kind=='OUT':
   try:events.put(json.loads(line))
   except ValueError:pass
threading.Thread(target=read,args=(proc.stdout,'OUT'),daemon=True).start();threading.Thread(target=read,args=(proc.stderr,'ERR'),daemon=True).start()
def send(i,method,params={}):proc.stdin.write(json.dumps({'id':i,'method':method,'params':params})+'\n');proc.stdin.flush()
def until(predicate,timeout=15):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  try:item=events.get(timeout=min(1,max(.01,end-time.monotonic())))
  except queue.Empty:
   if proc.poll() is not None:raise RuntimeError('Engine exited')
   continue
  if predicate(item):return item
 raise TimeoutError('No expected engine event')
try:
 until(lambda e:e.get('type')=='ready')
 send(1,'snapshot');s=until(lambda e:e.get('id')==1);assert not s.get('error'),s
 if args.stream is not None:
  if not args.address:raise ValueError('--address is required for streaming')
  send(2,'settings',{'gain':.05,'volumeLimit':15});until(lambda e:e.get('id')==2)
  params={'port':args.port,'address':args.address,'name':args.address or 'Test headphones','quality':args.stream}
  if args.cable_melody:
   if not args.melody:raise ValueError('--melody WAV path is required')
   capture=next(o for o in s['result']['outputs'] if o['name'].startswith('CABLE Input'))
   params['capture']=str(capture['index'])
  send(3,'connect',params);until(lambda e:e.get('id')==3,25)
  state=until(lambda e:e.get('status') in ('streaming','error'),50);assert state['status']=='streaming',state
  if args.cable_melody:
   def melody():
    import wave,pyaudiowpatch as p
    with p.PyAudio() as pa:
     device=next(pa.get_device_info_by_index(i) for i in range(pa.get_device_count()) if pa.get_device_info_by_index(i)['name'].startswith('CABLE Input') and pa.get_device_info_by_index(i)['maxOutputChannels']>0 and pa.get_device_info_by_index(i)['hostApi']==pa.get_host_api_info_by_type(p.paWASAPI)['index'])
     with wave.open(args.melody,'rb') as w:
      stream=pa.open(format=pa.get_format_from_width(w.getsampwidth()),channels=w.getnchannels(),rate=w.getframerate(),output=True,output_device_index=device['index'])
      while data:=w.readframes(480):stream.write(data)
      stream.stop_stream();stream.close()
   threading.Thread(target=melody,daemon=True).start()
   until(lambda e:e.get('type')=='metrics' and e.get('input_peak',0)>.01,20)
  until(lambda e:e.get('type')=='metrics' and e.get('audio_seconds',0)>=args.seconds,args.seconds+35)
  send(4,'control',{'volume':9});until(lambda e:e.get('id')==4)
  until(lambda e:e.get('type')=='volume' and e.get('value')==9,6)
  send(5,'disconnect');until(lambda e:e.get('id')==5,15)
 else:
  send(2,'probe',{'port':args.port});s=until(lambda e:e.get('id')==2,25);assert not s.get('error'),s
 send(9,'shutdown');proc.wait(timeout=10)
 assert proc.returncode==0,proc.returncode
 print('CHECK PASSED',flush=True)
finally:
 if proc.poll() is None:proc.kill();proc.wait()
