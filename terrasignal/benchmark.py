"""Profile representative real local raw strips; never copy source scenes."""
import argparse
import importlib.util
import json
import statistics
import time
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import cupy as cp
import numpy as np
import rasterio
from rasterio.windows import Window

from .numerics import multilook, to_db


def median_time(function, repeat):
    for _ in range(3): function()
    times = []
    for _ in range(repeat):
        begin = time.perf_counter()
        function()
        times.append(time.perf_counter()-begin)
    return statistics.median(times)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--repeat',type=int,default=15)
    parser.add_argument('--prototype',type=Path,default=Path('/home/overlord/hackathon/rs2-analysis/scripts/pipeline.py'))
    args=parser.parse_args()
    spec=importlib.util.spec_from_file_location('prototype',args.prototype)
    prototype=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prototype)
    with ZipFile(args.archive) as archive:
        product=next(n for n in archive.namelist() if n.endswith('/product.xml'))
        xml=ET.fromstring(archive.read(product))
        lut=next(e.text for e in xml.findall('.//{*}lookupTable') if e.get('incidenceAngleCorrection')=='Sigma Nought')
        gain_xml=ET.fromstring(archive.read(product.rsplit('/',1)[0]+'/'+lut))
        gains=np.fromstring(gain_xml.find('.//{*}gains').text,sep=' ')
    cases=[]
    with rasterio.open('/vsizip/'+str(args.archive.resolve())+'/'+product) as src:
        for h,w in [(512,512),(1024,1024),(2048,2048),(256,src.width)]:
            window=Window((src.width-w)//2,(src.height-h)//2,w,h)
            t=time.perf_counter()
            raw=src.read(1,window=window)
            read_seconds=time.perf_counter()-t
            gain=gains[int(window.col_off):int(window.col_off)+w].astype('float32')
            expected=prototype.block_power(raw,gain,16)
            cpu=multilook(raw,gain)
            for a,b in zip(cpu,expected):
                np.testing.assert_allclose(a,b,rtol=2e-6,atol=1e-6,equal_nan=True)
            device_raw,device_gain=cp.asarray(raw),cp.asarray(gain)
            actual=multilook(device_raw,device_gain,xp=cp)
            for a,b in zip(actual,expected):
                np.testing.assert_allclose(cp.asnumpy(a),b,rtol=2e-6,atol=1e-6,equal_nan=True)
            def cpu_work():
                p,f=multilook(raw,gain)
                return to_db(p),f
            def gpu_kernel():
                p,f=multilook(device_raw,device_gain,xp=cp)
                db=to_db(p,xp=cp)
                cp.cuda.get_current_stream().synchronize()
                return db,f
            def gpu_transfer():
                p,f=multilook(cp.asarray(raw),cp.asarray(gain),xp=cp)
                return cp.asnumpy(to_db(p,xp=cp)),cp.asnumpy(f)
            cpu_s=median_time(cpu_work,args.repeat)
            gpu_s=median_time(gpu_kernel,args.repeat)
            transfer_s=median_time(gpu_transfer,args.repeat)
            cases.append({'shape':[h,w],'pixels':h*w,'input_bytes':raw.nbytes,'raw_read_seconds':read_seconds,
                'cpu_seconds':cpu_s,'gpu_resident_seconds':gpu_s,'gpu_transfer_seconds':transfer_s,
                'cpu_pixels_per_second':h*w/cpu_s,'gpu_transfer_pixels_per_second':h*w/transfer_s,
                'cpu_tiles_per_second':1/cpu_s,'gpu_transfer_tiles_per_second':1/transfer_s,
                'gpu_transfer_input_GB_per_second':raw.nbytes/transfer_s/1e9,
                'resident_speedup':cpu_s/gpu_s,'transfer_inclusive_speedup':cpu_s/transfer_s,
                'gpu_pool_reserved_bytes':cp.get_default_memory_pool().total_bytes(),
                'parity_with_prototype':True,
                'read_plus_cpu_seconds':read_seconds+cpu_s,'read_plus_gpu_seconds':read_seconds+transfer_s})
            print(json.dumps(cases[-1]),flush=True)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps({'gpu':cp.cuda.runtime.getDeviceProperties(0)['name'].decode(),
        'cupy':cp.__version__,'numpy':np.__version__,'rasterio':rasterio.__version__,
        'archive':args.archive.name,'repeat':args.repeat,'cases':cases,
        'method':'3 warmups, median wall time; stream synchronized; transfer includes H2D + D2H. Read time is one observed OS-cache-dependent read, not a cold-disk benchmark.'},indent=2))


if __name__=='__main__': main()
