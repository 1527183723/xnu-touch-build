#!/usr/bin/env python3
"""Test a new SB library in the running RAM session, preserving the original root."""
import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap=argparse.ArgumentParser(__doc__)
    ap.add_argument('--build', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--rollback', action='store_true')
    args=ap.parse_args()
    out=args.output; out.mkdir(parents=True,exist_ok=False)
    counter=0

    def fb(*words):
        p=subprocess.run([str(ROOT.parent/'tools/platform-tools/fastboot.exe'),'-s','xnuloader',*words],
                         stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30)
        text=p.stdout.decode('utf-8','replace').replace('\r','')
        with (out/'commands.txt').open('a',encoding='utf-8') as f:
            f.write(' '.join(words)+'\n'+text+'\n')
        if p.returncode or 'FAILED' in text: raise RuntimeError(text)
        return text

    def sh(command):
        nonlocal counter
        seq=int(re.search(r'\(bootloader\)\s*(\d+)',fb('oem','sh',command)).group(1))
        for _ in range(30):
            time.sleep(.5); counter+=1
            fb('oem','log'); path=out/f'log-{counter:03d}.txt'; fb('get_staged',str(path))
            text=path.read_text(encoding='utf-8',errors='replace')
            done=re.search(rf'^rwsh {seq} exit (\d+) ',text,re.M)
            if done:
                print('\n'.join(s for s in text.splitlines() if f'rwsh {seq}' in s),flush=True)
                if done[1]!='0': raise RuntimeError(command)
                return
        raise TimeoutError(command)

    if 'running' not in fb('getvar','result'): raise RuntimeError('Running iOS required')
    if not args.rollback:
        for source,dest in [('libredwood-sb26.dylib','/private/var/tmp/libsb26-icons.dylib'),
                            ('session-icon-library','/private/var/tmp/icon-library')]:
            fb('oem','push'); fb('stage',str(args.build/source)); sh('pull '+dest)
    sh('/private/var/tmp/icon-library' + ('' if args.rollback else ' apply'))
    sh('restart SpringBoard')
    for step in range(4):
        time.sleep(10)
        fb('oem','screenshot'); frame=out/f'screen-{step:02d}.raw'; fb('get_staged',str(frame))
        if frame.stat().st_size!=1200*2670*4: raise RuntimeError('Unexpected geometry')
        im=Image.frombytes('RGBA',(1200,2670),frame.read_bytes(),'raw','BGRA')
        im.save(out/f'screen-{step:02d}.png'); im.resize((400,890)).save(out/f'screen-{step:02d}-small.png')
    sh('notify com.redwood.icons')
    time.sleep(2)
    fb('oem','screenshot'); frame=out/'screen.raw'; fb('get_staged',str(frame))
    im=Image.frombytes('RGBA',(1200,2670),frame.read_bytes(),'raw','BGRA')
    im.save(out/'screen.png'); im.resize((400,890)).save(out/'screen-small.png')
    (out/'session-result.json').write_text(json.dumps({'rollback':args.rollback,
        'SpringBoard_restarted':True,'kernel_restarted':False,'root_disk_changed':False,
        'GPU_binaries_changed':False,'partition_flash_commands':False},indent=2)+'\n')


if __name__=='__main__': main()
