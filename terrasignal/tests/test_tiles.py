import json

import numpy as np
import rasterio
from rasterio.warp import transform_geom
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

from terrasignal.tiles import build, tile_transform, write_raster


def test_spatial_ids_dated_manifest_and_idempotent_ard(tmp_path):
    transform=from_origin(10*512*60,41*512*60,60,60)
    geom=transform_geom('EPSG:32646','EPSG:4326',mapping(box(10*512*60,41*512*60-16*60,10*512*60+32*60,41*512*60)))
    sources=[]
    for i,day in enumerate(('2024-01-01','2024-06-01','2025-01-01')):
        path=tmp_path/f'source{i}.tif'
        power=np.full((16,32),.1 if i==0 else .025,'float32')
        write_raster(path,power,transform,'EPSG:32646')
        sources.append({'source_record_id':str(i),'sensor':'RADARSAT-2','comparable_group_key':'fixture',
                        'footprint':geom,'native_asset':str(path),'acquisition_iso':day,'raw_bytes':1_000_000})
    first=build(sources,geom,tmp_path/'products',size=512)
    second=build(sources,geom,tmp_path/'products',size=512)
    assert second['reused_ard_assets']==first['dated_tile_assets']
    manifest=json.loads((tmp_path/'products'/'manifest.json').read_text())
    assert manifest['tiles'][0]['dates']==['2024-01-01','2024-06-01','2025-01-01']
    assert set(manifest['tiles'][0]['source_record_ids'])=={'0','1','2'}
    assert manifest['recipe']['resolution']==60
    assert tile_transform(10,40,512,60)==transform
    # Add a fourth observation to the same grid: prior dated ARD remains reusable.
    path=tmp_path/'source_new.tif'
    write_raster(path,np.full((16,32),.025,'float32'),transform,'EPSG:32646')
    extra=dict(sources[0],source_record_id='new',native_asset=str(path),acquisition_iso='2025-02-01')
    incremental=build(sources+[extra],geom,tmp_path/'products',size=512)
    assert incremental['reused_ard_assets']==first['dated_tile_assets']
    assert incremental['dated_tile_assets']==first['dated_tile_assets']+1
    # A torn metadata stamp is recoverable rather than permanently wedging resume.
    stamp=next((tmp_path/'products'/'analysis-ready').rglob('*.json'))
    stamp.write_text('{"signature":')
    recovered=build(sources+[extra],geom,tmp_path/'products',size=512)
    assert recovered['reused_ard_assets']==incremental['dated_tile_assets']-1


def test_mosaic_credits_contributors_not_shadowed_inputs_and_uses_utc_dates(tmp_path):
    transform=from_origin(10*512*60,41*512*60,60,60)
    geom=transform_geom('EPSG:32646','EPSG:4326',mapping(box(10*512*60,41*512*60-16*60,10*512*60+32*60,41*512*60)))
    sources=[]
    for rid,day,power in [('0','2024-01-01',.001),('1','2024-01-01',.1),('2','2024-12-01T23:30-03:30',.025)]:
        path=tmp_path/(rid+'.tif')
        write_raster(path,np.full((16,32),power,'float32'),transform,'EPSG:32646')
        sources.append({'source_record_id':rid,'sensor':'RADARSAT-2','comparable_group_key':'fixture',
                        'footprint':geom,'native_asset':str(path),'acquisition_iso':day,'raw_bytes':1000})
    build(sources,geom,tmp_path/'products',size=512)
    manifest=json.loads((tmp_path/'products'/'manifest.json').read_text())
    tile=manifest['tiles'][0]
    assert tile['dates']==['2024-01-01','2024-12-02']
    first=tile['assets'][0]
    assert first['source_record_ids']==['0']
    assert first['input_source_record_ids']==['0','1']
    assert first['overlap_diagnostics'][0]['mean_absolute_difference_db']>10
    assert (tmp_path/'products'/first['provenance_asset']).is_file()


def test_partial_terrain_is_unknown_but_kept_in_investigation_ranking(tmp_path,monkeypatch):
    transform=from_origin(10*512*60,41*512*60,60,60)
    geom=transform_geom('EPSG:32646','EPSG:4326',mapping(box(10*512*60,41*512*60-16*60,10*512*60+32*60,41*512*60)))
    sources=[]
    for i,day in enumerate(('2024-01-01','2024-06-01','2025-01-01')):
        path=tmp_path/f's{i}.tif'
        write_raster(path,np.full((16,32),.1 if i==0 else .025,'float32'),transform,'EPSG:32646')
        sources.append({'source_record_id':str(i),'sensor':'RADARSAT-2','comparable_group_key':'fixture',
            'footprint':geom,'native_asset':str(path),'acquisition_iso':day,'raw_bytes':1000})
    terrain=np.full((3,512,512),np.nan,'float32')
    terrain[:,0:8,:]=0
    monkeypatch.setattr('terrasignal.tiles.terrain_tile',lambda *args:terrain)
    build(sources,geom,tmp_path/'products',size=512,dem_paths=[tmp_path/'s0.tif'])
    feature=json.loads((tmp_path/'products'/'candidates'/'catalogue.geojson').read_text())['features'][0]
    props=feature['properties']
    assert props['terrain_risk_pct'] is None
    assert 0<props['terrain_known_fraction']<1
    assert props['terrain_known_subset_risk_pct']==0
    assert props['priority_score']>0
    assert props['crops']
