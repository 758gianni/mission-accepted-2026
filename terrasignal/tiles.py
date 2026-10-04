"""Compact ARD and N-date products on deterministic geospatial tiles.

Reads compact native/GCP products, never distributes raw scenes. Use the raw
adapter once per new observation; geocoding and compression remain on CPU.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import time

import numpy as np
from PIL import Image
from pyproj import Transformer
import rasterio
from rasterio.features import geometry_mask, shapes
from rasterio.transform import from_origin
from rasterio.warp import reproject, Resampling, transform_geom
from scipy import ndimage as ndi
from shapely.geometry import box, mapping, shape
from shapely.ops import transform as transform_shape, unary_union

from .numerics import to_db
from .temporal import CLASS_NAMES, summarize


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()


def tile_transform(x,y,size,resolution):
    return from_origin(x*size*resolution,(y+1)*size*resolution,resolution,resolution)


def write_raster(path,data,transform,crs,nodata=np.nan):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    data=data[None] if data.ndim==2 else data
    with rasterio.open(tmp,'w',driver='GTiff',height=data.shape[-2],width=data.shape[-1],count=data.shape[0],
                       dtype=data.dtype,crs=crs,transform=transform,nodata=nodata,compress='deflate',tiled=True) as dst:
        dst.write(data)
    os.replace(tmp,path)


def warp(source_path,transform,crs,size, *, offset=(0.,0.)):
    result=np.full((size,size),np.nan,'float32')
    with rasterio.open(source_path) as src:
        gcps,gcp_crs=src.gcps
        # Shift-to-apply is expressed in destination pixels (row, column).
        shifted=transform @ rasterio.Affine.translation(-offset[1],-offset[0])
        options={'gcps':gcps,'src_crs':gcp_crs,'MAX_GCP_ORDER':2} if gcps else {'src_transform':src.transform,'src_crs':src.crs}
        reproject(src.read(1),result,**options,src_nodata=np.nan,dst_crs=crs,dst_transform=shifted,
                  dst_nodata=np.nan,resampling=Resampling.bilinear,num_threads=2,warp_mem_limit=64)
    return result


def smooth(power):
    valid=np.isfinite(power)
    value=ndi.uniform_filter(np.nan_to_num(power),3,mode='constant')
    weight=ndi.uniform_filter(valid.astype('float32'),3,mode='constant')
    return np.where(weight>.999,value/np.maximum(weight,1e-10),np.nan).astype('float32')


def terrain_tile(dem_paths,transform,crs,size,heading,incidence):
    halo=2
    bigger=transform @ rasterio.Affine.translation(-halo,-halo)
    dem=np.full((size+2*halo,size+2*halo),np.nan,'float32')
    for path in dem_paths:
        layer=warp(path,bigger,crs,size+2*halo)
        dem=np.where(np.isfinite(dem),dem,layer)
    dy,dx=np.gradient(dem,abs(transform.e),transform.a)
    slope=np.degrees(np.arctan(np.hypot(dx,dy)))
    risk=np.where(np.isfinite(dem),(slope>=25).astype('float32'),np.nan)
    if heading is not None and incidence is not None:
        look=np.radians((heading+90)%360)
        range_slope=dx*np.sin(look)-dy*np.cos(look)
        risk=np.where(np.isfinite(dem),((slope>=25)|(range_slope>=np.tan(np.radians(incidence)))|
                     (range_slope<=-1/np.tan(np.radians(incidence)))).astype('float32'),np.nan)
    return np.stack([a[halo:-halo,halo:-halo] for a in (dem,slope,risk)]).astype('float32')


def build(sources,aoi,out, *, crs='EPSG:32646',size=1024,resolution=60,dem_paths=(),heading=None,incidence=None,min_area_ha=5,terrain_cache=None):
    if size not in (512,1024,2048) or resolution<=0:
        raise ValueError('Use benchmarked tile sizes 512/1024/2048 and positive resolution')
    if len({s['comparable_group_key'] for s in sources})!=1:
        raise ValueError('Process sensor/acquisition geometry strata separately')
    if any(s['sensor']!='RADARSAT-2' for s in sources):
        raise ValueError('This adapter generates RS2 primary candidates only')
    out=Path(out)
    out.mkdir(parents=True,exist_ok=True)
    terrain_cache=Path(terrain_cache) if terrain_cache else out/'terrain'
    transform_forward=Transformer.from_crs(4326,crs,always_xy=True).transform
    country=shape(aoi)
    footprint=unary_union([shape(s['footprint']) for s in sources]).intersection(country)
    extent=transform_shape(transform_forward,footprint)
    west,south,east,north=extent.bounds
    step=size*resolution
    tiles=[(x,y) for x in range(math.floor(west/step),math.ceil(east/step))
                  for y in range(math.floor(south/step),math.ceil(north/step))
                  if extent.intersection(box(x*step,y*step,(x+1)*step,(y+1)*step)).area>=resolution**2]
    start=time.perf_counter()
    source_signatures={s['source_record_id']:file_hash(s['native_asset']) for s in sources}
    recipe={'crs':crs,'size':size,'resolution':resolution,'halo':4,'smoothing':3,
            'registration_offsets':{s['source_record_id']:s.get('shift_pixels',[0,0]) for s in sources},
            'kernel_sha256':file_hash(Path(__file__).with_name('numerics.py')),
            'terrain_geometry_mode':'look-dependent approximation' if heading is not None and incidence is not None else 'slope-only; layover/shadow unknown',
            'temporal_sha256':file_hash(Path(__file__).with_name('temporal.py')),
            'tiler_sha256':file_hash(__file__), 'aoi_sha256':hashlib.sha256(json.dumps(aoi,sort_keys=True).encode()).hexdigest(),
            'source_signatures':source_signatures,
            'terrain_inputs':{str(p):[Path(p).stat().st_size,Path(p).stat().st_mtime_ns] for p in dem_paths},
            'heading':heading,'incidence':incidence,'min_area_ha':min_area_ha}
    signature=hashlib.sha256(json.dumps(recipe,sort_keys=True).encode()).hexdigest()
    by_date=defaultdict(list)
    for s in sorted(sources,key=lambda s:s['source_record_id']):
        by_date[s['acquisition_iso'][:10]].append(s)
    dates=sorted(by_date)
    features,manifest,counts=[],[],defaultdict(int)
    reused_assets=0
    geocoding_seconds,temporal_seconds,extraction_seconds=0.,0.,0.
    for x,y in tiles:
        key=f'{crs.replace(":","")}_r{resolution}_n{size}_x{x}_y{y}'
        transform=tile_transform(x,y,size,resolution)
        tile_extent=box(x*step,y*step,(x+1)*step,(y+1)*step)
        images,assets=[],[]
        for day in dates:
            eligible=[s for s in by_date[day] if transform_shape(transform_forward,shape(s['footprint'])).intersects(tile_extent)]
            path=out/'analysis-ready'/key/(day+'.tif')
            stamp=path.with_suffix('.json')
            # New acquisitions invalidate only their intersecting dated tile assets.
            local_recipe={k:v for k,v in recipe.items() if k not in ('source_signatures','registration_offsets','temporal_sha256','min_area_ha')}
            local_recipe['sources']={s['source_record_id']:[source_signatures[s['source_record_id']],s.get('shift_pixels',[0,0])] for s in eligible}
            local_signature=hashlib.sha256(json.dumps(local_recipe,sort_keys=True).encode()).hexdigest()
            if path.exists() and stamp.exists() and json.loads(stamp.read_text()).get('signature')==local_signature:
                with rasterio.open(path) as src: image=src.read(1)
                reused_assets+=1
            else:
                t=time.perf_counter()
                halo=4
                expanded=transform @ rasterio.Affine.translation(-halo,-halo)
                mosaic=np.full((size+2*halo,size+2*halo),np.nan,'float32')
                for s in eligible:
                    layer=warp(s['native_asset'],expanded,crs,size+2*halo,offset=s.get('shift_pixels',(0,0)))
                    mosaic=np.where(np.isfinite(mosaic),mosaic,layer)
                image=smooth(mosaic)[halo:-halo,halo:-halo]
                inside=geometry_mask([mapping(transform_shape(transform_forward,country))],out_shape=(size,size),transform=transform,invert=True)
                image[~inside]=np.nan
                geocoding_seconds+=time.perf_counter()-t
                if np.isfinite(image).any():
                    write_raster(path,image,transform,crs)
                    stamp.write_text(json.dumps({'signature':local_signature,'source_record_ids':[s['source_record_id'] for s in eligible],
                        'acquisition_date':day,'measurement':'sigma0_linear_power','nodata':'NaN, never stable',
                        'geolocation_status':'GCP polynomial, not DEM terrain corrected'},indent=2))
            images.append(image)
            if np.isfinite(image).any():
                assets.append({'date':day,'asset':str(path.relative_to(out)), 'source_record_ids':[s['source_record_id'] for s in eligible]})
        if not assets:
            continue
        stack=np.stack(images)
        t=time.perf_counter()
        summary=summarize(stack)
        temporal_seconds+=time.perf_counter()-t
        # Full history/variance/MAD are regenerable from ARD; persist only compact intelligence rasters.
        write_raster(out/'temporal'/f'{key}.tif',np.stack([summary['classes'].astype('float32'),
            summary['latest_delta_db'],summary['observation_count'].astype('float32')]),transform,crs)
        terrain_path=terrain_cache/f'{key}.tif'
        if dem_paths:
            terrain_stamp=terrain_path.with_suffix('.json')
            terrain_signature=hashlib.sha256(json.dumps({k:recipe[k] for k in ('crs','size','resolution','terrain_inputs','heading','incidence','tiler_sha256')},sort_keys=True).encode()).hexdigest()
            if terrain_path.exists() and terrain_stamp.exists() and json.loads(terrain_stamp.read_text()).get('signature')==terrain_signature:
                with rasterio.open(terrain_path) as src: terrain=src.read()
            else:
                terrain=terrain_tile(dem_paths,transform,crs,size,heading,incidence)
                write_raster(terrain_path,terrain,transform,crs)
                terrain_stamp.write_text(json.dumps({'signature':terrain_signature,'bands':['elevation_m','slope_deg','terrain_risk']},indent=2))
        else: terrain=np.full((3,size,size),np.nan,'float32')
        t=time.perf_counter()
        for class_id in (0,1,2,3,4,5,255):
            counts[CLASS_NAMES[class_id]]+=int((summary['classes']==class_id).sum())
        for class_id,direction,sign in [(c,d,s) for c in (1,2,3,4) for d,s in (('increase',1),('decrease',-1))]:
            components,n=ndi.label((summary['classes']==class_id)&(np.sign(summary['peak_delta_db'])==sign),structure=np.ones((3,3)))
            sizes=np.bincount(components.ravel())
            keep=np.where(sizes*resolution**2/10000>=min_area_ha)[0]
            keep=keep[keep>0]
            for geom,rid in shapes(components.astype('int32'),mask=np.isin(components,keep),transform=transform,connectivity=8):
                pixels=components==int(rid)
                crop_area=shape(geom).intersection(transform_shape(transform_forward,country))
                if crop_area.is_empty: continue
                risk_values=terrain[2][pixels]
                known=np.isfinite(risk_values)
                risk=float(risk_values[known].mean()*100) if known.any() else None
                magnitude=float(np.nanmean(summary['peak_delta_db'][pixels]))
                area=crop_area.area/10000
                first=int(summary['first_departure_index'][pixels].min())
                last=int(summary['latest_observation_index'][pixels].max())
                geographic=shape(transform_geom(crs,'EPSG:4326',mapping(crop_area)))
                candidate_dates={dates[i] for i in range(len(dates)) if np.isfinite(stack[i][pixels]).any()}
                refs=sorted({s['source_record_id'] for s in sources if s['acquisition_iso'][:10] in candidate_dates and shape(s['footprint']).intersects(geographic)})
                props={'id':f'{key}_c{class_id}_{direction}_{int(rid)}','tile_id':key,'primary_source':'RADARSAT-2',
                       'temporal_class':CLASS_NAMES[class_id],'class_id':class_id,'area_ha':area,
                       'first_observed':dates[first],'most_recent_observation':dates[last],
                       'mean_signed_db':magnitude,'anomaly_magnitude_db':abs(magnitude),'direction':direction,
                       'latest_signed_db':float(np.nanmean(summary['latest_delta_db'][pixels])),
                       'acquisition_count':int(np.median(summary['observation_count'][pixels])),
                       'supporting_observation_count':int(np.median(summary['support_count'][pixels])),
                       'validation_count':max(0,int(np.median(summary['support_count'][pixels]))-1),
                       'validation_kind':'within-stratum directional persistence; not independent ground truth',
                       'terrain_risk_pct':risk,'terrain_known_fraction':float(known.mean()),
                       'priority_score':abs(magnitude)*math.sqrt(area)*(1-risk/100) if risk is not None and known.all() else None,
                       'source_record_ids':refs,'tile_edge_fragment':bool(pixels[0].any() or pixels[-1].any() or pixels[:,0].any() or pixels[:,-1].any()),
                       'temporal_trajectory':[{'date':day,'mean_power':float(np.nanmean(stack[i][pixels])) if np.isfinite(stack[i][pixels]).any() else None} for i,day in enumerate(dates)],
                       'quality_flags':sorted({'approximate_GCP_geolocation','registration_inherited_not_remeasured',
                                              *[flag for s in sources if s['source_record_id'] in refs for flag in s.get('quality_flags',[])]}),
                       'interpretation':'Unvalidated radar-change candidate; terrain, registration and moisture may explain the signal.'}
                features.append({'type':'Feature','geometry':transform_geom(crs,'EPSG:4326',mapping(crop_area)),'properties':props})
        extraction_seconds+=time.perf_counter()-t
        manifest.append({'tile_id':key,'crs':crs,'transform':list(transform),'assets':assets,'dates':dates,
            'source_record_ids':sorted({rid for a in assets for rid in a['source_record_ids']}),'temporal_asset':f'temporal/{key}.tif'})
        print(f'{key}: {len(assets)} dated observations, {len(features)} candidate fragments so far',flush=True)
    (out/'candidates').mkdir(exist_ok=True)
    catalogue={'type':'FeatureCollection','features':features}
    (out/'candidates'/'catalogue.geojson').write_text(json.dumps(catalogue,allow_nan=False))
    # Crops only for the ten ranked investigation targets, all dates on identical spatial windows.
    ranked=sorted((f for f in features if f['properties']['priority_score'] is not None),key=lambda f:-f['properties']['priority_score'])[:10]
    for candidate in ranked:
        p=candidate['properties']
        entry=next(m for m in manifest if m['tile_id']==p['tile_id'])
        geo=transform_shape(transform_forward,shape(candidate['geometry']))
        with rasterio.open(out/entry['assets'][0]['asset']) as src:
            window=rasterio.windows.from_bounds(*geo.buffer(600).bounds,transform=src.transform).round_offsets().round_lengths()
        cropdir=out/'candidates'/'crops'/p['id']
        cropdir.mkdir(parents=True,exist_ok=True)
        p['crops']=[]
        for a in entry['assets']:
            with rasterio.open(out/a['asset']) as src:
                data=src.read(1,window=window,boundless=True,fill_value=np.nan)
            db=to_db(data)
            image=np.where(np.isfinite(db),np.clip((np.nan_to_num(db,nan=-20)+20)/20,0,1)*255,0).astype('uint8')
            path=cropdir/(a['date']+'.png')
            Image.fromarray(image).save(path)
            p['crops'].append({'date':a['date'],'asset':str(path.relative_to(out))})
    (out/'candidates'/'catalogue.geojson').write_text(json.dumps(catalogue,allow_nan=False))
    (out/'manifest.json').write_text(json.dumps({'recipe':recipe,'signature':signature,'sources':sources,'tiles':manifest},indent=2))
    metrics={'geographic_tiles':len(manifest),'dated_tile_assets':sum(len(m['assets']) for m in manifest),
        'unique_acquisition_dates':dates,'source_scene_count':len(sources),'candidate_fragments':len(features),
        'class_pixel_counts':dict(counts),'raw_bytes':sum(s['raw_bytes'] for s in sources),
        'reused_ard_assets':reused_assets,'geocoding_seconds':geocoding_seconds,'temporal_seconds':temporal_seconds,
        'extraction_seconds':extraction_seconds,'end_to_end_seconds':time.perf_counter()-start}
    for tier in ('analysis-ready','temporal','candidates','terrain'):
        metrics[tier+'_bytes']=sum(p.stat().st_size for p in (out/tier).rglob('*') if p.is_file())
    metrics['raw_to_ard_ratio']=metrics['raw_bytes']/max(metrics['analysis-ready_bytes'],1)
    metrics['raw_to_candidate_ratio']=metrics['raw_bytes']/max(metrics['candidates_bytes'],1) if features else None
    (out/'metrics.json').write_text(json.dumps(metrics,indent=2))
    return metrics


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',type=Path,required=True)
    p.add_argument('--aoi',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--size',type=int,default=1024)
    p.add_argument('--crs',default='EPSG:32646')
    p.add_argument('--dem-dir',type=Path)
    p.add_argument('--heading',type=float)
    p.add_argument('--incidence',type=float)
    args=p.parse_args()
    sources=json.loads(args.sources.read_text())
    groups=defaultdict(list)
    for s in sources: groups[s['comparable_group_key']].append(s)
    reports=[]
    for group,members in groups.items():
        directory=args.out/hashlib.sha256(group.encode()).hexdigest()[:12]
        report=build(members,json.loads(args.aoi.read_text()),directory,crs=args.crs,size=args.size,
            dem_paths=sorted(args.dem_dir.glob('*.tif')) if args.dem_dir else (),heading=args.heading,incidence=args.incidence,
            terrain_cache=args.out/'terrain-cache')
        report['comparable_group_key']=group
        report['output_directory']=str(directory)
        reports.append(report)
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'summary.json').write_text(json.dumps({'groups':reports,
        'shared_terrain_bytes':sum(p.stat().st_size for p in (args.out/'terrain-cache').rglob('*') if p.is_file()),
        'temporal_bands':['class_id','latest_delta_db','observation_count']},indent=2))
    print(json.dumps(reports,indent=2))


if __name__=='__main__': main()
