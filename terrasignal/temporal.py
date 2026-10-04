"""Interpretable N-observation behavior; timestamps are retained by tile manifests."""
import warnings

import numpy as np

from .numerics import to_db

CLASS_NAMES={0:'stable within thresholds',1:'persistent radar change',
             2:'temporary radar change (return toward baseline)',
             3:'late radar change (persistence unknown)',4:'progressive radar change',
             5:'inconsistent or threshold-ambiguous radar change',255:'insufficient observations'}


def summarize(power, threshold=3., tolerance=1.5):
    """power[N,Y,X] chronological, unique-date, same-sensor/geometry observations.

    Fixed earliest-observed baseline preserves prototype semantics; robust history
    (excluding latest) is a separate statistic, not silently substituted for it.
    Classes describe backscatter behavior, not causes or learned seasonality.
    """
    if power.ndim!=3 or power.shape[0]<1:
        raise ValueError('Expected chronological power[N,Y,X]')
    db=to_db(power)
    finite=np.isfinite(db)
    count=finite.sum(axis=0).astype('uint16')
    n=power.shape[0]
    first=finite.argmax(axis=0)
    last=n-1-finite[::-1].argmax(axis=0)
    baseline=np.take_along_axis(db,first[None],axis=0)[0]
    latest=np.take_along_axis(db,last[None],axis=0)[0]
    if n<3:
        empty=np.full(count.shape,np.nan,'float32')
        return {'classes':np.full(count.shape,255,'uint8'),'observation_count':count,
                'support_count':np.zeros(count.shape,'uint16'),'baseline_db':baseline,
                'latest_delta_db':empty,'peak_delta_db':empty,'history_median_db':empty,'temporal_variance_db2':empty,
                'robust_latest_score':empty,'first_observation_index':first,
                'latest_observation_index':last,'first_departure_index':first}
    delta=db-baseline
    departure=finite & (np.abs(delta)>=threshold)
    first_departure=departure.argmax(axis=0)
    initial=np.take_along_axis(delta,first_departure[None],axis=0)[0]
    peak=np.take_along_axis(delta,np.abs(np.nan_to_num(delta)).argmax(axis=0)[None],axis=0)[0]
    latest_delta=latest-baseline
    axis=np.arange(n)[:,None,None]
    support=finite & (axis>=first_departure) & (np.sign(delta)==np.sign(initial)) & (np.abs(delta)>=tolerance)
    support_count=support.sum(axis=0).astype('uint16')
    classes=np.full(count.shape,5,'uint8')
    classes[np.all(~finite | (np.abs(delta)<=tolerance),axis=0)]=0
    classes[departure.any(axis=0) & (np.abs(latest_delta)<=tolerance)]=2
    classes[departure.any(axis=0) & (np.abs(latest_delta)>=threshold) & (np.sign(latest_delta)==np.sign(initial)) & (support_count>=2)]=1
    classes[departure.any(axis=0) & (first_departure==last)]=3
    classes[(support_count>=2) & (np.abs(latest_delta)>=np.abs(initial)+threshold) & (np.sign(latest_delta)==np.sign(initial))]=4
    classes[count<3]=255
    history=np.where(finite & (axis<last),db,np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',RuntimeWarning)
        median=np.nanmedian(history,axis=0)
        mad=np.nanmedian(np.abs(history-median),axis=0)
        variance=np.nanvar(db,axis=0)
    return {'classes':classes,'observation_count':count,'support_count':support_count,
            'baseline_db':baseline,'latest_delta_db':latest_delta,'peak_delta_db':peak,
            'history_median_db':median,'temporal_variance_db2':variance,
            'robust_latest_score':(latest-median)/np.maximum(1.4826*mad,tolerance),
            'first_observation_index':first,'latest_observation_index':last,
            'first_departure_index':first_departure}
