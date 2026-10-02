"""Response models.

The models exist so that ``/openapi.json`` documents the endpoints for the
frontend. Runtime responses are the validated bundle documents served
verbatim (the validators already guarantee they satisfy these shapes), so
no value is re-serialised, rounded or invented on the way out.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .validation import SCHEMA_VERSION


class HealthResponse(BaseModel):
    """Liveness probe."""

    status: Literal["ok"] = "ok"
    service: str = "forestwatch-api"


class StatusResponse(BaseModel):
    """Current availability of the result bundle."""

    state: Literal["awaiting_analysis", "ready", "error"]
    schema_version: Literal[1] = SCHEMA_VERSION
    analysis_id: str | None = None
    scene_count: int = 0
    message: str


class SceneResponse(BaseModel):
    id: str = Field(description="Unique within the bundle; scenes are sorted by acquired_at.")
    acquired_at: str = Field(description="ISO 8601 acquisition time in UTC.")
    polarization: str
    beam_mode: str
    orbit_direction: str
    relative_orbit: int | None
    product_type: str
    source_collection: str
    catalog_url: str = Field(description="Absolute http or https URL.")


class RegistrationResponse(BaseModel):
    status: str
    residual_pixels: float | None = None


class MethodResponse(BaseModel):
    quantity: Literal["sigma0", "gamma0"]
    units: Literal["dB"]
    change_definition: str
    threshold_db: float = Field(
        gt=0.0,
        description="Strictly positive magnitude threshold in dB, compared against "
        "each region's magnitude_db (median of |dB|) rather than the signed median.",
    )
    minimum_area_ha: float = Field(
        description="Minimum mapped area in hectares; every region must reach it."
    )
    speckle_filter: str
    registration: RegistrationResponse
    preprocessing: list[str]


class MetricsResponse(BaseModel):
    region_count: int
    total_changed_area_ha: float
    valid_area_ha: float
    not_evaluable_area_ha: float
    analysis_area_ha: float = Field(
        description="Total analysis extent: valid_area_ha + not_evaluable_area_ha."
    )
    scene_count: int


class ImageryItemResponse(BaseModel):
    path: str = Field(description="Bundle-relative file name declared by the analysis.")
    bounds: list[float] = Field(description="[west, south, east, north] in WGS84.")
    label: str
    url: str = Field(description="Relative URL for fetching the PNG preview.")


class ImageryResponse(BaseModel):
    before: ImageryItemResponse
    after: ImageryItemResponse
    change: ImageryItemResponse


class AnalysisResponse(BaseModel):
    schema_version: Literal[1]
    analysis_id: str
    title: str
    bbox: list[float]
    scenes: list[SceneResponse]
    method: MethodResponse
    metrics: MetricsResponse
    imagery: ImageryResponse
    demo_region_id: str | None = None
    limitations: list[str]


class PersistenceResponse(BaseModel):
    """Counts of real acquisitions observed after the detection acquisition."""

    status: Literal["not_evaluable", "observed"]
    observations_after_detection: int = Field(
        description="Acquisitions later than detected_at. Zero means the detection "
        "acquisition is the latest one, as with a prepared pair."
    )
    changed_observations: int
    rate: float | None = Field(
        default=None,
        description="changed_observations / observations_after_detection. "
        "Null means unavailable, not zero.",
    )


class TimeSeriesPointResponse(BaseModel):
    acquired_at: str
    mean_backscatter_db: float | None = None
    change_from_baseline_db: float | None = None
    valid_fraction: float


class ObservationIntervalResponse(BaseModel):
    """Bracketing acquisitions, not an inferred physical onset."""

    start: str
    end: str


class RegionPropertiesResponse(BaseModel):
    region_id: str
    area_ha: float
    change_db: float = Field(
        description="Signed median of the per-pixel dB change within the region."
    )
    magnitude_db: float = Field(
        description="Median of the absolute per-pixel dB change within the region. "
        "A different statistic from change_db; the two need not agree."
    )
    detected_at: str = Field(
        description="Acquisition at which a radar difference was first observed."
    )
    baseline_at: str = Field(description="Acquisition used as the baseline reference.")
    observation_interval: ObservationIntervalResponse = Field(
        description="Acquisition window the observation is bracketed by. "
        "Two scenes cannot establish a physical onset time."
    )
    priority_score: float = Field(
        description="magnitude_db * sqrt(area_ha), validated against that formula."
    )
    priority_units: str
    priority_formula: str
    persistence: PersistenceResponse
    historical_anomaly: float | None = Field(
        default=None,
        description="Null means unavailable, not zero.",
    )
    explanation: str
    time_series: list[TimeSeriesPointResponse]


class GeometryResponse(BaseModel):
    type: Literal["Polygon", "MultiPolygon"]
    coordinates: Any


class FeatureResponse(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: str
    geometry: GeometryResponse
    properties: RegionPropertiesResponse


class FeatureCollectionResponse(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[FeatureResponse]


class ErrorResponse(BaseModel):
    detail: str


__all__ = [
    "AnalysisResponse",
    "ErrorResponse",
    "FeatureCollectionResponse",
    "FeatureResponse",
    "HealthResponse",
    "ImageryResponse",
    "StatusResponse",
]
