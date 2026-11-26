"""Bounded real-device check; app must release the ESP32 serial port first.

Uses an ignored local test-data directory. Never prints/copies pairing keys into
the report. Restores nothing because installed settings are not modified.
"""
import json
import argparse
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--frozen', action='store_true')
parser.add_argument('--seconds', type=int, default=55)
args = parser.parse_args()
installed = Path(os.environ['APPDATA']) / 'esp32-audio'
data = ROOT / 'test-data' / 'hfp-probe'
data.mkdir(parents=True, exist_ok=True)
for name in ('pairing.json', 'legacy-imported.json', 'baud.txt', 'peers.json'):
    if (installed / name).exists():
        shutil.copy2(installed / name, data / name)
settings = json.loads((installed / 'settings.json').read_text('utf-8'))
events = queue.Queue()
command = [str(ROOT / 'backend-dist/esp32-audio-engine/esp32-audio-engine.exe')] if args.frozen else [sys.executable, '-u', str(ROOT / 'backend/engine.py')]
proc = subprocess.Popen([*command,
    '--assets', str(ROOT / 'resources'), '--data', str(data)],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding='utf-8', errors='replace',
    env={**os.environ, 'ESP32_AUDIO_HFP_BATTERY': '1', 'PYTHONIOENCODING': 'utf-8'})

def reader(stream, stdout):
    for line in stream:
        if stdout:
            try:
                events.put(json.loads(line))
            except ValueError:
                print('NON_JSON', line.strip(), flush=True)
        else:
            print('ERR', line.strip(), flush=True)

for stream, out in ((proc.stdout, True), (proc.stderr, False)):
    threading.Thread(target=reader, args=(stream, out), daemon=True).start()

def send(identifier, method, params=None):
    proc.stdin.write(json.dumps({'id': identifier, 'method': method, 'params': params or {}})+'\n')
    proc.stdin.flush()

summary = {'battery': None, 'codec': None, 'metrics': 0, 'inputPeak': 0,
           'audioSeconds': 0, 'hfpSlc': False, 'hfpFailed': False}
started = time.monotonic()
stream_started = None
try:
    while time.monotonic() - started < 105:
        try:
            event = events.get(timeout=1)
        except queue.Empty:
            if proc.poll() is not None:
                raise RuntimeError('Engine exited')
            continue
        kind = event.get('type')
        if kind == 'ready':
            send(1, 'settings', {**settings, 'autoConnect': False, 'adaptive': False})
        elif event.get('id') == 1:
            send(2, 'connect', {'port': settings['port'], 'address': settings['address'],
                 'quality': settings.get('quality', 1), 'capture': settings.get('capture', '')})
        elif kind == 'metrics':
            summary['metrics'] += 1
            summary['audioSeconds'] = event.get('audio_seconds', 0)
            summary['inputPeak'] = max(summary['inputPeak'], event.get('input_peak', 0))
            print('METRICS', json.dumps(event), flush=True)
        elif kind == 'battery':
            print('BATTERY', json.dumps(event, ensure_ascii=False), flush=True)
            if event.get('percent') is not None:
                summary['battery'] = event
        elif kind == 'status':
            print('STATUS', json.dumps(event, ensure_ascii=False), flush=True)
            if event.get('status') == 'streaming':
                stream_started = time.monotonic(); summary['codec'] = event.get('codec')
            if event.get('status') == 'error':
                break
        elif kind == 'log':
            message = event.get('message', '')
            if not message.startswith('STREAM '):
                print('LOG', message, flush=True)
            if 'HFP SLC complete' in message: summary['hfpSlc'] = True
            if 'HFP battery unavailable' in message: summary['hfpFailed'] = True
        elif event.get('error'):
            print('ERROR', event['error'], flush=True); break
        if stream_started and time.monotonic() - stream_started >= args.seconds:
            break
finally:
    if proc.poll() is None:
        try:
            send(9, 'shutdown'); proc.wait(timeout=15)
        except Exception:
            proc.kill(); proc.wait()
    (data / 'hfp-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), 'utf-8')
    print('SUMMARY', json.dumps(summary, ensure_ascii=False), flush=True)
