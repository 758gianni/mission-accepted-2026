# Presentation metadata

These files supplement the generated scientific results; they do not alter candidate classes, validation, or QA.

- `candidate-notes.json` records the project owner's unverified water-body interpretation for candidate 2. The UI shows the attribution and an explicit unverified label.
- `imagery-layout.json` records the preview content extents. The validated renderer adds a 45-pixel report header to temporal RGB and 44 pixels to acquisition quicklooks. The UI excludes those headers before placing imagery or cropping evidence. Original assets remain unchanged.

Pass these alongside the existing build arguments when regenerating the dashboard:

```sh
--candidate-notes dashboard/config/candidate-notes.json \
--imagery-layout dashboard/config/imagery-layout.json
```

`public/data/context/geography.geojson` contains regional country boundaries and rivers from Natural Earth's public-domain 1:50m datasets. Source URLs are https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson and https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_rivers_lake_centerlines.geojson. Features were selected by intersection of their bounding boxes with the dataset map bounds expanded 12 degrees east/west and 8 degrees north/south. Labels use the source's geographic label coordinates. This local layer provides context without network requests; it is not an analysis or validation dataset. The builder includes its asset reference when present in the output directory. Supply appropriate context and presentation metadata when changing datasets.

Street detail is an optional online layer. The application defaults to the local geographic context because the external tile endpoint returned HTTP 429 during verification.
