"""Bounded real WASAPI -> LDAC worker meter check; test signal is muted at encoder."""
import argparse,json,os,queue,subprocess,threading,time
from pathlib import Path
import numpy as np
import pyaudiowpatch as p

parser=argparse.ArgumentParser();parser.add_argument('--port',required=True);parser.add_argument('--address',required=True);args=parser.parse_args()
ROOT=Path(__file__).resolve().parents[1]
proc=subprocess.Popen([str(ROOT/'backend-dist/esp32-audio-engine/esp32-audio-engine.exe'),'--assets',str(ROOT/'resources'),'--data',str(ROOT/'test-data')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',env={**os.environ,'PYTHONIOENCODING':'utf-8'})
events=queue.Queue();stop=threading.Event();output_errors=[]
def read():
    for line in proc.stdout:
        try:events.put((time.monotonic(),json.loads(line)))
        except ValueError:pass
threading.Thread(target=read,daemon=True).start()
def send(i,method,params={}):
    proc.stdin.write(json.dumps({'id':i,'method':method,'params':params})+'\n');proc.stdin.flush()
def until(predicate,seconds=20):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        try:stamp,event=events.get(timeout=.5)
        except queue.Empty:continue
        if event.get('status')=='error':raise RuntimeError(event)
        if predicate(event):return event
    raise TimeoutError('Expected worker event')
def output():
    try:
        with p.PyAudio() as pa:
            host=pa.get_host_api_info_by_type(p.paWASAPI)['index']
            device=next(pa.get_device_info_by_index(i) for i in range(pa.get_device_count()) if pa.get_device_info_by_index(i)['hostApi']==host and pa.get_device_info_by_index(i)['maxOutputChannels']>0 and pa.get_device_info_by_index(i)['name'].startswith('CABLE Input'))
            rate=int(device['defaultSampleRate']);size=rate//100;phase=0
            stream=pa.open(format=p.paFloat32,channels=2,rate=rate,output=True,output_device_index=device['index'],frames_per_buffer=size)
            try:
                while not stop.is_set():
                    # One second loud, one second quiet, one second silent.
                    amplitude=[.12,.005,0][(phase//rate)%3]
                    samples=(amplitude*np.sin(2*np.pi*440*(np.arange(size)+phase)/rate)).astype(np.float32)
                    stream.write(np.repeat(samples[:,None],2,axis=1).tobytes());phase+=size
            finally:stream.stop_stream();stream.close()
    except Exception as error:output_errors.append(str(error))
try:
    until(lambda e:e.get('type')=='ready')
    send(1,'snapshot');snapshot=until(lambda e:e.get('id')==1)['result']
    capture=next(o for o in snapshot['outputs'] if o['name'].startswith('CABLE Input'))
    send(2,'settings',{'gain':0.0,'autoConnect':False});until(lambda e:e.get('id')==2)
    send(3,'connect',{'port':args.port,'address':args.address,'quality':0,'capture':str(capture['index'])})
    until(lambda e:e.get('status')=='streaming',50)
    writer=threading.Thread(target=output,daemon=True);writer.start()
    samples=[];start=time.monotonic();end=start+8
    while time.monotonic()<end:
        try:stamp,event=events.get(timeout=.5)
        except queue.Empty:continue
        if event.get('type')=='level':samples.append((stamp,event['peak']))
        if event.get('status')=='error':raise RuntimeError(event)
    stop.set();writer.join(3)
    if output_errors:raise RuntimeError(output_errors)
    assert len(samples)>1,'No level events'
    rate=(len(samples)-1)/(samples[-1][0]-samples[0][0])
    peaks=[value for _,value in samples];gap=max(b[0]-a[0] for a,b in zip(samples,samples[1:]))
    assert rate>=15,(rate,len(samples))
    assert max(peaks)>.08,'Test audio not captured'
    assert min(peaks)<max(peaks)/4,'Meter failed to fall after a loud passage'
    send(4,'disconnect');until(lambda e:e.get('id')==4,16)
    print(json.dumps({'events':len(samples),'updates_per_second':round(rate,2),'max_gap_ms':round(gap*1000,1),'min_peak':min(peaks),'max_peak':max(peaks),'quality_kbps':990,'result':'PASS'}),flush=True)
finally:
    stop.set()
    if proc.poll() is None:
        send(9,'shutdown')
        try:proc.wait(timeout=16)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
