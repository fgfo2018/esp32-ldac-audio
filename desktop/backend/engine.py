"""JSON-lines control process. Audio never crosses IPC and is never recorded."""
import argparse, asyncio, ctypes, hashlib, json, logging, os, re, sys, time
from quality import AdaptiveQuality
from battery import monitor_battery
from meter import PeakMeter
from pathlib import Path
from types import SimpleNamespace
import serial.tools.list_ports
import pc_audio as audio
from bumble import avrcp, avc, hci
from bumble.device import Device
from bumble.keys import JsonKeyStore
from bumble.core import AdvertisingData, PhysicalTransport
from bumble.avdtp import Protocol, MediaPacketPump
from bumble.a2dp import make_audio_source_service_sdp_records

OUT = sys.stdout
for _stream in (sys.stdout,sys.stderr):
    if hasattr(_stream,'reconfigure'):_stream.reconfigure(encoding='utf-8',errors='replace')
def emit(kind, **data):
    OUT.write(json.dumps({'type':kind, **data}, ensure_ascii=False, default=str)+'\n'); OUT.flush()

class UsbDisconnected(ConnectionError):pass

class Engine:
    def __init__(self, assets, data):
        self.assets=Path(assets); self.data=Path(data);self.data.mkdir(parents=True,exist_ok=True)
        self.task=None; self.connection=None; self.remote=None; self.encoder=None
        self.settings={}; self.log=[]; self.peers={}; self.volume=9; self.requested_volume=None
        self.status='idle';self.rssi=None;self.baud=921600;self.progress=0
        self.active_port=None;self.stats={};self.last_manual_disconnect=0
        self.packet_sink=None;self.effective_quality=2
        self.checked_port=None
        self.battery=None;self.auto_paused=False
        self.operation_lock=asyncio.Lock();self.next_retry=0.0;self.problem=None
        try:self.baud=int((self.data/'baud.txt').read_text())
        except (OSError,ValueError):pass
        try:self.peers=json.loads((self.data/'peers.json').read_text('utf-8'))
        except (OSError,ValueError):pass
        audio.print=self.forward

    def forward(self,*values,**kwargs):
        line=' '.join(map(str,values));self.log.append({'time':time.time(),'message':line});self.log=self.log[-250:]
        emit('log',message=line)
        if line.startswith('STREAM packets='):
            fields=dict(re.findall(r'(\w+)=([\d.]+)',line))
            self.stats={k:float(v) for k,v in fields.items()};emit('metrics',**self.stats,rssi=self.rssi,quality=self.effective_quality)

    def state(self,status,**extra):
        self.status=status
        self.problem={'message':extra['message'],'detail':extra.get('detail',extra['message'])} if extra.get('message') and status in ('error','waiting','paired') else None
        emit('status',status=status,problem=self.problem,**extra)
        if status!='streaming':emit('level',peak=0.0)

    def ports(self):
        return [{'port':p.device,'name':p.description,'vid':p.vid,'pid':p.pid,'serial':p.serial_number} for p in serial.tools.list_ports.comports() if p.vid is not None]

    def endpoints(self):
        import pyaudiowpatch as p
        with p.PyAudio() as pa:
            return [{'index':d['index'],'name':d['name'].replace(' [Loopback]',''),'rate':int(d['defaultSampleRate']),'channels':d['maxInputChannels']} for d in pa.get_loopback_device_info_generator()]

    async def snapshot(self,_):
        return {'ports':self.ports(),'outputs':await asyncio.to_thread(self.endpoints),'status':self.status,'peers':self.peers,'volume':self.volume,'battery':self.battery,'problem':self.problem,'baud':self.baud,'settings':self.settings,'stats':self.stats,'driver':await self.driverStatus({})}

    def get_port(self,p):
        port=p.get('port') or self.settings.get('port')
        available=self.ports()
        if not port and len(available)==1:port=available[0]['port']
        if port not in [x['port'] for x in available]:raise ValueError('請選擇已連接的 ESP32 USB 序列埠')
        return port

    async def stop(self):
        self.last_manual_disconnect=time.monotonic()
        if self.task and not self.task.done():
            self.task.cancel()
            try:await self.task
            except (asyncio.CancelledError,Exception):pass
        self.task=None;self.encoder=None;self.connection=None;self.remote=None;self.packet_sink=None;self.state('idle')
        self.battery=None;emit('battery',percent=None,connected=False)

    async def disconnect(self,_):
        self.auto_paused=True
        await self.stop();return True

    async def make_device(self,tr):
        config=self.data/'device.json'
        config.write_text(json.dumps({'name':'ESP32 Audio','class_of_device':2098184,'keystore':f'JsonKeyStore:{self.data / "pairing.json"}'}),'utf-8')
        d=Device.from_config_file_with_hci(str(config),tr.source,tr.sink)
        d.classic_enabled=True;d.le_enabled=False
        d.sdp_service_records={0x10001:make_audio_source_service_sdp_records(0x10001),0x10002:avrcp.ControllerServiceSdpRecord(0x10002).to_service_attributes()}
        remote=avrcp.Protocol();remote.listen(d)
        if os.environ.get('ESP32_AUDIO_DEBUG'):
            original=d.host.send_sync_command
            async def trace(command,*args,**kwargs):
                self.forward('HCI command',command.name)
                result=await original(command,*args,**kwargs)
                self.forward('HCI completed',command.name)
                return result
            d.host.send_sync_command=trace
        await asyncio.wait_for(d.power_on(),12)
        # Reuse pairing from the earlier local CLI, for this controller only.
        # Never ship keys, overwrite current keys, or reimport a forgotten peer.
        marker=self.data/'legacy-imported.json'
        imported=json.loads(marker.read_text('utf-8')) if marker.exists() else []
        namespace=str(d.public_address)
        legacy=JsonKeyStore(namespace=namespace).filename
        target=self.data/'pairing.json'
        if namespace not in imported and legacy.exists() and legacy.resolve()!=target.resolve():
            db=json.loads(target.read_text('utf-8')) if target.exists() else {}
            old=json.loads(legacy.read_text('utf-8')).get(namespace,{})
            current=db.setdefault(namespace,{})
            for key,value in old.items():
                if key not in current:current[key]=value
                address=key.split('/')[0]
                self.peers.setdefault(address,{'name':'先前已配對的耳機','codecs':[]})
            target.write_text(json.dumps(db),'utf-8')
            (self.data/'peers.json').write_text(json.dumps(self.peers,ensure_ascii=False),'utf-8')
            marker.write_text(json.dumps(imported+[namespace]),'utf-8')
            emit('peers',peers=self.peers)
        return d,remote

    async def _guard(self,coro):
        try:await coro
        except asyncio.CancelledError:raise
        except Exception as e:
            self.checked_port=None;self.next_retry=time.monotonic()+10
            self.forward(type(e).__name__,str(e))
            if isinstance(e,UsbDisconnected):self.state('waiting',message='ESP32 USB 已中斷，請重新插入裝置。')
            elif 'PAGE_TIMEOUT' in str(e):self.state('error',detail=str(e),message='耳機未回應連線。請確認耳機已開機，並暫時中斷手機連線；自動連線開啟時會稍後重試。')
            else:self.state('error',message=str(e),detail=type(e).__name__+': '+str(e))
        finally:
            self.encoder=None;self.remote=None;self.connection=None;self.packet_sink=None;self.active_port=None
            self.battery=None;emit('battery',percent=None,connected=False)

    async def transport_session(self,tr,session):
        # A removed USB controller cannot produce a Bluetooth disconnection event.
        # Race transport termination against the ENTIRE session, including pairing.
        task=asyncio.create_task(session)
        try:
            done,_=await asyncio.wait([task,tr.source.terminated],return_when=asyncio.FIRST_COMPLETED)
            if tr.source.terminated in done:
                self.checked_port=None
                raise UsbDisconnected('USB transport terminated')
            return await task
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)

    async def probe(self,p):
        if self.task and not self.task.done():raise ValueError('請先中斷音訊連線')
        port=self.get_port(p)
        for baud in (2000000,921600):
            try:
                async with await audio.transport(port,baud) as tr:
                    device,_=await asyncio.wait_for(self.make_device(tr),5)
                    self.baud=baud;self.checked_port=port;(self.data/'baud.txt').write_text(str(baud));emit('controller',baud=baud,address=str(device.public_address));return {'port':port,'baud':baud,'address':str(device.public_address)}
            except (TimeoutError,Exception) as e:
                self.forward(f'Probe {baud}: {type(e).__name__}')
                await asyncio.sleep(.3)
        raise RuntimeError('無法與音訊橋接韌體通訊。請到裝置與韌體頁寫入韌體。')

    async def scan(self,p):
        self.auto_paused=True
        await self.stop();port=self.get_port(p)
        if self.checked_port!=port:await self.probe({'port':port})
        self.active_port=port;self.state('scanning')
        self.task=asyncio.create_task(self._guard(self._scan(port)));return True

    async def _scan(self,port):
        async with await audio.transport(port,self.baud) as tr:
            await self.transport_session(tr,self._scan_session(tr))

    async def _scan_session(self,tr):
        d,_=await self.make_device(tr)
        done=asyncio.Event();count=0
        d.on('inquiry_complete',lambda:done.set())
        def found(addr,cod,data,rssi):
            nonlocal count
            count+=1
            name=data.get(AdvertisingData.COMPLETE_LOCAL_NAME) or data.get(AdvertisingData.SHORTENED_LOCAL_NAME) or '未命名藍牙裝置'
            emit('found',address=str(addr).split('/')[0],name=name,rssi=rssi)
        d.on('inquiry_result',found)
        await d.start_discovery(auto_restart=False)
        try:await asyncio.wait_for(done.wait(),15)
        except TimeoutError:await d.stop_discovery()
        self.state('idle',message='搜尋完成。沒有找到裝置，請讓耳機進入 Bluetooth pairing 模式；已配對的耳機可直接連線。' if count==0 else f'搜尋完成，收到 {count} 筆裝置回應。')

    async def connect(self,p):
        await self.stop();address=p.get('address') or self.settings.get('address','')
        if not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}',address):raise ValueError('請選擇耳機')
        port=self.get_port(p);quality=int(p.get('quality',self.settings.get('quality',2)))
        if quality not in (0,1,2):raise ValueError('不支援的音質選項')
        if self.checked_port!=port:await self.probe({'port':port})
        if quality==0 and self.baud<1500000:raise ValueError('990 kbps 需要新版 2 Mbps 橋接韌體；請先更新並偵測裝置')
        self.settings.update({'address':address,'port':port,'quality':quality,'capture':p.get('capture',self.settings.get('capture',''))})
        self.auto_paused=False
        self.active_port=port;self.state('connecting',address=address)
        self.task=asyncio.create_task(self._guard(self._play(port,address,p)));return True

    async def _play(self,port,address,p):
        async with await audio.transport(port,self.baud) as tr:
            await self.transport_session(tr,self._play_session(tr,port,address,p))

    async def _play_session(self,tr,port,address,p):
        self.packet_sink=tr.sink
        d,remote=await self.make_device(tr)
        connection=await asyncio.wait_for(d.connect(address,transport=PhysicalTransport.BR_EDR),30)
        self.connection=connection
        ended=asyncio.Event();connection.on('disconnection',lambda *a:ended.set())
        await asyncio.wait_for(connection.authenticate(),30);await asyncio.wait_for(connection.encrypt(),10)
        try:p['name']=await asyncio.wait_for(d.request_remote_name(connection),5)
        except Exception:pass
        self.state('paired',address=address)
        try:
            if not remote.avctp_protocol:await asyncio.wait_for(remote.connect(connection),5)
            self.remote=remote
            monitor=remote.monitor_volume();self.volume=round((await asyncio.wait_for(anext(monitor),4))*100/127);await monitor.aclose()
            limit=int(self.settings.get('volumeLimit',20))
            if self.volume>limit:
                response=await asyncio.wait_for(remote.send_avrcp_command(avc.CommandFrame.CommandType.CONTROL,avrcp.SetAbsoluteVolumeCommand(round(limit*127/100))),3)
                if isinstance(response.response,avrcp.SetAbsoluteVolumeResponse):self.volume=round(response.response.volume*100/127)
            emit('volume',value=self.volume,hardware=True)
        except Exception as e:self.forward('AVRCP unavailable:',e);self.remote=None
        protocol=await asyncio.wait_for(Protocol.connect(connection),10)
        endpoints=await asyncio.wait_for(protocol.discover_remote_endpoints(),15);codecs=[]
        for ep in endpoints:
            for cap in ep.capabilities:
                if hasattr(cap,'media_codec_type'):
                    info=getattr(cap,'media_codec_information',None)
                    name='LDAC' if getattr(info,'vendor_id',None)==301 and getattr(info,'codec_id',None)==170 else {0:'SBC',2:'AAC'}.get(int(cap.media_codec_type))
                    if name and name not in codecs:codecs.append(name)
        self.peers[address]={'name':p.get('name') or self.peers.get(address,{}).get('name',address),'codecs':codecs}
        (self.data/'peers.json').write_text(json.dumps(self.peers,ensure_ascii=False),'utf-8')
        emit('capabilities',address=address,codecs=codecs,implemented=['LDAC'])
        sink=protocol.find_remote_sink_by_codec(0,255,301,170)
        if sink is None:
            self.state('paired',message='配對成功；此耳機目前未提供 LDAC。SBC 播放尚在開發。');await connection.disconnect();return
        encoder=audio.Encoder(48000,int(self.settings.get('quality',2)));self.encoder=encoder
        try:
            encoder.meter=PeakMeter()
            self.effective_quality=int(self.settings.get('quality',2))
            encoder.gain=float(self.settings.get('gain',1.0)) if self.remote else min(float(self.settings.get('gain',1.0)),int(self.settings.get('volumeLimit',20))/100)
            selected=self.settings.get('capture','')
            if selected!='':
                outputs=await asyncio.to_thread(self.endpoints)
                matched=next((x for x in outputs if str(x['index'])==str(selected)),None)
                if not matched:raise ValueError('所選播放裝置已變更，請重新選擇音訊來源')
                encoder.capture_index=int(selected)
            packets=audio.packets(encoder,86400,True,1.0)
            pump=MediaPacketPump(packets)
            source=protocol.add_source(audio.caps(48000),pump)
            stream=await asyncio.wait_for(protocol.create_stream(source,sink),10)
            controls=None
            levels=None
            battery_task=None
            controls_stop=asyncio.Event()
            try:
                await asyncio.wait_for(stream.start(),15);self.state('streaming',address=address,codec='LDAC',rate=48000,quality=self.settings.get('quality',2))
                controls=asyncio.create_task(self.control_loop(d,connection,encoder,controls_stop))
                levels=asyncio.create_task(self.meter_loop(encoder.meter,controls_stop))
                def battery_update(value):
                    self.battery={**value,'address':address,'updatedAt':time.time(),'connected':True}
                    emit('battery',**self.battery)
                battery_task=asyncio.create_task(monitor_battery(connection,battery_update,controls_stop))
                waiter=asyncio.create_task(pump.wait_for_completion());lost=asyncio.create_task(ended.wait())
                try:await asyncio.wait([waiter,lost],return_when=asyncio.FIRST_COMPLETED)
                finally:
                    waiter.cancel();lost.cancel()
                if pump.pump_task and pump.pump_task.done() and not pump.pump_task.cancelled() and pump.pump_task.exception():raise pump.pump_task.exception()
            finally:
                controls_stop.set()
                if levels:
                    try:await levels
                    except (Exception,asyncio.CancelledError):pass
                if tr.source.terminated.done():
                    for child in (controls,battery_task):
                        if child:child.cancel()
                if controls:
                    # Do not cancel an in-flight HCI command and immediately send
                    # Disconnect: its late response can be mistaken for Disconnect.
                    controls_stop.set()
                    try:await asyncio.wait_for(controls,5)
                    except (Exception,asyncio.CancelledError):pass
                if battery_task:
                    try:await asyncio.wait_for(battery_task,3)
                    except (Exception,asyncio.CancelledError):pass
                try:await pump.stop()
                finally:
                    try:await packets.aclose()
                    finally:encoder.close();self.encoder=None
                if not tr.source.terminated.done():
                    try:await asyncio.wait_for(connection.disconnect(),3)
                    except Exception:pass
                if self.status=='streaming':self.state('idle')

        finally:encoder.close()

    async def meter_loop(self,meter,stop):
        # Independent of RSSI/volume commands and the slow diagnostic log.
        while not stop.is_set():
            try:await asyncio.wait_for(stop.wait(),1/30)
            except TimeoutError:emit('level',peak=meter.take())
        emit('level',peak=0.0)

    async def control_loop(self,d,conn,encoder,stop):
        last_rssi=0;last_quality=int(self.settings.get('quality',2))
        policy=AdaptiveQuality(last_quality)
        encoder.dll.ldacBT_set_eqmid.argtypes=[ctypes.c_void_p,ctypes.c_int]
        while not stop.is_set():
            await asyncio.sleep(.2)
            if stop.is_set():break
            if self.requested_volume is not None:
                desired=self.requested_volume;self.requested_volume=None
                if self.remote:
                    try:
                        r=await asyncio.wait_for(self.remote.send_avrcp_command(avc.CommandFrame.CommandType.CONTROL,avrcp.SetAbsoluteVolumeCommand(round(desired*127/100))),3)
                        if not isinstance(r.response,avrcp.SetAbsoluteVolumeResponse):raise RuntimeError('耳機拒絕音量指令')
                        self.volume=round(r.response.volume*100/127);emit('volume',value=self.volume,hardware=True)
                    except Exception as e:self.forward('Volume:',e)
                else:encoder.gain=desired/100;self.volume=desired;emit('volume',value=desired,hardware=False)
            if stop.is_set():break
            if time.monotonic()-last_rssi>3:
                last_rssi=time.monotonic()
                try:
                    r=await asyncio.wait_for(d.send_sync_command(hci.HCI_Read_RSSI_Command(handle=conn.handle)),2)
                    # Classic HCI RSSI is a relative link metric, NOT calibrated dBm.
                    self.rssi=r.rssi;emit('signal',relativeRssi=self.rssi)
                except Exception:pass
            sink_transport=getattr(self.packet_sink,'transport',None)
            backlog=sink_transport.get_write_buffer_size() if sink_transport else 0
            desired=policy.choose(int(self.settings.get('quality',2)),bool(self.settings.get('adaptive')),self.baud,self.rssi,self.stats.get('capture_drops',0),backlog,time.monotonic())
            if desired!=last_quality:
                if desired==0 and self.baud<1500000:desired=1
                if encoder.dll.ldacBT_set_eqmid(encoder.handle,desired)==0:
                    last_quality=desired;self.effective_quality=desired;emit('quality',value=desired,adaptive=bool(self.settings.get('adaptive')))

    async def control(self,p):
        if 'volume' in p:
            value=int(p['volume'])
            if not 0<=value<=100:raise ValueError('音量範圍為 0–100')
            self.requested_volume=value
        if 'quality' in p:
            q=int(p['quality'])
            if q not in (0,1,2):raise ValueError('不支援的位元率')
            if q==0 and self.baud<1500000:raise ValueError('990 kbps 需要 2 Mbps 韌體')
            self.settings['quality']=q
        if 'adaptive' in p:self.settings['adaptive']=bool(p['adaptive'])
        return True

    async def settings_update(self,p):
        if p.get('autoConnect') and not self.settings.get('autoConnect'):self.auto_paused=False
        if '_autoPaused' in p:self.auto_paused=bool(p['_autoPaused'])
        self.settings.update({k:v for k,v in p.items() if k!='_autoPaused'});return self.settings

    async def forget(self,p):
        address=p.get('address','').upper()
        if not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}',address):raise ValueError('無效的裝置位址')
        await self.stop()
        file=self.data/'pairing.json'
        if file.exists():
            db=json.loads(file.read_text('utf-8'))
            for peers in db.values():
                if isinstance(peers,dict):
                    for key in list(peers):
                        if key.split('/')[0].upper()==address:del peers[key]
            file.write_text(json.dumps(db),'utf-8')
        self.peers.pop(address,None)
        (self.data/'peers.json').write_text(json.dumps(self.peers,ensure_ascii=False),'utf-8')
        if self.settings.get('address','').upper()==address:self.settings['address']=''
        return True

    async def tool(self,args):
        # Run esptool inside the frozen executable as a separate process.
        command=[sys.executable,'--esptool'] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__)),'--esptool']
        proc=await asyncio.create_subprocess_exec(*command,*map(str,args),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT,creationflags=0x08000000)
        text=[]
        while line:=await proc.stdout.readline():
            s=line.decode('utf-8','replace').strip();text.append(s)
            if s:self.forward(s);emit('progress',message=s)
        if await proc.wait():raise RuntimeError('\n'.join(text[-8:]))
        return '\n'.join(text)

    async def backup(self,p):
        await self.stop();port=self.get_port(p);self.state('working')
        try:
            info=await self.tool(['--port',port,'flash-id'])
            match=re.search(r'Detected flash size:\s*(\d+)MB',info)
            if not match:raise RuntimeError('無法辨識 Flash 容量，取消備份')
            size=int(match[1])*1024*1024
            if size>16*1024*1024:raise RuntimeError('此容量尚未支援')
            dest=Path(p.get('path') or self.data/'backups'/f'ESP32-{int(time.time())}.bin');dest.parent.mkdir(parents=True,exist_ok=True)
            partial=dest.with_name(dest.name+'.partial')
            await self.tool(['--port',port,'--baud','460800','read-flash','0',hex(size),partial])
            if partial.stat().st_size!=size:raise RuntimeError('備份大小不符，取消後續寫入')
            partial.replace(dest)
            return {'path':str(dest),'size':size}
        finally:self.state('idle')

    async def flash(self,p):
        port=self.get_port(p)
        await self.stop()
        # Force chip verification before any write; do not flash S3/C3/S31 using classic ESP32 images.
        info=await self.tool(['--chip','esp32','--port',port,'chip-id'])
        backup=await self.backup({'port':port})
        if backup['size']<4*1024*1024:raise RuntimeError('這份韌體需要至少 4 MB Flash，已保留備份但不寫入')
        self.state('working')
        try:
            root=self.assets/'firmware'
            manifest=json.loads((root/'manifest.json').read_text('utf-8'))
            for name,digest in manifest.items():
                if hashlib.sha256((root/name).read_bytes()).hexdigest()!=digest:raise RuntimeError('韌體完整性檢查失敗，取消寫入')
            await self.tool(['--chip','esp32','--port',port,'--baud','460800','write-flash','0x1000',root/'bootloader.bin','0x8000',root/'partitions.bin','0xe000',root/'boot_app0.bin','0x10000',root/'firmware.bin'])
            result=await self.probe({'port':port});return {'backup':backup,**result}
        finally:self.state('idle')

    async def restore(self,p):
        await self.stop();port=self.get_port(p);file=Path(p['path'])
        if file.suffix.lower()!='.bin' or not file.is_file() or not 1024*1024<=file.stat().st_size<=16*1024*1024:raise ValueError('請選擇完整 Flash 備份檔')
        info=await self.tool(['--chip','esp32','--port',port,'flash-id'])
        match=re.search(r'Detected flash size:\s*(\d+)MB',info)
        if not match or file.stat().st_size!=int(match[1])*1024*1024:raise ValueError('備份容量與板子的 Flash 容量不符，取消還原')
        await self.tool(['--chip','esp32','--port',port,'--baud','460800','write-flash','0',file]);return True

    async def driverStatus(self,_):
        owned=Path(os.environ.get('PROGRAMDATA',r'C:\ProgramData'))/'ESP32Audio'/'driver.json'
        outputs=await asyncio.to_thread(self.endpoints)
        installed=any('CABLE Input' in d['name'] for d in outputs)
        return {'installed':installed,'owned':owned.exists(),'provider':'VB-CABLE / VB-Audio','mayRequireRestart':False}

    async def driver(self,mode):
        await self.stop()
        proc=await asyncio.create_subprocess_exec('powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(self.assets/'driver.ps1'),'-Mode',mode,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT,creationflags=0x08000000)
        out,_=await proc.communicate();self.forward(out.decode('utf-8','replace'))
        if proc.returncode:raise RuntimeError('驅動操作未完成，請確認系統管理員提示或查看紀錄')
        return await self.driverStatus({})
    async def driverInstall(self,_):return await self.driver('install')
    async def driverRemove(self,_):return await self.driver('remove')
    async def exportLogs(self,_):return {'version':'0.1.4','log':self.log,'ports':self.ports(),'status':self.status,'stats':self.stats}

    async def autoconnect(self):
        previous_ports=None
        while True:
            await asyncio.sleep(1)
            try:
                current_ports=self.ports();names={p['port'] for p in current_ports}
                if self.checked_port not in names:self.checked_port=None
                if current_ports!=previous_ports:
                    old_names={p['port'] for p in previous_ports or []}
                    if names-old_names:self.next_retry=0
                    emit('ports',ports=current_ports);previous_ports=current_ports
                if self.active_port and self.active_port not in names and self.task and not self.task.done():
                    async with self.operation_lock:
                        await self.stop();self.checked_port=None;self.next_retry=0
                        self.state('waiting',message='ESP32 USB 已中斷，等待裝置重新插入。')
                if not self.auto_paused and self.settings.get('autoConnect') and self.settings.get('address') and self.status in ('idle','error','waiting') and time.monotonic()>=self.next_retry:
                    port=self.settings.get('port')
                    if not port and len(current_ports)==1:port=current_ports[0]['port']
                    if port and port in names:
                        async with self.operation_lock:
                            if self.status in ('idle','error','waiting') and not self.auto_paused:
                                self.next_retry=time.monotonic()+10
                                try:await self.connect({'port':port})
                                except Exception as e:
                                    self.checked_port=None;self.forward('Auto-connect:',e)
                                    self.state('error',message='重新連線未完成，稍後會自動重試。')
            except asyncio.CancelledError:raise
            except Exception as e:self.forward('USB monitor:',e)

    async def serve(self):
        emit('ready');auto=asyncio.create_task(self.autoconnect())
        try:
            while line:=await asyncio.to_thread(sys.stdin.readline):
                try:
                    req=json.loads(line);method=req['method'];p=req.get('params',{})
                    if method=='shutdown':await self.stop();break
                    functions={k:getattr(self,k) for k in ['snapshot','scan','connect','disconnect','control','probe','backup','flash','restore','driverStatus','driverInstall','driverRemove','exportLogs','forget']}
                    functions['settings']=self.settings_update
                    if method not in functions:raise ValueError('Unsupported operation')
                    if method in ('snapshot','control','settings','driverStatus','exportLogs'):
                        result=await functions[method](p)
                    else:
                        async with self.operation_lock:result=await functions[method](p)
                    OUT.write(json.dumps({'id':req['id'],'result':result},ensure_ascii=False,default=str)+'\n');OUT.flush()
                except Exception as e:
                    OUT.write(json.dumps({'id':req.get('id',-1),'error':str(e)},ensure_ascii=False)+'\n');OUT.flush()
        finally:auto.cancel();await self.stop()

if __name__=='__main__':
    if '--esptool' in sys.argv:
        import esptool
        sys.argv=sys.argv[sys.argv.index('--esptool'):];esptool._main();sys.exit()
    p=argparse.ArgumentParser();p.add_argument('--assets',required=True);p.add_argument('--data',required=True);a=p.parse_args()
    logging.basicConfig(level=logging.WARNING,stream=sys.stderr)
    asyncio.run(Engine(a.assets,a.data).serve())
