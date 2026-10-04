"""Sensor adapter for actual RAPI metadata pairs and metadata2 labels."""
from shapely.geometry import shape, mapping

from .models import Observation, RS2_TROPICAL_COLLECTION


def normalize_record(record):
    fields = dict(record.get("metadata", []))
    for item in record.get("metadata2", []):
        fields.setdefault(item["label"], item.get("value"))
    fields.update({k: v for k, v in record.items() if k not in ("metadata", "metadata2")})
    flags = []

    def value(*names):
        found = next((fields[n] for n in names if fields.get(n) not in (None, "")), None)
        if isinstance(found, str) and "," in found:
            parts = {p.strip() for p in found.split(",") if p.strip()}
            if len(parts) != 1:
                flags.append("ambiguous_metadata:"+names[0])
                return None
            return next(iter(parts))
        return found

    def number(*names):
        raw = value(*names)
        try:
            return float(raw) if raw is not None else None
        except (ValueError,TypeError):
            flags.append("invalid_number:"+names[0])
            return None

    geom = shape(value("geometry", "Footprint"))
    if geom.geom_type not in ("Polygon", "MultiPolygon") or geom.is_empty or not geom.is_valid:
        raise ValueError("Record requires a valid polygon footprint")
    size = number("SIP Size (MB)", "sizeMB")
    relative = number("Relative Orbit", "relativeOrbit")
    tx, rx = value("Transmit Polarization"), value("Receive Polarization")
    pol = value("Polarization") or (tx + rx if tx and rx else None)
    orderable = value("isOrderable", "Orderable")
    if isinstance(orderable, str):
        orderable = {"true": True, "false": False}.get(orderable.lower())
    return Observation(
        source_record_id=str(value("recordId", "Sequence ID", "Sequence Id") or ""),
        sensor="RADARSAT-2", acquisition_iso=value("Start Date", "Date", "acquisitionDate") or "",
        footprint=mapping(geom), measurement="sigma0_linear_power",
        resolution_m=number("Spatial Resolution"), polarization=pol,
        orbit_direction=value("Orbit Direction"), look_direction=value("Look Orientation", "Look Direction"),
        relative_orbit=int(relative) if relative is not None else None,
        processing_level=value("Product Type", "Type"), beam_mnemonic=value("Position"),
        incidence_low_deg=number("Incidence Angle (Low)"), incidence_high_deg=number("Incidence Angle (High)"),
        size_bytes=round(size * 1024**2) if size is not None else None,
        orderable=orderable, quality_flags=tuple(flags), provenance={"collection": RS2_TROPICAL_COLLECTION,
            "title": value("title", "Title"), "catalogue_size_unit_assumption": "MiB (rounded estimate)",
            "resolution_note": "Catalogue resolution, not effective support after multilooking/resampling"})
