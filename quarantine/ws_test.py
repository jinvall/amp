#!/usr/bin/env python3
"""Standalone WS consumer test against a running receiver on 127.0.0.1:8082.

Also opens a fake PCM TCP source so frames actually flow.
"""
import socket, struct, math, time, threading, json, sys
import asyncio, websockets

HOST='127.0.0.1'; PCM=8080; VIZ=8082; SR=44100

s=socket.socket(); s.connect((HOST,PCM)); s.sendall(b'{"client":"verify"}\n')
def prod():
    t=0.0; n=2048; f=440.0
    while True:
        b=bytearray()
        for i in range(n):
            b+=struct.pack('<h', int(30000*math.sin(2*math.pi*f*(t+i/SR))))
        s.sendall(b); t+=n/SR; time.sleep(n/SR*0.9)
threading.Thread(target=prod, daemon=True).start()

frames=[]; lats=[]
async def c():
    async with websockets.connect(f"ws://{HOST}:{VIZ}") as ws:
        end=time.time()+8
        while time.time()<end:
            try: m=await asyncio.wait_for(ws.recv(), timeout=2.0)
            except Exception: continue
            d=json.loads(m)
            if d.get("type")=="config": print("config:",d); continue
            frames.append(time.time()); lats.append((time.time()-d["t"])*1000)
asyncio.run(c())
print(f"FRAMES={len(frames)}")
if len(frames)>=2:
    fps=(len(frames)-1)/(frames[-1]-frames[0])
    print(f"FPS={fps:.1f} LAT min={min(lats):.1f} max={max(lats):.1f} mean={sum(lats)/len(lats):.1f}")
    print("RESULT:", "PASS" if max(lats)<=300 else "OVER")
else:
    print("FAIL no frames")
