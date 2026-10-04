"""Reference existing verified prototype assets in the generic source manifest."""
import argparse
import json
from pathlib import Path

from .planner.models import Observation, comparable_group_key


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--derived',type=Path,required=True)
    p.add_argument('--observations',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--raw-dir',type=Path,required=True)
    args=p.parse_args()
    scenes=json.loads((args.derived/'metadata'/'scene_metadata.json').read_text())
    records=[Observation(**o) for o in json.loads(args.observations.read_text())['observations']]
    by_title={o.provenance['title']:o for o in records}
    registration=json.loads((args.derived/'metadata'/'registration.json').read_text())
    sources=[]
    for scene in scenes:
        obs=by_title[Path(scene['archive']).stem]
        label=scene['label']
        shift=registration['scenes'].get(label,{}).get('applied_shift',[0,0])
        asset=args.derived/'aligned'/(label+'_native_power.tif')
        if not asset.is_file(): raise FileNotFoundError(asset)
        sources.append({'source_record_id':obs.source_record_id,'sensor':obs.sensor,
            'comparable_group_key':comparable_group_key(obs),'native_asset':str(asset.resolve()),
            'footprint':scene['gcp_hull_wgs84'],'acquisition_iso':scene['acquisition'],
            'archive_reference':str((args.raw_dir/scene['archive']).resolve()),'raw_bytes':scene['bytes'],
            'shift_pixels':shift,'registration_reference':registration['reference'],
            'registration_quality':registration['scenes'].get(label,{}).get('quality'),
            'raw_crc_verification':'Previously verified prototype journals; not reverified by this packaging operation'})
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(sources,indent=2))
    print(json.dumps({'sources':len(sources),'record_ids':[s['source_record_id'] for s in sources],
                      'distinct_dates':sorted({s['acquisition_iso'][:10] for s in sources}),
                      'raw_bytes':sum(s['raw_bytes'] for s in sources)},indent=2))


if __name__=='__main__': main()
