import json

import pytest

from terrasignal.storage import read_cache, write_json


def test_failed_json_publication_preserves_previous_metadata(tmp_path):
    target=tmp_path/'manifest.json'
    write_json(target,{'signature':'old'})
    with pytest.raises(ValueError): write_json(target,{'bad':float('nan')})
    assert json.loads(target.read_text())=={'signature':'old'}
    assert not list(tmp_path.glob('*.tmp'))


def test_torn_or_unexpected_stamp_is_a_cache_miss(tmp_path):
    target=tmp_path/'stamp.json'
    target.write_text('{"signature":')
    assert read_cache(target)=={}
    target.write_text('["not metadata"]')
    assert read_cache(target)=={}
