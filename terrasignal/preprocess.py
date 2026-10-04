"""Central, read-only RS2 raw adapter; CUDA operates on bounded full-width strips."""
import argparse
import hashlib
import json
import math
import os
import time
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import numpy as np
import rasterio
from rasterio.control import GroundControlPoint
from rasterio.windows import Window

from .numerics import multilook


def preprocess(archive_path, out, record_id, *, backend='cpu', factor=16):
    archive_path,out=Path(archive_path),Path(out)
    with ZipFile(archive_path) as archive:
        products=[n for n in archive.namelist() if n.rsplit('/',1)[-1]=='product.xml']
        if len(products)!=1:
            raise ValueError('Expected exactly one RADARSAT product.xml')
        product=products[0]
        product_bytes=archive.read(product)
        xml=ET.fromstring(product_bytes)
        sigma_tables=[e.text for e in xml.findall('.//{*}lookupTable') if e.get('incidenceAngleCorrection')=='Sigma Nought']
        if len(sigma_tables)!=1:
            raise ValueError('Expected exactly one Sigma Nought calibration table')
        lut=sigma_tables[0]
        lut_xml=ET.fromstring(archive.read(product.rsplit('/',1)[0]+'/'+lut))
        if float(lut_xml.find('.//{*}offset').text) != 0:
            raise ValueError('Nonzero calibration offset requires a separate verified adapter')
        gains=np.fromstring(lut_xml.find('.//{*}gains').text,sep=' ').astype('float32')
    xp=np
    if backend=='cuda':
        import cupy as xp
        xp.cuda.get_current_stream().synchronize()
    stats={'source_record_id':record_id,'archive_reference':str(archive_path.resolve()),
        'raw_bytes':archive_path.stat().st_size,'product_xml_sha256':hashlib.sha256(product_bytes).hexdigest(),
        'backend':backend,'factor':factor,'strip_rows':factor*16,
        'raw_read_seconds':0.,'arithmetic_transfer_seconds':0.}
    t=time.perf_counter()
    with rasterio.open('/vsizip/'+str(archive_path.resolve())+'/'+product) as src:
        gcps,gcp_crs=src.gcps
        if len(gains)!=src.width or not np.all(np.isfinite(gains)&(gains>0)) or src.dtypes[0] not in ('complex64','complex_int16'):
            raise ValueError('Unsupported calibration dimensions, gains, or measurement dtype')
        output=np.full((math.ceil(src.height/factor),math.ceil(src.width/factor)),np.nan,'float32')
        device_gain=xp.asarray(gains)
        for y in range(0,src.height,factor*16):
            begin=time.perf_counter()
            raw=src.read(1,window=Window(0,y,src.width,min(factor*16,src.height-y)))
            stats['raw_read_seconds']+=time.perf_counter()-begin
            begin=time.perf_counter()
            block,_=multilook(xp.asarray(raw),device_gain,factor,xp=xp)
            if backend=='cuda': block=xp.asnumpy(block)
            output[y//factor:y//factor+block.shape[0]]=block
            stats['arithmetic_transfer_seconds']+=time.perf_counter()-begin
        stats['raw_pixels']=src.width*src.height
        out.parent.mkdir(parents=True,exist_ok=True)
        tmp=out.with_name(out.name+'.tmp')
        with rasterio.open(tmp,'w',driver='GTiff',height=output.shape[0],width=output.shape[1],count=1,
                           dtype='float32',nodata=np.nan,compress='deflate',tiled=True) as dst:
            dst.gcps=([GroundControlPoint(row=g.row/factor,col=g.col/factor,x=g.x,y=g.y,z=g.z) for g in gcps],gcp_crs)
            dst.write(output,1)
            dst.update_tags(source_record_id=record_id,measurement='sigma0_linear_power',
                            geolocation_status='GCP polynomial, not DEM terrain corrected')
        os.replace(tmp,out)
    stats['end_to_end_seconds']=time.perf_counter()-t
    stats['native_bytes']=out.stat().st_size
    stats['pixels_per_second']=stats['raw_pixels']/stats['end_to_end_seconds']
    stats['raw_GB_per_second']=stats['raw_bytes']/stats['end_to_end_seconds']/1e9
    return stats


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--record-id',required=True)
    parser.add_argument('--backend',choices=('cpu','cuda'),default='cpu')
    args=parser.parse_args()
    stats=preprocess(args.archive,args.out,args.record_id,backend=args.backend)
    args.out.with_suffix('.json').write_text(json.dumps(stats,indent=2))
    print(json.dumps(stats,indent=2))


if __name__=='__main__': main()
