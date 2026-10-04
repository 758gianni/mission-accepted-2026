from pathlib import Path

from shapely.geometry import box, mapping

from terrasignal.planner.eodms import search


def test_rapi_pagination_is_one_based(monkeypatch, tmp_path):
    import eodms_rapi
    calls = []
    class Fake:
        err_occurred = False
        def __init__(self, *args): pass
        def set_query_timeout(self, x): pass
        def clear_results(self): pass
        def close_session(self): pass
        def search(self, *args, **kwargs):
            if kwargs.get("hit_count"): return {"hitCount":2}
            calls.append(kwargs["first_result"])
        def get_results(self, **kwargs):
            if calls[-1] > 2: return []
            return [{"recordId": str(calls[-1]), "geometry": mapping(box(93,22,94,23)),
                     "metadata": [["Date", "2024-01-01"], ["Relative Orbit", "256"]]}]
    monkeypatch.setattr(eodms_rapi, "EODMSRAPI", Fake)
    env = tmp_path/"env"
    env.write_text("EODMS_USERNAME=fixture\nEODMS_PASSWORD=fixture\n")
    obs, truncated = search(mapping(box(93,22,94,23)), env, page_size=1, limit=10,
                            start="20240101_000000",end="20241231_235959")
    assert calls == [1,2]
    assert len(obs) == 2 and not truncated
