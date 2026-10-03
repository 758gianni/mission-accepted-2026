"""ForestWatch read-only HTTP API.

The service exposes an already-produced result bundle to the frontend. It
never acquires, processes or writes data: every route is a ``GET``.

Bundle source (in order of precedence):

1. the ``bundle_dir`` argument of :func:`create_app`
2. the ``FORESTWATCH_BUNDLE_DIR`` environment variable
3. ``data/processed/current``
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .bundle import AWAITING_MESSAGE, ERROR_MESSAGE, BundleSnapshot, BundleStore
from .config import default_bundle_dir, default_cors_origins
from .models import (
    AnalysisResponse,
    ErrorResponse,
    FeatureCollectionResponse,
    FeatureResponse,
    HealthResponse,
    StatusResponse,
)
from .validation import IMAGERY_KEYS

SERVICE_NAME = "forestwatch-api"

DESCRIPTION = """
Read-only access to a ForestWatch result bundle (`analysis.json`,
`regions.geojson` and the three declared PNG previews).

The API performs no acquisition, processing or writing. When no bundle has
been produced yet the status endpoint reports `awaiting_analysis` and the
data endpoints answer `404`. When a bundle exists but violates the contract,
the status endpoint reports `error` and the data endpoints answer `503`;
values are never fabricated to fill a gap.
""".strip()

TAGS_METADATA = [
    {"name": "health", "description": "Liveness probe."},
    {"name": "status", "description": "Result bundle availability."},
    {"name": "analysis", "description": "Validated analysis document."},
    {"name": "regions", "description": "Validated change regions (GeoJSON)."},
    {"name": "imagery", "description": "PNG previews declared by the analysis."},
]

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "No result bundle has been produced yet."},
    503: {"model": ErrorResponse, "description": "The result bundle does not satisfy the contract."},
}


def create_app(
    bundle_dir: Path | str | None = None,
    *,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Build the ForestWatch API application.

    Parameters
    ----------
    bundle_dir:
        Directory holding ``analysis.json``, ``regions.geojson`` and the PNG
        previews. Defaults to ``FORESTWATCH_BUNDLE_DIR`` and then to
        ``data/processed/current``.
    cors_origins:
        Allowed browser origins. Defaults to localhost:3000 and
        localhost:5173, overridable with ``FORESTWATCH_CORS_ORIGINS``.
    """
    resolved_dir = Path(bundle_dir) if bundle_dir is not None else default_bundle_dir()
    resolved_origins = list(cors_origins) if cors_origins is not None else default_cors_origins()

    app = FastAPI(
        title="ForestWatch result API",
        version="0.1.0",
        description=DESCRIPTION,
        openapi_tags=TAGS_METADATA,
    )
    store = BundleStore(resolved_dir)
    app.state.bundle_store = store
    app.state.bundle_dir = resolved_dir
    app.state.cors_origins = resolved_origins

    @app.middleware("http")
    async def _restricted_cors(request: Request, call_next):  # type: ignore[no-untyped-def]
        origin = request.headers.get("origin")
        if origin and origin in app.state.cors_origins:
            response = await call_next(request)
            response.headers["access-control-allow-origin"] = origin
            response.headers["vary"] = "Origin"
            response.headers["access-control-allow-methods"] = "GET, HEAD, OPTIONS"
            response.headers["access-control-allow-headers"] = "Accept, Content-Type"
            response.headers["access-control-max-age"] = "600"
            response.headers.setdefault("x-content-type-options", "nosniff")
            return response
        response = await call_next(request)
        response.headers.setdefault("x-content-type-options", "nosniff")
        return response

    def snapshot() -> BundleSnapshot:
        return store.snapshot()

    @app.get("/health", tags=["health"], response_model=HealthResponse, summary="Liveness probe")
    def health() -> HealthResponse:
        """Liveness only. Reports nothing about the bundle, so it stays useful
        as a container health check even when no analysis exists yet."""
        return HealthResponse()

    @app.get(
        "/api/status",
        tags=["status"],
        response_model=StatusResponse,
        summary="Result bundle availability",
    )
    def status(current: BundleSnapshot = Depends(snapshot)) -> StatusResponse:
        return StatusResponse(
            state=current.state,
            analysis_id=current.analysis_id,
            scene_count=current.scene_count,
            message=current.message,
        )

    @app.get(
        "/api/analysis",
        tags=["analysis"],
        response_model=AnalysisResponse,
        responses=ERROR_RESPONSES,
        summary="Validated analysis document",
    )
    def analysis(current: BundleSnapshot = Depends(snapshot)) -> Response:
        guard = _guard(current)
        if guard is not None:
            return guard
        document = copy.deepcopy(current.bundle.analysis)  # type: ignore[union-attr]
        for key in IMAGERY_KEYS:
            document["imagery"][key]["url"] = f"/api/imagery/{key}"
        return JSONResponse(document)

    @app.get(
        "/api/regions",
        tags=["regions"],
        response_model=FeatureCollectionResponse,
        responses=ERROR_RESPONSES,
        summary="Validated change regions as GeoJSON",
    )
    def regions(current: BundleSnapshot = Depends(snapshot)) -> Response:
        guard = _guard(current)
        if guard is not None:
            return guard
        return JSONResponse(copy.deepcopy(current.bundle.regions))  # type: ignore[union-attr]

    @app.get(
        "/api/regions/{region_id}",
        tags=["regions"],
        response_model=FeatureResponse,
        responses={
            404: {"model": ErrorResponse, "description": "No such region in the current bundle."},
            503: {"model": ErrorResponse, "description": "The result bundle does not satisfy the contract."},
        },
        summary="A single change region as a GeoJSON Feature",
    )
    def region(region_id: str, current: BundleSnapshot = Depends(snapshot)) -> Response:
        guard = _guard(current)
        if guard is not None:
            return guard
        feature = current.bundle.region_index.get(region_id)  # type: ignore[union-attr]
        if feature is None:
            return JSONResponse(
                {"detail": f"Region '{_safe_id(region_id)}' is not present in the current result bundle."},
                status_code=404,
            )
        return JSONResponse(copy.deepcopy(feature))

    @app.get(
        "/api/imagery/{key}",
        tags=["imagery"],
        response_model=None,
        responses={
            200: {
                "content": {"image/png": {}},
                "description": "PNG preview declared by the analysis document.",
            },
            404: {"model": ErrorResponse, "description": "No result bundle, or no such imagery key."},
            503: {"model": ErrorResponse, "description": "The result bundle does not satisfy the contract."},
        },
        summary="PNG preview declared by the analysis document",
    )
    def imagery(key: str, current: BundleSnapshot = Depends(snapshot)) -> Response:
        guard = _guard(current)
        if guard is not None:
            return guard
        if key not in IMAGERY_KEYS:
            return JSONResponse(
                {
                    "detail": "Imagery key must be one of: "
                    + ", ".join(IMAGERY_KEYS)
                    + "."
                },
                status_code=404,
            )
        entry = current.bundle.imagery[key]  # type: ignore[union-attr]
        # Re-validate containment and file type on every read so that a file
        # swapped for a symlink after validation cannot be served.
        try:
            data = _read_png(store.bundle_dir, entry.filename)
        except OSError:
            return JSONResponse(
                {"detail": "The declared image preview is no longer readable."}, status_code=503
            )
        return Response(content=data, media_type="image/png")

    return app


