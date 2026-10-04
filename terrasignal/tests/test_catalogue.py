import pytest

from terrasignal.catalogue import Catalogue


def test_registration_is_idempotent_and_changed_raw_reference_refused(tmp_path):
    archive=tmp_path/'fixture.zip'
    archive.write_bytes(b'fixture')
    c=Catalogue(tmp_path/'catalogue.sqlite')
    c.register_raw('RADARSAT-2','123',archive,crc_verified=True)
    c.register_raw('RADARSAT-2','123',archive,crc_verified=True)
    assert c.db.execute('SELECT COUNT(*) FROM raw_assets').fetchone()[0]==1
    archive.write_bytes(b'changed bytes')
    with pytest.raises(ValueError):
        c.register_raw('RADARSAT-2','123',archive,crc_verified=True)
    with pytest.raises(ValueError):
        c.register_raw('RADARSAT-2','456',archive,crc_verified=False)
    c.close()
