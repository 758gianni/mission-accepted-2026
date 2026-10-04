import json

import numpy as np
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
