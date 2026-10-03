# Mission Accepted 2026


## Environmental change demo

Choose Deforestation, Wildfires, or Rivers & lakes. Each category has its own example geometries, overlay colors, details, and illustrative trend. Use the before/after slider, toggle overlays, select an example, zoom, or download the current examples as JSON. Water gain is blue; water loss is brown.

All imagery, shapes, and trends are synthetic. Areas and dates are not measured. The radar threshold is a demo setting, not a detection algorithm. Categories and facts live in `shared/change-modes.json`, shared by the UI and chatbot backend.

Real detection is a separate step: ingest dated georeferenced imagery, compare consistently processed observations, compute change masks, validate the result, and replace the demo shapes with geographic features. Burn scars and active fires need separate layers: [USGS NBR](https://www.usgs.gov/landsat-missions/landsat-normalized-burn-ratio) supports burned-area analysis; [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/map/) provides active-fire/thermal-anomaly observations. Thermal anomalies alone do not establish that an event is a wildfire.
