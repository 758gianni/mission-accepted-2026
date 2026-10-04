"""Array kernels preserving the Myanmar prototype's calibrated-power semantics."""
import numpy as np


def multilook(z, gains, factor=16, xp=np):
    """Complex calibration once, finite/zero/saturation masking, >=90% valid support."""
    valid = xp.isfinite(z) & (xp.abs(z) > 0) & (xp.abs(z.real) < 32767) & (xp.abs(z.imag) < 32767)
    power = (z.real.astype('float32')**2 + z.imag.astype('float32')**2) / gains.astype('float32')**2
    power = xp.where(valid, power, 0)
    h,w = z.shape
    oh,ow = (h+factor-1)//factor, (w+factor-1)//factor
    padding = ((0,oh*factor-h),(0,ow*factor-w))
    power = xp.pad(power,padding)
    support = xp.pad(valid.astype('float32'),padding)
    sums = power.reshape(oh,factor,ow,factor).sum(axis=(1,3),dtype='float64')
    count = support.reshape(oh,factor,ow,factor).sum(axis=(1,3))
    fraction = count/(factor*factor)
    out = xp.where(fraction >= .9, sums/xp.maximum(count,1), xp.nan).astype('float32')
    return out,fraction


def to_db(power, xp=np):
    return xp.where(power>0,10*xp.log10(xp.maximum(power,1e-10)),xp.nan).astype('float32')
