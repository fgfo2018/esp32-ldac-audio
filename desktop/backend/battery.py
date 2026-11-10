"""Read-only Fast Pair and Sony MDR v1 battery monitoring.

Framing: https://developers.google.com/nearby/fast-pair/specifications/extensions/messagestream
Only Sony handshake and battery GET commands are sent; no settings writes.
"""
import asyncio
from bumble.rfcomm import Client,find_rfcomm_channels
from bumble.core import UUID as BluetoothUUID

UUID='df21fe2c-2515-4fdb-8886-f12c4d67927c'
SONY_UUID='96cc203e-5068-46ad-b32d-e316f5e069ba'

def sony_frame(kind,seq,payload=b''):
    body=bytes([kind,seq])+len(payload).to_bytes(4,'big')+payload
    body+=bytes([sum(body)&255])
    return b'>'+b''.join(bytes([61,x&239]) if x in (60,61,62) else bytes([x]) for x in body)+b'<'

class SonyFrames:
    """Bounded MDR v1 framing; deliver only complete, checksummed frames."""
    def __init__(self,callback):self.buffer=bytearray();self.callback=callback
    def feed(self,data):
        self.buffer.extend(data)
        while self.buffer:
            start=self.buffer.find(b'>')
            if start<0:self.buffer.clear();return
            del self.buffer[:start]
            end=self.buffer.find(b'<',1)
            if end<0:
                if len(self.buffer)>8192:self.buffer.clear()
                return
            raw=bytes(self.buffer[1:end]);del self.buffer[:end+1]
            body=bytearray();i=0
            while i<len(raw):
                value=raw[i];i+=1
                if value==61:
                    if i==len(raw) or raw[i] not in (44,45,46):break
                    value=raw[i]|16;i+=1
                body.append(value)
            else:
                if len(body)>=7 and len(body)==7+int.from_bytes(body[2:6],'big') and body[-1]==sum(body[:-1])&255 and body[1] in (0,1):
                    self.callback(body[0],body[1],bytes(body[6:-1]))

async def sony_battery(session,notify,stop):
    # MDR v1 GET_PROTOCOL_INFO / GET_BATTERY_LEVEL only; no settings writes.
    # Wire fields documented by libmdr include/mdr/packet/common_battery_level.h.
    seq=0;ack=asyncio.Event();response=asyncio.Event();expected=1
    def received(kind,rseq,payload):
        nonlocal seq
        if kind==1:seq=rseq;ack.set();return
        if kind!=12:return
        session.write(sony_frame(1,1-rseq))
        if payload and payload[0]==expected:response.set()
        if len(payload)==4 and payload[0] in (17,19) and payload[1]==0:
            level,charging=payload[2:]
            notify({'source':'Sony MDR','percent':level if level<=100 else None,'charging':bool(charging) if charging in (0,1) else None})
    session.sink=SonyFrames(received).feed
    async def query(payload,reply):
        nonlocal expected
        expected=reply;ack.clear();response.clear()
        session.write(sony_frame(12,seq,payload))
        await asyncio.wait_for(asyncio.gather(ack.wait(),response.wait()),6)
    await query(b'\x00\x00',1)
    while not stop.is_set():
        await query(b'\x10\x00',17)
        try:await asyncio.wait_for(stop.wait(),60)
        except TimeoutError:pass

class BatteryFrames:
    def __init__(self,callback):self.buffer=bytearray();self.callback=callback
    def feed(self,data):
        self.buffer.extend(data)
        while len(self.buffer)>=4:
            group,code=self.buffer[:2];length=int.from_bytes(self.buffer[2:4],'big')
            if length>4096:self.buffer.clear();return
            if len(self.buffer)<4+length:return
            payload=bytes(self.buffer[4:4+length]);del self.buffer[:4+length]
            if (group,code)!=(3,3) or len(payload) not in (1,3):continue
            def decode(value):
                level=value&127
                return {'percent':level if level<=100 else None,'charging':bool(value&128)}
            values=[decode(x) for x in payload]
            result={'source':'Fast Pair','components':dict(zip(['headset'] if len(values)==1 else ['left','right','case'],values))}
            result['percent']=values[0]['percent'] if len(values)==1 else None
            result['charging']=values[0]['charging'] if len(values)==1 else None
            self.callback(result)

async def monitor_battery(connection,notify,stop):
    client=None
    try:
        channels=await asyncio.wait_for(find_rfcomm_channels(connection),6)
        channel=next((c for c,ids in channels.items() if BluetoothUUID(UUID) in ids),None)
        sony=False
        if channel is None:
            channel=next((c for c,ids in channels.items() if BluetoothUUID(SONY_UUID) in ids),None);sony=True
        if channel is None:notify({'percent':None,'reason':'耳機未提供支援的電量通道'});return
        client=Client(connection)
        mux=await asyncio.wait_for(client.start(),5)
        session=await asyncio.wait_for(mux.open_dlc(channel),5)
        if sony:await sony_battery(session,notify,stop);return
        received=asyncio.Event()
        def update(data):received.set();notify(data)
        session.sink=BatteryFrames(update).feed
        try:await asyncio.wait_for(received.wait(),8)
        except TimeoutError:notify({'percent':None,'reason':'已連接電量通道，等待耳機回報'})
        await stop.wait()
    except asyncio.CancelledError:raise
    except Exception:notify({'percent':None,'reason':'無法取得耳機電量'})
    finally:
        if client:
            try:await asyncio.wait_for(client.shutdown(),2)
            except Exception:pass
