"""Normalize observation metadata, never pool measurements across sensors."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

RS2_TROPICAL_COLLECTION = "Radarsat-2_Tropical_Forest_Products"


def timestamp_utc(value):
    parsed=datetime.fromisoformat(value.replace("Z", "+00:00").replace(" +0000", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class Observation:
    source_record_id: str
    sensor: str
    acquisition_iso: str
    footprint: dict
    measurement: str
    crs: str = "EPSG:4326"
    resolution_m: float | None = None
    bands: tuple[str, ...] = ()
    polarization: str | None = None
    orbit_direction: str | None = None
    look_direction: str | None = None
    relative_orbit: int | None = None
    processing_level: str | None = None
    beam_mnemonic: str | None = None
    incidence_low_deg: float | None = None
    incidence_high_deg: float | None = None
    size_bytes: int | None = None
    orderable: bool | None = None
    local_asset: str | None = None
    provenance: dict = field(default_factory=dict)
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.source_record_id or not self.sensor:
            raise ValueError("Observation requires sensor and source_record_id")
        self.timestamp  # Validate timestamp; missing dates cannot form a temporal stack.
        if self.relative_orbit is None and not self.quality_flags:
            object.__setattr__(self, "quality_flags", ("relative_orbit_unknown",))

    @property
    def timestamp(self):
        return timestamp_utc(self.acquisition_iso)

    @property
    def comparison_ready(self):
        return all(x is not None for x in (self.relative_orbit, self.polarization,
                   self.beam_mnemonic, self.orbit_direction, self.look_direction,
                   self.processing_level,self.incidence_low_deg,self.incidence_high_deg))

    def to_dict(self):
        return asdict(self)


def comparable_group_key(obs):
    """Conservative metadata strata; footprint overlap must still be checked."""
    fields = (obs.sensor, obs.measurement, obs.beam_mnemonic, obs.polarization,
              obs.orbit_direction, obs.look_direction, obs.relative_orbit,
              obs.processing_level, obs.incidence_low_deg, obs.incidence_high_deg)
    return "|".join("unknown" if f is None else str(f) for f in fields)
