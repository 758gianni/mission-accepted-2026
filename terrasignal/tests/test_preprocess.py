from zipfile import ZipFile

import pytest

from terrasignal.preprocess import preprocess


def test_missing_product_xml_has_actionable_record_error(tmp_path):
    archive=tmp_path/'bad.zip'
    with ZipFile(archive,'w') as z: z.writestr('README.txt','not an RS2 product')
    with pytest.raises(ValueError,match='exactly one RADARSAT product.xml'):
        preprocess(archive,tmp_path/'native.tif','123')
    assert archive.exists()
    assert not (tmp_path/'native.tif').exists()
