import numpy as np

from terrasignal.temporal import summarize


def test_n_dates_asymmetric_seasonal_band_persistent_block_and_missingness():
    db=np.zeros((5,64,96),dtype='float32')-10
    # A horizontal band, not a square fixture that could hide a transpose.
    db[1:3,12:20,4:84] += 5
    db[1:,40:48,10:22] -= 6.7
    db[-1,52:60,70:80] += 4
    db[:,:,92:] = np.nan
    result=summarize(10**(db/10))
    assert (result['classes'][12:20,4:84] == 2).all()
    assert (result['classes'][40:48,10:22] == 1).all()
    assert (result['classes'][52:60,70:80] == 3).all()
    assert (result['classes'][:,92:] == 255).all()
    rows,cols=np.nonzero(result['classes']==2)
    assert rows.mean()<25 and cols.mean()>25
    assert (result['observation_count'][40:48,10:22] == 5).all()


def test_more_observations_do_not_turn_late_into_validated():
    db=np.array([-10,-10,-10,-10,-5],dtype='float32')[:,None,None]
    result=summarize(10**(db/10))
    assert result['classes'][0,0] == 3
    assert result['support_count'][0,0] == 1


def test_progressive_and_sparse_sampling():
    db=np.array([-10,-6,-2,2],dtype='float32')[:,None,None]
    assert summarize(10**(db/10))['classes'][0,0] == 4
    db=np.array([-10,np.nan,np.nan,-5],dtype='float32')[:,None,None]
    assert summarize(10**(db/10))['classes'][0,0] == 255
