import numpy as np
import pytest

from terrasignal.numerics import multilook, to_db


def test_calibration_is_not_squared_twice_and_edges_need_support():
    z = np.full((17,32), 3+4j, dtype='complex64')
    power,fraction = multilook(z,np.full(32,5,dtype='float32'))
    np.testing.assert_allclose(power[0],1)
    assert np.isnan(power[1]).all()  # padded final row is not a full support cell
    assert fraction[1,0] == 1/16
    np.testing.assert_allclose(to_db(power)[0],0)


def test_invalid_and_saturated_samples_are_excluded():
    z = np.full((16,16),3+4j,dtype='complex64')
    z[0,0] = 0
    z[0,1] = 32767+4j
    z[0,2] = np.nan
    p,f = multilook(z,np.full(16,5,dtype='float32'))
    np.testing.assert_allclose(p,1)
    assert f[0,0] == 253/256


def test_cuda_matches_cpu_with_asymmetric_spatial_pattern():
    cp = pytest.importorskip('cupy')
    try:
        cp.cuda.runtime.getDeviceCount()
    except Exception:
        pytest.skip('CUDA driver unavailable')
    z = (np.arange(512*1024).reshape(512,1024)%100 + 3j).astype('complex64')
    gains = np.linspace(1,5,1024,dtype='float32')
    actual = multilook(cp.asarray(z),cp.asarray(gains),xp=cp)
    expected = multilook(z,gains)
    for a,e in zip(actual,expected):
        np.testing.assert_allclose(cp.asnumpy(a),e,rtol=2e-6,atol=1e-6,equal_nan=True)
