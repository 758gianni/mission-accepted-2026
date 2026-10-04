"""Launch the verified local EODMS order/resume runner from an explicit plan.

New downloads default to the user's D drive. This wrapper contains no credentials
and does not replace the already-verified order/download implementation.
"""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--jobs',type=int,choices=range(1,13),default=12)
    p.add_argument('--raw-dir',type=Path,default=Path('/mnt/d/TerraSignal/raw'))
    p.add_argument('--runner',type=Path,default=Path('/tmp/forestwatch-integration-takeover/eodms_orders.py'))
    p.add_argument('--python',type=Path,default=Path('/tmp/forestwatch-integration-takeover/.tools/eodms-cli/.venv/bin/python'))
    args=p.parse_args()
    plan=json.loads(args.plan.read_text())
    records=sorted({d['source_record_id'] for d in plan['decisions'] if d['plan_decision']=='acquire' and d['sensor']=='RADARSAT-2'})
    if not records or any(not r.isdigit() for r in records):
        p.error('Plan must select numeric RS2 record IDs')
    if not args.runner.is_file() or not args.python.is_file():
        p.error('Verified local runner/interpreter unavailable; supply explicit paths')
    args.raw_dir.mkdir(parents=True,exist_ok=True)
    command=[str(args.python),str(args.runner),'--out',str(args.raw_dir),'--jobs',str(args.jobs),'--interval','20','--timeout','14400']
    for rid in records: command+=['--record-id',rid]
    with (args.raw_dir/'acquisition.log').open('ab') as log:
        proc=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    print(json.dumps({'pid':proc.pid,'records':records,'raw_dir':str(args.raw_dir),'estimated_raw_bytes':plan['storage']['estimated_new_raw_bytes']}))


if __name__=='__main__': main()
