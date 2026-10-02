"""Tests for credential-free scene selection from a public STAC catalog.

Two kinds of fixture are used, and they are kept strictly apart:

* ``PUBLIC_CATALOG_JSON`` is the six published research items from the public
  EODMS ``Radarsat-2_Tropical_Forest_Products`` STAC collection, copied verbatim
  (geometry, properties, assets and public links) from
  ``research/catalog-scene-selection/selected-scenes.geojson`` at commit
  dd44fa12d15893c5474f382f0e1fa9839f231308. These are public metadata records.
  Their ``assets.product.href`` values are bearer-guarded catalogue endpoints,
  not signed download URLs, and no credential is present anywhere in the file.
* Everything else is generated here, marked with a ``SYNTHETIC-TEST-FIXTURE``
  marker and a ``synthetic-`` id prefix, and is used only inside these tests.

Run from the repository root with:

    python -m pytest tests/catalog/test_select.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from catalog.select import (  # noqa: E402
    Criteria,
    SelectionError,
    load_catalog,
    main,
    select_scenes,
)

PUBLIC_CATALOG_SHA256 = "4319cf627c9c211e2e48423b7862dc4be880ccdbf4b0bc377f4e1d37116d62a7"

PUBLIC_CATALOG_JSON = r"""
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "stac_version": "1.0.0",
      "collection": "Radarsat-2_Tropical_Forest_Products",
      "id": "4ff7a4b5-57b0-5cde-bd8b-300a6903c0e7",
      "bbox": [
        118.1289093927102,
        4.867079872602928,
        119.4902880921239,
        6.088719732534585
      ],
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [
              118.128909393,
              5.842641236
            ],
            [
              118.336717795,
              4.867079873
            ],
            [
              119.490288092,
              5.114950366
            ],
            [
              119.284519509,
              6.088719733
            ],
            [
              118.128909393,
              5.842641236
            ]
          ]
        ]
      },
      "properties": {
        "sar:frequency_band": "C",
        "sar:polarizations": "VV VH",
        "datetime": "2009-08-12T10:24:43+00:00",
        "beam_mode": "Wide3",
        "megabytes": 355,
        "order_key": "RS2_OK133876_PK1168492_DK1128203_W3_20090812_102443_VV_VH_SGF",
        "sar:beam_ids": [
          "W3"
        ],
        "applied_lut": "Constant-beta",
        "end_datetime": "2009-08-12T10:24:44+00:00",
        "polarization": "VV VH",
        "product_type": "SGF",
        "beam_mnemonic": "W3",
        "absolute_orbit": 8673,
        "product_format": "GeoTIFF",
        "sar:center_frequency": 5.405,
        "sat:orbit_state": "ascending",
        "sat:relative_orbit": 98,
        "sampled_line_spacing": 12.5,
        "sampled_pixel_spacing": 12.5,
        "proj:code": "EPSG:4326"
      },
      "assets": {
        "product": {
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/dds/v1/item/EODMS/Radarsat-2_Tropical_Forest_Products/4ff7a4b5-57b0-5cde-bd8b-300a6903c0e7",
          "title": "Radarsat-2 Product",
          "description": "Radarsat-2 Product",
          "type": "application/zip",
          "roles": [
            "data",
            "metadata",
            "archive"
          ],
          "auth:refs": [
            "bearer"
          ]
        },
        "thumbnail": {
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/image/v1/browse/4ff7a4b5-57b0-5cde-bd8b-300a6903c0e7",
          "title": "Radarsat-2 Thumbnail",
          "description": "Radarsat-2 Thumbnail",
          "type": "image/png",
          "roles": [
            "thumbnail"
          ]
        }
      },
      "links": [
        {
          "rel": "self",
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products/items/4ff7a4b5-57b0-5cde-bd8b-300a6903c0e7"
        },
        {
          "rel": "collection",
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products"
        }
      ]
    },
    {
      "type": "Feature",
      "stac_version": "1.0.0",
      "collection": "Radarsat-2_Tropical_Forest_Products",
      "id": "b8a8461b-0f00-5996-922e-76721012d178",
      "bbox": [
        118.120849397435,
        4.813995659935657,
        119.505781223016,
        6.14651332012973
      ],
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [
              118.120849397,
              5.900559027
            ],
            [
              118.352309162,
              4.81399566
            ],
            [
              119.505781223,
              5.061944619
            ],
            [
              119.276599346,
              6.14651332
            ],
            [
              118.120849397,
              5.900559027
            ]
          ]
        ]
      },
      "properties": {
        "sar:frequency_band": "C",
        "sar:polarizations": "VV VH",
        "datetime": "2009-11-16T10:24:48+00:00",
        "beam_mode": "Wide3",
        "megabytes": 395,
        "order_key": "RS2_OK133876_PK1168508_DK1128219_W3_20091116_102448_VV_VH_SGF",
        "sar:beam_ids": [
          "W3"
        ],
        "applied_lut": "Constant-beta",
        "end_datetime": "2009-11-16T10:24:48+00:00",
        "polarization": "VV VH",
        "product_type": "SGF",
        "beam_mnemonic": "W3",
        "absolute_orbit": 10045,
        "product_format": "GeoTIFF",
        "sar:center_frequency": 5.405,
        "sat:orbit_state": "ascending",
        "sat:relative_orbit": 98,
        "sampled_line_spacing": 12.5,
        "sampled_pixel_spacing": 12.5,
        "proj:code": "EPSG:4326"
      },
      "assets": {
        "thumbnail": {
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/image/v1/browse/b8a8461b-0f00-5996-922e-76721012d178",
          "title": "Radarsat-2 Thumbnail",
          "description": "Radarsat-2 Thumbnail",
          "type": "image/png",
          "roles": [
            "thumbnail"
          ]
        },
        "product": {
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/dds/v1/item/EODMS/Radarsat-2_Tropical_Forest_Products/b8a8461b-0f00-5996-922e-76721012d178",
          "title": "Radarsat-2 Product",
          "description": "Radarsat-2 Product",
          "type": "application/zip",
          "roles": [
            "data",
            "metadata",
            "archive"
          ],
          "auth:refs": [
            "bearer"
          ]
        }
      },
      "links": [
        {
          "rel": "self",
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products/items/b8a8461b-0f00-5996-922e-76721012d178"
        },
        {
          "rel": "collection",
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products"
        }
      ]
    },
    {
      "type": "Feature",
      "stac_version": "1.0.0",
      "collection": "Radarsat-2_Tropical_Forest_Products",
      "id": "8178671e-0090-5be2-ad28-a176e4358b34",
      "bbox": [
        118.1201137129504,
        4.814707666206433,
        119.5047180222664,
        6.14559986333675
      ],
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [
              118.120113713,
              5.899603978
            ],
            [
              118.351252671,
              4.814707666
            ],
            [
              119.504718022,
              5.062695306
            ],
            [
              119.275852552,
              6.145599863
            ],
            [
              118.120113713,
              5.899603978
            ]
          ]
        ]
      },
      "properties": {
        "sar:frequency_band": "C",
        "sar:polarizations": "VV VH",
        "datetime": "2010-02-20T10:24:47+00:00",
        "beam_mode": "Wide3",
        "megabytes": 395,
        "order_key": "RS2_OK133876_PK1168526_DK1128237_W3_20100220_102447_VV_VH_SGF",
        "sar:beam_ids": [
          "W3"
        ],
        "applied_lut": "Constant-beta",
        "end_datetime": "2010-02-20T10:24:48+00:00",
        "polarization": "VV VH",
        "product_type": "SGF",
        "beam_mnemonic": "W3",
        "absolute_orbit": 11417,
        "product_format": "GeoTIFF",
        "sar:center_frequency": 5.405,
        "sat:orbit_state": "ascending",
        "sat:relative_orbit": 98,
        "sampled_line_spacing": 12.5,
        "sampled_pixel_spacing": 12.5,
        "proj:code": "EPSG:4326"
      },
      "assets": {
        "thumbnail": {
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/image/v1/browse/8178671e-0090-5be2-ad28-a176e4358b34",
          "title": "Radarsat-2 Thumbnail",
          "description": "Radarsat-2 Thumbnail",
          "type": "image/png",
          "roles": [
            "thumbnail"
          ]
        },
        "product": {
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/dds/v1/item/EODMS/Radarsat-2_Tropical_Forest_Products/8178671e-0090-5be2-ad28-a176e4358b34",
          "title": "Radarsat-2 Product",
          "description": "Radarsat-2 Product",
          "type": "application/zip",
          "roles": [
            "data",
            "metadata",
            "archive"
          ],
          "auth:refs": [
            "bearer"
          ]
        }
      },
      "links": [
        {
          "rel": "self",
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products/items/8178671e-0090-5be2-ad28-a176e4358b34"
        },
        {
          "rel": "collection",
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products"
        }
      ]
    },
    {
      "type": "Feature",
      "stac_version": "1.0.0",
      "collection": "Radarsat-2_Tropical_Forest_Products",
      "id": "cf749344-5897-4ae9-9f12-eb59c8cafdad",
      "bbox": [
        -57.72529132955975,
        -2.60642937937276,
        -56.37809766668281,
        -1.175148968081942
      ],
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [
              -57.72529133,
              -1.419677449
            ],
            [
              -57.463328992,
              -2.606429379
            ],
            [
              -56.378097667,
              -2.359977247
            ],
            [
              -56.641078873,
              -1.175148968
            ],
            [
              -57.72529133,
              -1.419677449
            ]
          ]
        ]
      },
      "properties": {
        "sar:frequency_band": "C",
        "sar:polarizations": "HH",
        "datetime": "2014-05-06T22:08:17+00:00",
        "beam_mode": "Extra Fine0",
        "megabytes": 5497,
        "order_key": "RS2_OK151247_PK1385617_DK1350411_XF0W2_20140506_220817_HH_SLC",
        "sar:beam_ids": [
          "XF0W2"
        ],
        "applied_lut": "Land",
        "end_datetime": "2014-05-06T22:08:18+00:00",
        "polarization": "HH",
        "product_type": "SLC",
        "beam_mnemonic": "XF0W2",
        "absolute_orbit": 33375,
        "product_format": "GeoTIFF",
        "sar:center_frequency": 5.405,
        "sat:orbit_state": "ascending",
        "sat:relative_orbit": 104,
        "sampled_line_spacing": 2.49843,
        "sampled_pixel_spacing": 2.662357,
        "proj:code": "EPSG:4326"
      },
      "assets": {
        "product": {
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/dds/v1/item/EODMS/Radarsat-2_Tropical_Forest_Products/cf749344-5897-4ae9-9f12-eb59c8cafdad",
          "title": "Radarsat-2 Product",
          "description": "Radarsat-2 Product",
          "type": "application/zip",
          "roles": [
            "data",
            "metadata",
            "archive"
          ],
          "auth:refs": [
            "bearer"
          ]
        },
        "thumbnail": {
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/image/v1/browse/cf749344-5897-4ae9-9f12-eb59c8cafdad",
          "title": "Radarsat-2 Thumbnail",
          "description": "Radarsat-2 Thumbnail",
          "type": "image/png",
          "roles": [
            "thumbnail"
          ]
        }
      },
      "links": [
        {
          "rel": "self",
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products/items/cf749344-5897-4ae9-9f12-eb59c8cafdad"
        },
        {
          "rel": "collection",
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products"
        }
      ]
    },
    {
      "type": "Feature",
      "stac_version": "1.0.0",
      "collection": "Radarsat-2_Tropical_Forest_Products",
      "id": "87d1506d-8fd8-44c5-849f-61631e968c1e",
      "bbox": [
        -57.72605112299947,
        -2.606651442379825,
        -56.3789573133793,
        -1.175511486734327
      ],
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [
              -57.726051123,
              -1.420024401
            ],
            [
              -57.464111552,
              -2.606651442
            ],
            [
              -56.378957313,
              -2.360215245
            ],
            [
              -56.64191497,
              -1.175511487
            ],
            [
              -57.726051123,
              -1.420024401
            ]
          ]
        ]
      },
      "properties": {
        "sar:frequency_band": "C",
        "sar:polarizations": "HH",
        "datetime": "2015-04-07T22:08:08+00:00",
        "beam_mode": "Extra Fine0",
        "megabytes": 5497,
        "order_key": "RS2_OK151584_PK1387509_DK1352132_XF0W2_20150407_220808_HH_SLC",
        "sar:beam_ids": [
          "XF0W2"
        ],
        "applied_lut": "Land",
        "end_datetime": "2015-04-07T22:08:09+00:00",
        "polarization": "HH",
        "product_type": "SLC",
        "beam_mnemonic": "XF0W2",
        "absolute_orbit": 38177,
        "product_format": "GeoTIFF",
        "sar:center_frequency": 5.405,
        "sat:orbit_state": "ascending",
        "sat:relative_orbit": 104,
        "sampled_line_spacing": 2.498448,
        "sampled_pixel_spacing": 2.662357,
        "proj:code": "EPSG:4326"
      },
      "assets": {
        "thumbnail": {
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/image/v1/browse/87d1506d-8fd8-44c5-849f-61631e968c1e",
          "title": "Radarsat-2 Thumbnail",
          "description": "Radarsat-2 Thumbnail",
          "type": "image/png",
          "roles": [
            "thumbnail"
          ]
        },
        "product": {
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/dds/v1/item/EODMS/Radarsat-2_Tropical_Forest_Products/87d1506d-8fd8-44c5-849f-61631e968c1e",
          "title": "Radarsat-2 Product",
          "description": "Radarsat-2 Product",
          "type": "application/zip",
          "roles": [
            "data",
            "metadata",
            "archive"
          ],
          "auth:refs": [
            "bearer"
          ]
        }
      },
      "links": [
        {
          "rel": "self",
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products/items/87d1506d-8fd8-44c5-849f-61631e968c1e"
        },
        {
          "rel": "collection",
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products"
        }
      ]
    },
    {
      "type": "Feature",
      "stac_version": "1.0.0",
      "collection": "Radarsat-2_Tropical_Forest_Products",
      "id": "742988bf-062b-4fe7-995d-8620f0ade85a",
      "bbox": [
        -57.72648634831063,
        -2.60619329462889,
        -56.37935444302795,
        -1.174937874671778
      ],
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [
              -57.726486348,
              -1.419459989
            ],
            [
              -57.464502095,
              -2.606193295
            ],
            [
              -56.379354443,
              -2.359748228
            ],
            [
              -56.642354461,
              -1.174937875
            ],
            [
              -57.726486348,
              -1.419459989
            ]
          ]
        ]
      },
      "properties": {
        "sar:frequency_band": "C",
        "sar:polarizations": "HH",
        "datetime": "2016-04-01T22:07:53+00:00",
        "beam_mode": "Extra Fine0",
        "megabytes": 5497,
        "order_key": "RS2_OK151671_PK1389651_DK1354430_XF0W2_20160401_220753_HH_SLC",
        "sar:beam_ids": [
          "XF0W2"
        ],
        "applied_lut": "Land",
        "end_datetime": "2016-04-01T22:07:54+00:00",
        "polarization": "HH",
        "product_type": "SLC",
        "beam_mnemonic": "XF0W2",
        "absolute_orbit": 43322,
        "product_format": "GeoTIFF",
        "sar:center_frequency": 5.405,
        "sat:orbit_state": "ascending",
        "sat:relative_orbit": 104,
        "sampled_line_spacing": 2.498449,
        "sampled_pixel_spacing": 2.662357,
        "proj:code": "EPSG:4326"
      },
      "assets": {
        "thumbnail": {
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/image/v1/browse/742988bf-062b-4fe7-995d-8620f0ade85a",
          "title": "Radarsat-2 Thumbnail",
          "description": "Radarsat-2 Thumbnail",
          "type": "image/png",
          "roles": [
            "thumbnail"
          ]
        },
        "product": {
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/dds/v1/item/EODMS/Radarsat-2_Tropical_Forest_Products/742988bf-062b-4fe7-995d-8620f0ade85a",
          "title": "Radarsat-2 Product",
          "description": "Radarsat-2 Product",
          "type": "application/zip",
          "roles": [
            "data",
            "metadata",
            "archive"
          ],
          "auth:refs": [
            "bearer"
          ]
        }
      },
      "links": [
        {
          "rel": "self",
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products/items/742988bf-062b-4fe7-995d-8620f0ade85a"
        },
        {
          "rel": "collection",
          "href": "https://eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products"
        }
      ]
    }
  ]
}
"""

SYNTHETIC_MARKER = "SYNTHETIC-TEST-FIXTURE"
_DEFAULT_GEOMETRY = object()

# Published figures recomputed by research/catalog-scene-selection/verify_artifacts.py
# at dd44fa1, used here as an independent cross-check of the measured geometry.
PUBLISHED_PRIMARY_DATES = ["2009-08-12", "2009-11-16", "2010-02-20"]
PUBLISHED_PRIMARY_GAPS_CALENDAR_DAYS = [96, 96]
PUBLISHED_PRIMARY_MIN_PAIRWISE_OVERLAP_HA = 1442704.4
PUBLISHED_PRIMARY_MIN_PAIRWISE_OVERLAP_PERCENT = 99.64
PUBLISHED_PRIMARY_SCENE_MB = [355, 395, 395]
PUBLISHED_COMPARISON_DATES = ["2014-05-06", "2015-04-07", "2016-04-01"]
PUBLISHED_COMPARISON_GAPS_CALENDAR_DAYS = [336, 360]
PUBLISHED_COMPARISON_SCENE_MB = [5497, 5497, 5497]


def public_catalog() -> dict:
    return json.loads(PUBLIC_CATALOG_JSON)


def write_catalog(tmp_path, document: dict) -> str:
    path = os.path.join(str(tmp_path), "catalog.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle)
    return path


def public_features_by_date() -> dict:
    mapping = {}
    for feature in public_catalog()["features"]:
        mapping[feature["properties"]["datetime"][:10]] = feature
    return mapping


# --------------------------------------------------------------------------- #
# synthetic fixture generation (tests only, never published as real data)
# --------------------------------------------------------------------------- #
def synthetic_item(
    item_id: str,
    date_text: str,
    *,
    lon: float = 118.0,
    lat: float = 5.0,
    size_deg: float = 0.5,
    product_type: str = "SGF",
    megabytes: float = 100.0,
    polarization: str = "VV VH",
    beam: str = "W3",
    orbit_state: str = "ascending",
    relative_orbit: int = 98,
    applied_lut: str = "Constant-beta",
    geometry=_DEFAULT_GEOMETRY,
    extra_properties: dict | None = None,
    assets: dict | None = None,
    links: list | None = None,
    time_text: str = "00:00:00",
) -> dict:
    """Build a clearly-labelled synthetic square-footprint STAC item."""
    half = size_deg / 2.0
    properties = {
        SYNTHETIC_MARKER: True,
        "datetime": f"{date_text}T{time_text}+00:00",
        "product_type": product_type,
        "megabytes": megabytes,
        "polarization": polarization,
        "beam_mnemonic": beam,
        "sat:orbit_state": orbit_state,
        "sat:relative_orbit": relative_orbit,
        "applied_lut": applied_lut,
        "proj:code": "EPSG:4326",
        "order_key": f"SYNTHETIC_{item_id}_{date_text}",
    }
    for key in (
        "product_type",
        "megabytes",
        "polarization",
        "beam_mnemonic",
        "sat:orbit_state",
        "sat:relative_orbit",
        "applied_lut",
        "proj:code",
    ):
        if properties[key] is None:
            del properties[key]
    if extra_properties:
        properties.update(extra_properties)
    if geometry is _DEFAULT_GEOMETRY:
        geometry = {
            "type": "Polygon",
            "coordinates": [
                [
                    [lon - half, lat - half],
                    [lon + half, lat - half],
                    [lon + half, lat + half],
                    [lon - half, lat + half],
                    [lon - half, lat - half],
                ]
            ],
        }
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "collection": "synthetic-test-collection",
        "id": f"synthetic-{item_id}",
        "geometry": geometry,
        "properties": properties,
        "assets": {"thumbnail": {"href": f"https://example.invalid/thumb/{item_id}.png", "type": "image/png"}} if assets is None else assets,
        "links": links if links is not None else [{"rel": "self", "href": f"https://example.invalid/items/{item_id}"}],
    }


def synthetic_collection(items) -> dict:
    return {"type": "FeatureCollection", "features": list(items)}


def criteria(**overrides) -> Criteria:
    values = {
        "count": 2,
        "max_scene_mb": 700.0,
        "min_gap_days": 30,
        "min_overlap_percent": 50.0,
    }
    values.update(overrides)
    return Criteria(**values)


def select(document: dict, **overrides) -> dict:
    return select_scenes(copy.deepcopy(document), criteria(**overrides))


# --------------------------------------------------------------------------- #
# the published public records
# --------------------------------------------------------------------------- #
def test_public_fixture_is_the_six_published_items_unchanged():
    document = public_catalog()
    assert hashlib.sha256(PUBLIC_CATALOG_JSON.strip().encode()).hexdigest() == PUBLIC_CATALOG_SHA256
    assert len(document["features"]) == 6
    types = sorted(feature["properties"]["product_type"] for feature in document["features"])
    assert types == ["SGF", "SGF", "SGF", "SLC", "SLC", "SLC"]
    assert document["features"][0]["properties"]["applied_lut"] == "Constant-beta"
    assert document["features"][3]["properties"]["applied_lut"] == "Land"


def test_public_six_items_with_700mb_cap_selects_the_sabah3_sgf_trio():
    report = select(public_catalog(), count=3, max_scene_mb=700, min_gap_days=90, min_overlap_percent=99.0)
    selection = report["selection"]

    assert selection["acquisition_dates"] == PUBLISHED_PRIMARY_DATES
    assert selection["scene_megabytes"] == [float(value) for value in PUBLISHED_PRIMARY_SCENE_MB]
    assert selection["gaps_calendar_days"] == PUBLISHED_PRIMARY_GAPS_CALENDAR_DAYS
    assert selection["total_span_calendar_days"] == 192
    assert selection["group_key"] == {
        "polarization": "VV+VH",
        "beam": "W3",
        "orbit": {"relative_orbit": 98, "orbit_state": "ascending"},
        "applied_lut": "Constant-beta",
    }

    size_rejections = [r for r in report["rejected_items"] if "max-scene-mb" in r["reason"]]
    assert len(size_rejections) == 3
    assert all("5497" in r["reason"] for r in size_rejections)
    assert all(r["reason"].endswith("> --max-scene-mb 700") for r in size_rejections)


def test_measured_overlap_matches_the_published_figures():
    report = select(public_catalog(), count=3, max_scene_mb=700, min_gap_days=90, min_overlap_percent=99.0)
    selection = report["selection"]

    pairwise = selection["pairwise_overlap"]
    assert len(pairwise) == 3
    by_date = {feature["properties"]["datetime"][:10]: feature["id"] for feature in public_catalog()["features"]}
    for pair in pairwise:
        assert pair["intersection_ha_equal_area"] == pytest.approx(
            pair["intersection_ha_geodesic"], rel=0.005
        )
        assert pair["covers_entire_smaller_footprint"] is False
        assert pair["equal_area_cross_check_relative_difference"] < 0.005

    first_pair = next(p for p in pairwise if p["earlier_item_id"] == by_date["2009-08-12"] and p["later_item_id"] == by_date["2009-11-16"])
    assert first_pair["percent_of_smaller_footprint"] == pytest.approx(
        PUBLISHED_PRIMARY_MIN_PAIRWISE_OVERLAP_PERCENT, abs=0.02
    )
    assert first_pair["intersection_ha_geodesic"] == pytest.approx(
        PUBLISHED_PRIMARY_MIN_PAIRWISE_OVERLAP_HA, rel=0.005
    )

    later_pair = next(p for p in pairwise if p["earlier_item_id"] == by_date["2009-11-16"])
    assert later_pair["percent_of_smaller_footprint"] == pytest.approx(99.92, abs=0.02)

    assert selection["min_pairwise_overlap_percent_of_smaller_footprint"] == pytest.approx(99.64, abs=0.02)
    assert selection["equal_area_cross_check_passed"] is True
    assert selection["common_intersection_ha_geodesic"] == pytest.approx(
        PUBLISHED_PRIMARY_MIN_PAIRWISE_OVERLAP_HA, rel=0.005
    )


def test_public_calendar_day_gaps_are_not_floored_elapsed_days():
    report = select(public_catalog(), count=3, max_scene_mb=700, min_gap_days=90, min_overlap_percent=99.0)
    gaps = report["selection"]["consecutive_pairs"]
    first = public_features_by_date()["2009-08-12"]
    second = public_features_by_date()["2009-11-16"]
    assert first["properties"]["datetime"] == "2009-08-12T10:24:43+00:00"
    assert second["properties"]["datetime"] == "2009-11-16T10:24:48+00:00"
    assert gaps[0]["gap_calendar_days"] == 96
    assert gaps[0]["gap_elapsed_seconds"] == 96 * 86400 + 5


def test_public_two_scene_selection_reports_the_documented_ranking_winner():
    report = select(public_catalog(), count=2, max_scene_mb=700, min_gap_days=90, min_overlap_percent=99.0)
    selection = report["selection"]
    assert selection["count"] == 2
    assert selection["gaps_calendar_days"] == [96]
    # Ranking rule 4 prefers the largest minimum pairwise overlap, so the
    # 99.92% pair wins over the 99.64% pair that involves the smaller footprint.
    assert selection["acquisition_dates"] == ["2009-11-16", "2010-02-20"]
    assert selection["min_pairwise_overlap_percent_of_smaller_footprint"] == pytest.approx(99.92, abs=0.02)
    assert selection["considered_candidate_sets"] == 3


def test_public_slc_trio_is_selected_only_when_the_size_cap_allows_it():
    report = select(public_catalog(), count=3, max_scene_mb=6000, min_gap_days=300, min_overlap_percent=99.0)
    selection = report["selection"]
    assert selection["acquisition_dates"] == PUBLISHED_COMPARISON_DATES
    assert selection["gaps_calendar_days"] == PUBLISHED_COMPARISON_GAPS_CALENDAR_DAYS
    assert selection["scene_megabytes"] == [float(v) for v in PUBLISHED_COMPARISON_SCENE_MB]
    assert selection["group_key"]["applied_lut"] == "Land"
    assert selection["group_key"]["beam"] == "XF0W2"
    assert selection["group_key"]["polarization"] == "HH"


def test_bearer_guarded_product_asset_is_stripped_and_public_links_are_kept():
    report = select(public_catalog(), count=3, max_scene_mb=700, min_gap_days=90, min_overlap_percent=99.0)
    features = report["selected"]["features"]
    assert len(features) == 3
    for feature in features:
        assert "product" not in feature["assets"]
        assert "thumbnail" in feature["assets"]
        assert feature["assets"]["thumbnail"]["href"].startswith("https://")
        rels = {link["rel"] for link in feature["links"]}
        assert {"self", "collection"} <= rels
        assert any(link["href"].endswith(feature["id"]) for link in feature["links"])
    findings = report["sanitization"]["findings"]
    assert len(findings) == 3
    assert all(f["kind"] == "asset" and f["name"] == "product" for f in findings)
    assert all("auth:refs" in f["reason"] for f in findings)
    source_hrefs = [
        asset["href"]
        for feature in public_catalog()["features"]
        for key, asset in feature["assets"].items()
        if key == "product"
    ]
    serialised = json.dumps(report)
    for href in source_hrefs:
        assert href not in serialised
    for feature in report["selected"]["features"]:
        assert "auth:refs" not in json.dumps(feature)
        assert "dds/v1/item" not in json.dumps(feature)


def test_selected_public_items_are_preserved_in_full():
    original = {feature["id"]: feature for feature in public_catalog()["features"]}
    report = select(public_catalog(), count=3, max_scene_mb=700, min_gap_days=90, min_overlap_percent=99.0)
    for feature in report["selected"]["features"]:
        source = original[feature["id"]]
        assert feature["geometry"] == source["geometry"]
        assert feature["bbox"] == source["bbox"]
        assert feature["properties"] == source["properties"]
        assert feature["collection"] == source["collection"]
        assert feature["stac_version"] == source["stac_version"]
    assert set(report["selected"]["features"][0]["properties"]) == set(
        original[report["selected"]["features"][0]["id"]]["properties"]
    )


def test_public_items_report_no_unknown_metadata_fields():
    report = select(public_catalog(), count=3, max_scene_mb=700, min_gap_days=90, min_overlap_percent=99.0)
    for entry in report["unknown_metadata"]["items"]:
        assert entry["unknown_fields"] == []
    assert report["items_with_unknown_size"] == []


# --------------------------------------------------------------------------- #
# synthetic edge cases
# --------------------------------------------------------------------------- #
def test_calendar_day_gap_is_used_when_two_scenes_straddle_midnight_by_hours():
    items = [
        synthetic_item("a", "2020-01-01", time_text="23:30:00"),
        synthetic_item("b", "2020-01-02", time_text="00:30:00"),
    ]
    report = select(synthetic_collection(items), count=2, min_gap_days=1, min_overlap_percent=99.0)
    pair = report["selection"]["pairwise_overlap"][0]
    assert pair["gap_calendar_days"] == 1
    assert pair["gap_elapsed_seconds"] == 3600

    with pytest.raises(SelectionError) as excinfo:
        select(synthetic_collection(items), count=2, min_gap_days=2, min_overlap_percent=99.0)
    assert "calendar-day separation is 1" in str(excinfo.value)


def test_overlap_fraction_is_measured_against_the_smaller_footprint():
    small = synthetic_item("small", "2020-01-01", lon=118.0, lat=5.0, size_deg=0.2)
    large = synthetic_item("large", "2020-03-01", lon=118.15, lat=5.0, size_deg=0.4)
    document = synthetic_collection([small, large])
    report = select(document, count=2, min_gap_days=30, min_overlap_percent=0.0)
    pair = report["selection"]["pairwise_overlap"][0]
    assert 0.0 < pair["percent_of_smaller_footprint"] < 100.0
    assert pair["intersection_ha_geodesic"] < pair["smaller_footprint_ha"]
    assert pair["covers_entire_smaller_footprint"] is False
    assert pair["smaller_footprint_ha"] == pytest.approx(report["selection"]["footprint_ha_geodesic"][0], rel=1e-6)

    with pytest.raises(SelectionError) as excinfo:
        select(document, count=2, min_gap_days=30, min_overlap_percent=99.0)
    assert "overlap" in str(excinfo.value) and "--min-overlap 99.0" in str(excinfo.value)


def test_half_overlapping_squares_are_accepted_only_below_the_half_threshold():
    items = [
        synthetic_item("west", "2020-01-01", lon=118.0, size_deg=1.0),
        synthetic_item("east", "2020-03-01", lon=118.5, size_deg=1.0),
    ]
    document = synthetic_collection(items)
    report = select(document, count=2, min_gap_days=30, min_overlap_percent=40.0)
    percent = report["selection"]["pairwise_overlap"][0]["percent_of_smaller_footprint"]
    assert percent == pytest.approx(50.0, abs=0.2)
    with pytest.raises(SelectionError) as excinfo:
        select(document, count=2, min_gap_days=30, min_overlap_percent=90.0)
    assert "--min-overlap 90.0" in str(excinfo.value)


def test_mixed_polarization_or_lut_is_never_selected_as_one_group():
    items = [
        synthetic_item("vv", "2020-01-01", polarization="VV VH"),
        synthetic_item("hh", "2020-03-01", polarization="HH"),
        synthetic_item("other_lut", "2020-05-01", applied_lut="Land"),
    ]
    document = synthetic_collection(items)
    with pytest.raises(SelectionError):
        select(document, count=2, min_gap_days=30, min_overlap_percent=99.0)
    with pytest.raises(SelectionError) as excinfo:
        select(
            synthetic_collection(items[:2]),
            count=3,
            min_gap_days=30,
            min_overlap_percent=99.0,
        )
    assert "no set of 3 scenes" in str(excinfo.value)


def test_incompatible_metadata_is_reported_even_when_a_compatible_group_exists():
    items = [
        synthetic_item("a", "2020-01-01"),
        synthetic_item("b", "2020-03-01"),
        synthetic_item("hh", "2020-05-01", polarization="HH"),
    ]
    report = select(synthetic_collection(items), count=2, min_gap_days=30, min_overlap_percent=99.0)
    assert report["selection"]["item_ids"] == ["synthetic-a", "synthetic-b"]
    assert len(report["groups"]) == 2
    hh_group = [g for g in report["groups"] if g["group_key"]["polarization"] == "HH"][0]
    assert hh_group["candidate_sets_feasible"] == 0


def test_unknown_metadata_is_reported_and_never_copied_from_a_sibling():
    items = [
        synthetic_item("full", "2020-01-01"),
        synthetic_item("no_lut", "2020-03-01", applied_lut=None),
    ]
    report = select(synthetic_collection(items), count=2, min_gap_days=30, min_overlap_percent=99.0)
    assert report["selection"]["item_ids"] == ["synthetic-full", "synthetic-no_lut"]
    unknown = {entry["item_id"]: entry for entry in report["unknown_metadata"]["items"]}
    assert unknown["synthetic-no_lut"]["unknown_fields"] == ["applied_lut"]
    assert unknown["synthetic-no_lut"]["applied_lut"] is None
    assert report["selection"]["unknown_fields_by_item"]["synthetic-no_lut"] == ["applied_lut"]


def test_item_with_unknown_size_is_not_selectable_and_the_reason_is_explicit():
    items = [
        synthetic_item("sized_a", "2020-01-01"),
        synthetic_item("sized_b", "2020-03-01"),
        synthetic_item("unsized", "2020-05-01", megabytes=None),
    ]
    document = synthetic_collection(items)
    report = select(document, count=2, min_gap_days=30, min_overlap_percent=99.0, max_scene_mb=1.0e9)
    assert report["items_with_unknown_size"] == ["synthetic-unsized"]
    reasons = [r["reason"] for r in report["rejected_items"] if r["item"] == "synthetic-unsized"]
    assert reasons and "--max-scene-mb" in reasons[0]
    assert report["selection"]["item_ids"] == ["synthetic-sized_a", "synthetic-sized_b"]

    only_unsized = synthetic_collection(
        [synthetic_item("u1", "2020-01-01", megabytes=None), synthetic_item("u2", "2020-03-01", megabytes=None)]
    )
    with pytest.raises(SelectionError):
        select(only_unsized, count=2, min_gap_days=30, min_overlap_percent=99.0)


def test_product_type_filter_is_client_side_and_explicit():
    items = [
        synthetic_item("sgf", "2020-01-01", product_type="SGF"),
        synthetic_item("slc", "2020-03-01", product_type="SLC"),
    ]
    document = synthetic_collection(items)
    document = synthetic_collection(
        [
            synthetic_item("sgf", "2020-01-01", product_type="SGF"),
            synthetic_item("slc_a", "2020-03-01", product_type="SLC"),
            synthetic_item("slc_b", "2020-05-01", product_type="SLC"),
        ]
    )
    report = select(document, count=2, min_gap_days=30, min_overlap_percent=99.0, product_types=("SLC",))
    assert report["selection"]["item_ids"] == ["synthetic-slc_a", "synthetic-slc_b"]
    assert report["criteria"]["product_types"] == ["SLC"]
    assert any("product_type" in r["reason"] for r in report["rejected_items"])
    with pytest.raises(SelectionError):
        select(document, count=2, min_gap_days=30, min_overlap_percent=99.0, product_types=("SCN",))


@pytest.mark.parametrize(
    "geometry",
    [
        None,
        {"type": "Point", "coordinates": [118.0, 5.0]},
        {
            "type": "MultiPolygon",
            "coordinates": [[[[118.0, 5.0], [118.1, 5.0], [118.1, 5.1], [118.0, 5.1], [118.0, 5.0]]]],
        },
        {"type": "LineString", "coordinates": [[118.0, 5.0], [118.1, 5.1]]},
    ],
    ids=["missing", "point", "multipolygon", "linestring"],
)
def test_unsupported_geometry_is_rejected_with_an_explicit_reason(geometry):
    items = [
        synthetic_item("good", "2020-01-01"),
        synthetic_item("bad", "2020-03-01", geometry=geometry),
    ]
    document = synthetic_collection(items)
    with pytest.raises(SelectionError):
        select(document, count=2, min_gap_days=30, min_overlap_percent=99.0)
    with pytest.raises(SelectionError):
        select(synthetic_collection([items[0]]), count=2, min_gap_days=30, min_overlap_percent=99.0)


def test_unsupported_geometry_reason_is_recorded_for_the_rejected_item():
    document = synthetic_collection(
        [
            synthetic_item("good", "2020-01-01"),
            synthetic_item("good2", "2020-03-01"),
            synthetic_item("multi", "2020-05-01", geometry={
                "type": "MultiPolygon",
                "coordinates": [[[[118.0, 5.0], [118.1, 5.0], [118.1, 5.1], [118.0, 5.1], [118.0, 5.0]]]],
            }),
        ]
    )
    report = select(document, count=2, min_gap_days=30, min_overlap_percent=99.0)
    rejected = [r for r in report["rejected_items"] if r["item"] == "synthetic-multi"]
    assert rejected and "unsupported geometry type 'MultiPolygon'" in rejected[0]["reason"]


def test_antimeridian_footprint_is_rejected_explicitly():
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [179.0, 10.0],
                [-179.0, 10.0],
                [-179.0, 10.5],
                [179.0, 10.5],
                [179.0, 10.0],
            ]
        ],
    }
    document = synthetic_collection(
        [
            synthetic_item("good", "2020-01-01"),
            synthetic_item("good2", "2020-03-01"),
            synthetic_item("antimeridian", "2020-05-01", geometry=geometry),
        ]
    )
    report = select(document, count=2, min_gap_days=30, min_overlap_percent=99.0)
    rejected = [r for r in report["rejected_items"] if r["item"] == "synthetic-antimeridian"]
    assert rejected and "antimeridian-spanning" in rejected[0]["reason"]


def test_declared_non_geographic_crs_is_rejected():
    document = synthetic_collection(
        [
            synthetic_item("utm", "2020-01-01", extra_properties={"proj:code": "EPSG:32633"}),
            synthetic_item("utm2", "2020-03-01", extra_properties={"proj:code": "EPSG:32633"}),
        ]
    )
    with pytest.raises(SelectionError):
        select(document, count=2, min_gap_days=30, min_overlap_percent=99.0)


def test_missing_crs_is_reported_as_unknown_and_still_measured():
    document = synthetic_collection(
        [
            synthetic_item("a", "2020-01-01", extra_properties={"proj:code": None}),
            synthetic_item("b", "2020-03-01", extra_properties={"proj:code": None}),
        ]
    )
    report = select(document, count=2, min_gap_days=30, min_overlap_percent=99.0)
    assert "proj:code" in report["selection"]["unknown_fields_by_item"]["synthetic-a"]
    assert report["selection"]["common_intersection_ha_geodesic"] > 0.0


def test_signed_asset_href_and_credential_property_are_stripped():
    signed = "https://download.example.invalid/product.tif?X-Amz-Signature=deadbeef&X-Amz-Credential=AKIA"
    items = [
        synthetic_item(
            "signed",
            "2020-01-01",
            assets={
                "product": {"href": signed, "type": "image/tiff"},
                "thumbnail": {"href": "https://example.invalid/thumb/signed.png", "type": "image/png"},
            },
            extra_properties={
                "download_hint": "https://download.example.invalid/p.tif?token=abc123&expires=99",
                "bearer_token": "not-a-real-token",
                "footprint_note": "https://user:hunter2@example.invalid/private.tif",
            },
        ),
        synthetic_item("second", "2020-03-01"),
    ]
    report = select(synthetic_collection(items), count=2, min_gap_days=30, min_overlap_percent=99.0)
    selected = {feature["id"]: feature for feature in report["selected"]["features"]}
    signed_feature = selected["synthetic-signed"]

    assert "product" not in signed_feature["assets"]
    assert set(signed_feature["assets"]) == {"thumbnail"}
    assert "bearer_token" not in signed_feature["properties"]
    assert "download_hint" not in signed_feature["properties"]
    assert "footprint_note" not in signed_feature["properties"]
    assert "SYNTHETIC-TEST-FIXTURE" in signed_feature["properties"]

    serialised = json.dumps(report)
    for secret in ("deadbeef", "AKIA", "abc123", "hunter2", "not-a-real-token"):
        assert secret not in serialised
    kinds = {finding["kind"] for finding in report["sanitization"]["findings"]}
    assert {"asset", "property", "property_value"} <= kinds


def test_self_link_with_signed_query_is_dropped_but_public_links_survive():
    items = [
        synthetic_item(
            "a",
            "2020-01-01",
            links=[
                {"rel": "self", "href": "https://example.invalid/items/a"},
                {"rel": "alternate", "href": "https://example.invalid/items/a?signature=zzz"},
            ],
        ),
        synthetic_item("b", "2020-03-01"),
    ]
    report = select(synthetic_collection(items), count=2, min_gap_days=30, min_overlap_percent=99.0)
    links = {f["id"]: {link["rel"] for link in f["links"]} for f in report["selected"]["features"]}
    assert links["synthetic-a"] == {"self"}
    assert "zzz" not in json.dumps(report)


def test_selection_is_deterministic_and_order_independent():
    document = public_catalog()
    shuffled = copy.deepcopy(document)
    shuffled["features"] = list(reversed(shuffled["features"]))
    first = select(document, count=3, max_scene_mb=700, min_gap_days=90, min_overlap_percent=99.0)
    second = select(shuffled, count=3, max_scene_mb=700, min_gap_days=90, min_overlap_percent=99.0)
    assert first["selection"]["item_ids"] == second["selection"]["item_ids"]
    assert first["selection"] == second["selection"]
    again = select(document, count=3, max_scene_mb=700, min_gap_days=90, min_overlap_percent=99.0)
    assert first["selection"] == again["selection"]


def test_ranking_prefers_the_larger_common_footprint_then_the_smaller_size():
    wide = [
        synthetic_item("wide_a", "2020-01-01", lon=118.0, size_deg=0.9, megabytes=200.0),
        synthetic_item("wide_b", "2020-03-01", lon=118.0, size_deg=0.9, megabytes=200.0),
    ]
    narrow = [
        synthetic_item("narrow_a", "2020-01-01", lon=118.0, size_deg=0.3, megabytes=50.0),
        synthetic_item("narrow_b", "2020-03-01", lon=118.0, size_deg=0.3, megabytes=50.0),
    ]
    report = select(synthetic_collection(wide + narrow), count=2, min_gap_days=30, min_overlap_percent=99.0)
    assert report["selection"]["item_ids"] == ["synthetic-wide_a", "synthetic-wide_b"]
    assert report["selection"]["considered_candidate_sets"] == 4
    assert "common intersection" in json.dumps(report["selection_order"])
    joined = " ".join(report["selection_order"])
    assert "rank by the common intersection area" in joined
    assert "rank by the total calendar-day span" in joined
    assert "rank by total size in megabytes" in joined
    assert joined.index("rank by the common intersection area") < joined.index(
        "rank by the total calendar-day span"
    ) < joined.index("rank by total size in megabytes")


def test_shorter_span_is_ranked_before_the_item_id_tie_break():
    items = [
        synthetic_item("aaa", "2020-01-01"),
        synthetic_item("bbb", "2020-03-01"),
        synthetic_item("ccc", "2020-05-01"),
    ]
    report = select(synthetic_collection(items), count=2, min_gap_days=30, min_overlap_percent=99.0)
    # Both adjacent pairs span 60 calendar days and have identical geometry, so
    # only the ascending item-id tie-break separates them.
    assert report["selection"]["item_ids"] == ["synthetic-aaa", "synthetic-bbb"]
    assert report["selection"]["total_span_calendar_days"] == 60
    reordered = select(synthetic_collection(list(reversed(items))), count=2, min_gap_days=30, min_overlap_percent=99.0)
    assert reordered["selection"]["item_ids"] == report["selection"]["item_ids"]


def test_evenly_spaced_scenes_fall_back_to_the_item_id_tie_break():
    items = [
        synthetic_item("bbb", "2020-01-01"),
        synthetic_item("aaa", "2020-03-01"),
        synthetic_item("ccc", "2020-05-01"),
    ]
    report = select(synthetic_collection(items), count=2, min_gap_days=30, min_overlap_percent=99.0)
    assert report["selection"]["item_ids"] == ["synthetic-bbb", "synthetic-aaa"]
    assert report["selection"]["gaps_calendar_days"] == [60]


def test_selection_order_and_criteria_are_reported_without_a_risk_score():
    report = select(public_catalog(), count=3, max_scene_mb=700, min_gap_days=90, min_overlap_percent=99.0)
    assert report["criteria"] == {
        "count": 3,
        "max_scene_mb": 700.0,
        "min_gap_days": 90,
        "min_overlap_percent": 99.0,
        "product_types": "any",
    }
    assert len(report["selection_order"]) == 9
    serialised = json.dumps(report).lower()
    for forbidden in ("deforestation", "logging", "fire", "flood", "confidence", "probability", "risk_score", "risk score"):
        assert forbidden not in serialised
    assert "score" not in report["selection"]
    assert report["selection"]["note"].startswith("selection is a geometric")


def test_no_feasible_set_reports_why_rather_than_picking_something():
    with pytest.raises(SelectionError) as excinfo:
        select(public_catalog(), count=3, max_scene_mb=700, min_gap_days=10000, min_overlap_percent=99.0)
    assert "no set of 3 scenes" in str(excinfo.value)
    assert "candidate_sets_feasible" in str(excinfo.value)


def test_criteria_validation_is_explicit():
    for bad in (
        {"count": 1},
        {"count": 4},
        {"max_scene_mb": 0.0},
        {"min_gap_days": -1},
        {"min_overlap_percent": 101.0},
        {"min_overlap_percent": -0.1},
    ):
        with pytest.raises(SelectionError):
            criteria(**bad).validate()


def test_load_catalog_requires_a_feature_collection(tmp_path):
    path = write_catalog(tmp_path, {"type": "Feature"})
    with pytest.raises(SelectionError) as excinfo:
        load_catalog(path)
    assert "FeatureCollection is required" in str(excinfo.value)

    broken = os.path.join(str(tmp_path), "broken.json")
    with open(broken, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    with pytest.raises(SelectionError):
        load_catalog(broken)

    with pytest.raises(SelectionError):
        load_catalog(os.path.join(str(tmp_path), "absent.json"))


def test_load_catalog_records_a_content_hash_and_makes_no_request(tmp_path):
    path = write_catalog(tmp_path, public_catalog())
    document, provenance = load_catalog(path)
    assert len(document["features"]) == 6
    assert provenance["sha256"] == hashlib.sha256(open(path, "rb").read()).hexdigest()
    assert provenance["network_access"].startswith("none")
    assert "authentication" in provenance


def test_cli_writes_the_report_and_is_byte_stable_across_runs(tmp_path):
    catalog = write_catalog(tmp_path, public_catalog())
    out = os.path.join(str(tmp_path), "selection.json")
    argv = [catalog, "--count", "3", "--max-scene-mb", "700", "--min-gap-days", "90", "--min-overlap", "99.0", "--out", out]
    assert main(argv) == 0
    with open(out, encoding="utf-8") as handle:
        first = handle.read()
    assert main(argv) == 0
    with open(out, encoding="utf-8") as handle:
        second = handle.read()
    first_doc, second_doc = json.loads(first), json.loads(second)
    first_doc.pop("generated_utc")
    second_doc.pop("generated_utc")
    assert first_doc == second_doc

    selection = json.loads(first)["selection"]
    assert selection["acquisition_dates"] == PUBLISHED_PRIMARY_DATES
    assert json.loads(first)["input"]["sha256"]


def test_cli_requires_every_threshold_and_a_two_or_three_count(tmp_path):
    catalog = write_catalog(tmp_path, public_catalog())
    full = {
        "--count": "3",
        "--max-scene-mb": "700",
        "--min-gap-days": "90",
        "--min-overlap": "99.0",
        "--out": os.path.join(str(tmp_path), "o.json"),
    }
    for missing in full:
        argv = [catalog]
        for flag, value in full.items():
            if flag != missing:
                argv.extend([flag, value])
        with pytest.raises(SystemExit) as excinfo:
            main(argv)
        assert excinfo.value.code == 2

    rest = []
    for flag, value in full.items():
        if flag != "--count":
            rest.extend([flag, value])
    assert main([catalog, "--count", "4"] + rest) == 2
    assert main([catalog, "--count", "1"] + rest) == 2
    assert main([catalog, "--count", "0"] + rest) == 2
    assert main([catalog, "--count", "2"] + rest) == 0


def test_cli_fails_loudly_without_writing_output_when_no_candidate_exists(tmp_path):
    catalog = write_catalog(tmp_path, public_catalog())
    out = os.path.join(str(tmp_path), "selection.json")
    argv = [catalog, "--count", "3", "--max-scene-mb", "700", "--min-gap-days", "5000", "--min-overlap", "99.0", "--out", out]
    assert main(argv) == 1
    assert not os.path.exists(out)


def test_cli_product_type_filter_is_repeatable(tmp_path):
    catalog = write_catalog(tmp_path, public_catalog())
    out = os.path.join(str(tmp_path), "selection.json")
    argv = [
        catalog,
        "--count",
        "3",
        "--max-scene-mb",
        "6000",
        "--min-gap-days",
        "300",
        "--min-overlap",
        "99.0",
        "--product-type",
        "SLC",
        "--out",
        out,
    ]
    assert main(argv) == 0
    with open(out, encoding="utf-8") as handle:
        assert json.load(handle)["selection"]["acquisition_dates"] == PUBLISHED_COMPARISON_DATES


def test_module_imports_no_network_or_credential_client():
    source_path = os.path.join(REPO_ROOT, "catalog", "select.py")
    with open(source_path, encoding="utf-8") as handle:
        source = handle.read()
    for forbidden in (
        "import requests",
        "import urllib",
        "import socket",
        "import http",
        "import ftplib",
        "from requests",
        "getenv",
        ".env",
        "subprocess",
        "urlopen",
        "download(",
    ):
        assert forbidden not in source, forbidden


def test_module_runs_as_a_script_over_the_real_public_catalog(tmp_path):
    catalog = write_catalog(tmp_path, public_catalog())
    out = os.path.join(str(tmp_path), "selection.json")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "catalog.select",
            catalog,
            "--count",
            "3",
            "--max-scene-mb",
            "700",
            "--min-gap-days",
            "90",
            "--min-overlap",
            "99.0",
            "--out",
            out,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": REPO_ROOT, "HOME": "/nonexistent"},
    )
    assert completed.returncode == 0, completed.stderr
    assert "min pairwise overlap" in completed.stdout
    with open(out, encoding="utf-8") as handle:
        report = json.load(handle)
    assert report["selection"]["acquisition_dates"] == PUBLISHED_PRIMARY_DATES
    assert "auth" not in json.dumps(report["input"]).replace("authentication", "")