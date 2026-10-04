"""Incrementally preprocess newly CRC-verified central archives and update affected stacks.

No credentials and no ordering. The acquisition runner owns transfer/CRC; this
process consumes its atomic journal and never changes raw files.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import time
import fcntl
import subprocess
import sys
from zipfile import ZipFile

import rasterio
from shapely.geometry import MultiPoint, mapping

from .catalogue import Catalogue
from .planner.models import Observation, comparable_group_key
from .preprocess import preprocess
from .tiles import build, file_hash
from .storage import write_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--prototype-sources',type=Path,required=True)
    p.add_argument('--raw-dir',type=Path,default=Path('/mnt/d/TerraSignal/raw'))
    p.add_argument('--out',type=Path,default=Path('/mnt/d/TerraSignal/products'))
    p.add_argument('--dem-dir',type=Path)
    p.add_argument('--timeout',type=int,default=14400)
    p.add_argument('--background',action='store_true')
    args=p.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    if args.background:
        with (args.out/'ingest-watch.log').open('ab') as log:
            command=[sys.executable,'-m','terrasignal.ingest_watch']+[a for a in sys.argv[1:] if a!='--background']
            process=subprocess.Popen(command,cwd=Path(__file__).resolve().parent.parent,
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        print(json.dumps({'pid':process.pid,'output':str(args.out),'log':str(args.out/'ingest-watch.log')}))
        return
    lock=(args.out/'watch.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    plan=json.loads(args.plan.read_text())
    if any(d['sensor']!='RADARSAT-2' for d in plan['decisions'] if d['plan_decision']=='acquire'):
        raise ValueError('RS2 ingestion adapter cannot acquire supporting-sensor records')
    observations={o['source_record_id']:Observation(**o) for o in plan['observations'] if o['sensor']=='RADARSAT-2'}
    wanted={d['source_record_id'] for d in plan['decisions'] if d['plan_decision']=='acquire'}
    catalogue=Catalogue(args.out/'acquisition-catalogue.sqlite')
    catalogue.register_observations(list(observations.values()))
    sources_path=args.out/'sources.json'
    try:
        sources=json.loads(sources_path.read_text())
    except (OSError,ValueError):
        sources=json.loads(args.prototype_sources.read_text())
    # Missing reproducible native products are not completed ingestion jobs.
    sources=[s for s in sources if s['source_record_id'] not in wanted or Path(s['native_asset']).is_file()]
    complete={s['source_record_id'] for s in sources}
    dirty={s['comparable_group_key'] for s in sources if s['source_record_id'] in wanted}
    deadline=time.monotonic()+args.timeout
    failures={}
    while wanted-complete-failures.keys() or dirty:
        if time.monotonic()>deadline:
            raise TimeoutError('Archive readiness timeout; rerun with the same plan to resume')
        available={}
        for journal in args.raw_dir.glob('orders-*.json'):
            d=json.loads(journal.read_text())
            for rid,assets in d.get('archives',{}).items():
                if rid in wanted:
                    for a in assets:
                        if a.get('crc_verified') and Path(a['path']).is_file() and Path(a['path']).stat().st_size==a['bytes']:
                            available[rid]=Path(a['path'])
        changed=set(dirty)
        for rid in sorted(available.keys()-complete-failures.keys()):
            raw=available[rid]
            obs=observations[rid]
            native=args.out/'staging'/'native'/(rid+'.tif')
            try:
                checksum=file_hash(raw)
                catalogue.register_raw(obs.sensor,rid,raw,crc_verified=True,sha256=checksum)
                stats=preprocess(raw,native,rid,backend='cuda')
                stats['raw_sha256']=checksum
                write_json(native.with_suffix('.json'),stats)
                with ZipFile(raw) as archive:
                    products=[n for n in archive.namelist() if n.rsplit('/',1)[-1]=='product.xml']
                    if len(products)!=1: raise ValueError('Expected exactly one product.xml')
                    product=products[0]
                with rasterio.open('/vsizip/'+str(raw)+'/'+product) as src:
                    gcps,_=src.gcps
                    footprint=mapping(MultiPoint([(g.x,g.y) for g in gcps]).convex_hull)
            except Exception as error:
                failures[rid]={'exception':type(error).__name__,'message':str(error)}
                print(f'{rid}: ingest failed; other verified records will continue ({type(error).__name__})',flush=True)
                continue
            key=comparable_group_key(obs)
            sources.append({'source_record_id':rid,'sensor':obs.sensor,'comparable_group_key':key,
                'native_asset':str(native.resolve()),'footprint':footprint,'acquisition_iso':obs.acquisition_iso,
                'archive_reference':str(raw),'raw_bytes':raw.stat().st_size,'shift_pixels':[0,0],
                'raw_sha256':checksum,
                'registration_quality':None,'raw_crc_verified':True,
                'quality_flags':['registration_not_yet_measured','approximate_GCP_geolocation']})
            complete.add(rid)
            changed.add(key)
            write_json(sources_path,sources)
            print(f'{rid}: CRC-verified archive reduced to native power; registration still requires QA',flush=True)
        if changed:
            groups=defaultdict(list)
            for s in sources: groups[s['comparable_group_key']].append(s)
            for key in changed:
                directory=args.out/'tiles'/hashlib.sha256(key.encode()).hexdigest()[:12]
                metrics=build(groups[key],plan['aoi'],directory,
                    dem_paths=sorted(args.dem_dir.glob('*.tif')) if args.dem_dir else (),
                    terrain_cache=args.out/'terrain-cache')
                print(json.dumps(metrics),flush=True)
            dirty.clear()
        write_json(args.out/'watch-status.json',{'processed_record_ids':sorted(wanted&complete),
            'awaiting_record_ids':sorted(wanted-complete-failures.keys()),'failed_records':failures,
            'raw_dir':str(args.raw_dir)})
        if wanted-complete-failures.keys(): time.sleep(30)
    catalogue.close()
    if failures:
        raise RuntimeError(f'{len(failures)} records failed; see watch-status.json and rerun to retry')
    print('Selected acquisition batch fully ingested. Outputs remain exploratory until registration QA.',flush=True)


if __name__=='__main__': main()