def _guard(current: BundleSnapshot) -> Response | None:
    if current.state == "awaiting_analysis":
        return JSONResponse({"detail": AWAITING_MESSAGE}, status_code=404)
    if current.state == "error":
        return JSONResponse({"detail": ERROR_MESSAGE}, status_code=503)
    return None


def _read_png(bundle_dir: Path, filename: str) -> bytes:
    """Containment and signature guard on the hot read path.

    Full integrity is established when the bundle is validated, and the
    snapshot is revalidated whenever a declared file changes, so this only
    re-checks the cheap invariants and the size bound before handing bytes back.
    """
    from .pngcheck import MAX_IMAGE_BYTES, PNG_SIGNATURE

    root = Path(bundle_dir).resolve()
    candidate = root / filename
    if candidate.parent != root or candidate.is_symlink():
        raise OSError("imagery path is not a contained regular file")
    with candidate.open("rb") as handle:
        data = handle.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise OSError("imagery file is larger than the permitted preview size")
    if not data.startswith(PNG_SIGNATURE):
        raise OSError("imagery file is not a PNG")
    return data


def _safe_id(value: str) -> str:
    """Echo a client-supplied id without reflecting raw control characters."""
    cleaned = "".join(char for char in value if char.isprintable())
    return cleaned[:120] or "unknown"


app = create_app()

__all__ = ["SERVICE_NAME", "app", "create_app"]
