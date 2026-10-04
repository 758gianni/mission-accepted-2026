export type TemporalClass = 'seasonal_transient' | 'persistent' | 't4_validated_persistent' | 'late_unclassified' | 'other';
export type CandidateFilter = 'all' | 'seasonal_transient' | 'persistent' | 'validated' | 'late_unclassified' | 'qa_cleared';
export type FunnelStage = { id: CandidateFilter; label: string; value: number };

export interface CandidateProperties {
	id: string | number;
	class_id?: number;
	temporal_class?: string;
	t4_status?: string;
	priority_score?: number | null;
	area_ha?: number;
	mean_change_db?: number;
	mean_signed_db?: number;
	first_observed?: string;
	observation_interval?: string[];
	terrain_risk_pct?: number | null;
	terrain_tier?: string;
	tile_support?: { tile_id: string | null; observation_count: number; temporal_class_id: number | null; status: string };
	interpretation?: string;
	[key: string]: unknown;
}

export interface CandidateFeature {
	type: 'Feature';
	geometry: { type: string; coordinates: unknown };
	properties: CandidateProperties;
}

export interface DashboardContract {
	imagery_layout?: Record<string, { width: number; height: number; x: number; y: number; content_width: number; content_height: number }>;
	geographic_context?: { asset: string; source: string; source_url: string };
	candidate_notes?: Record<string, { hypothesis: string; attribution: string; status: string; detail: string }>;
	counts: Record<string, number>;
	metrics?: Record<string, unknown> & { total_candidates_analyzed?: number };
	acquisitions?: Acquisition[];
	scene?: { name?: string; footprint?: GeoBounds; analysis_area_ha?: number; product?: string; beam?: string; polarization?: string; orbit?: string; resolution_m?: number };
	map_bounds?: GeoBounds;
	tiles?: AnalysisTile[];
	tile_processing?: {
		tile_count?: number;
		acquisition_count?: number;
		acquisition_dates?: string[];
		analysis_valid_pixels?: number;
		satellite_coverage_pixels?: number;
		insufficient_evidence_pixels?: number;
		analysis_valid_area_km2?: number;
		satellite_coverage_area_km2?: number;
		requested_aoi_area_km2?: number | null;
		requested_aoi_geometry_available?: boolean;
		area_note?: string;
	};
	baseline_comparability?: { common_scene_footprint_km2?: number; common_valid_grid_area_km2?: number; note?: string };
	image_bounds?: GeoBounds;
	imagery?: Record<string, string>;
	processing?: Record<string, number | number[] | string | undefined>;
	registration_qa?: {
		pair_checks?: { comparisons: number; passing: number; insufficient: number; review_required: number };
		candidate_subset?: { screened: number; registration_passing: number; registration_insufficient: number; initial_screen_passing: number };
		interpretation?: string;
	};
	t4?: { available?: boolean; covered_candidates?: number; independently_supported_candidates?: number; strong_support_candidates?: number; moderate_support_candidates?: number; outside_swath?: number; note?: string };
	[key: string]: unknown;
}

export interface Acquisition {
	id: string;
	date: string;
	iso?: string;
	sensor?: string;
	role?: string;
	primary?: boolean;
	image?: string | null;
	source_ids?: string[];
	source_count?: number;
	footprint?: { type: string; coordinates: unknown };
	metadata?: Record<string, unknown>;
}

export interface AnalysisTile {
	tile_id: string;
	crs: string;
	corners: [number, number][];
	display_bounds: GeoBounds;
	width: number;
	height: number;
	preview_width?: number;
	preview_height?: number;
	dates: string[];
	source_record_ids: string[];
	analysis_valid_pixels: number;
	satellite_coverage_pixels: number;
	insufficient_evidence_pixels: number;
	layers: Record<string, string>;
}

export interface GeoBounds { west: number; south: number; east: number; north: number }

export interface CandidateCluster {
	x: number;
	y: number;
	count: number;
	validatedCount: number;
	persistentCount: number;
	features: CandidateFeature[];
}

export interface BasemapTile { z: number; x: number; y: number; bounds: GeoBounds; src: string }

export function getBasemapTiles(bounds: GeoBounds, view: { zoom: number; x: number; y: number }, width: number, height: number): BasemapTile[] {
	const latitudeScale = Math.cos(((bounds.north + bounds.south) / 2) * Math.PI / 180);
	const projectedWidth = (bounds.east - bounds.west) * latitudeScale;
	const projectedHeight = bounds.north - bounds.south;
	const scale = Math.min(width / projectedWidth, height / projectedHeight);
	const offsetX = (width - projectedWidth * scale) / 2;
	const offsetY = (height - projectedHeight * scale) / 2;
	const unproject = (x: number, y: number): [number, number] => [
		bounds.west + (x - offsetX) / (latitudeScale * scale),
		bounds.north - (y - offsetY) / scale,
	];
	const halfWidth = width / (2 * view.zoom) * 1.08;
	const halfHeight = height / (2 * view.zoom) * 1.08;
	const [west, north] = unproject(view.x - halfWidth, view.y - halfHeight);
	const [east, south] = unproject(view.x + halfWidth, view.y + halfHeight);
	const northClamped = Math.min(85.05112878, north);
	const southClamped = Math.max(-85.05112878, south);
	const spanLongitude = Math.max(0.0001, east - west);
	const spanLatitude = Math.max(0.0001, northClamped - southClamped);
	const pixelsPerDegreeX = width / spanLongitude;
	const pixelsPerDegreeY = height / spanLatitude;
	const mercatorScale = Math.cos(((northClamped + southClamped) / 2) * Math.PI / 180);
	const zoom = Math.max(2, Math.min(18, Math.round(Math.log2(Math.max(pixelsPerDegreeX, pixelsPerDegreeY * mercatorScale) * 360 / 256))));
	const count = 2 ** zoom;
	const tileX = (longitude: number) => Math.max(0, Math.min(count - 1, Math.floor((longitude + 180) / 360 * count)));
	const tileY = (latitude: number) => {
		const radians = Math.max(-85.05112878, Math.min(85.05112878, latitude)) * Math.PI / 180;
		return Math.max(0, Math.min(count - 1, Math.floor((1 - Math.asinh(Math.tan(radians)) / Math.PI) / 2 * count)));
	};
	const firstX = tileX(west), lastX = tileX(east);
	const firstY = tileY(northClamped), lastY = tileY(southClamped);
	const tiles: BasemapTile[] = [];
	for (let y = firstY; y <= lastY; y++) for (let x = firstX; x <= lastX; x++) {
		const tileNorth = Math.atan(Math.sinh(Math.PI * (1 - 2 * y / count))) * 180 / Math.PI;
		const tileSouth = Math.atan(Math.sinh(Math.PI * (1 - 2 * (y + 1) / count))) * 180 / Math.PI;
		const tileBounds = { west: x / count * 360 - 180, east: (x + 1) / count * 360 - 180, north: tileNorth, south: tileSouth };
		tiles.push({ z: zoom, x, y, bounds: tileBounds, src: `https://tile.openstreetmap.org/${zoom}/${x}/${y}.png` });
	}
	return tiles;
}

export function projectCoordinate(coordinate: number[], bounds: GeoBounds, width: number, height: number): [number, number] {
	return [
		((coordinate[0] - bounds.west) / (bounds.east - bounds.west)) * width,
		((bounds.north - coordinate[1]) / (bounds.north - bounds.south)) * height,
	];
}

export function projectMapCoordinate(coordinate: number[], bounds: GeoBounds, width: number, height: number): [number, number] {
	const latitudeScale = Math.cos(((bounds.north + bounds.south) / 2) * Math.PI / 180);
	const projectedWidth = (bounds.east - bounds.west) * latitudeScale;
	const projectedHeight = bounds.north - bounds.south;
	const scale = Math.min(width / projectedWidth, height / projectedHeight);
	const offsetX = (width - projectedWidth * scale) / 2;
	const offsetY = (height - projectedHeight * scale) / 2;
	return [
		offsetX + (coordinate[0] - bounds.west) * latitudeScale * scale,
		offsetY + (bounds.north - coordinate[1]) * scale,
	];
}

function collectCoordinates(value: unknown, output: number[][]): void {
	if (!Array.isArray(value)) return;
	if (value.length >= 2 && typeof value[0] === 'number' && typeof value[1] === 'number') {
		output.push([value[0], value[1]]);
		return;
	}
	for (const child of value) collectCoordinates(child, output);
}

type CoordinateProjector = typeof projectCoordinate;

export function geometryExtent(geometry: CandidateFeature['geometry'], bounds: GeoBounds, width: number, height: number, project: CoordinateProjector = projectCoordinate): [number, number, number, number] {
	const coordinates: number[][] = [];
	collectCoordinates(geometry.coordinates, coordinates);
	const points = coordinates.map((coordinate) => project(coordinate, bounds, width, height));
	if (!points.length) return [0, 0, width, height];
	return [Math.min(...points.map(([x]) => x)), Math.min(...points.map(([, y]) => y)), Math.max(...points.map(([x]) => x)), Math.max(...points.map(([, y]) => y))];
}

export function geometryCenter(geometry: CandidateFeature['geometry'], bounds: GeoBounds, width: number, height: number,
	project: CoordinateProjector = projectCoordinate): [number, number] {
	const coordinates: number[][] = [];
	collectCoordinates(geometry.coordinates, coordinates);
	if (!coordinates.length) return [width / 2, height / 2];
	const projected = coordinates.map((coordinate) => project(coordinate, bounds, width, height));
	const xs = projected.map(([x]) => x), ys = projected.map(([, y]) => y);
	return [
		(Math.min(...xs) + Math.max(...xs)) / 2,
		(Math.min(...ys) + Math.max(...ys)) / 2,
	];
}

export function geometryPath(geometry: CandidateFeature['geometry'] | Acquisition['footprint'], bounds: GeoBounds,
	width: number, height: number, project: CoordinateProjector = projectCoordinate): string {
	if (!geometry) return '';
	const paths: string[] = [];
	const addRing = (ring: unknown, close = true) => {
		if (!Array.isArray(ring) || ring.length < 2) return;
		const points = ring.filter((point): point is number[] => Array.isArray(point) && typeof point[0] === 'number')
			.map((point) => project(point, bounds, width, height));
		if (points.length < 2) return;
		paths.push(`M${points.map(([x, y]) => `${roundPixel(x)} ${roundPixel(y)}`).join('L')}${close ? 'Z' : ''}`);
	};
	const visit = (coordinates: unknown, type: string) => {
		if (type === 'LineString') addRing(coordinates, false);
		else if (type === 'MultiLineString') (coordinates as unknown[][]).forEach((line) => addRing(line, false));
		else if (type === 'Polygon') (coordinates as unknown[][]).forEach((ring) => addRing(ring));
		else if (type === 'MultiPolygon') (coordinates as unknown[][][]).forEach((polygon) => polygon.forEach((ring) => addRing(ring)));
		else if (type === 'GeometryCollection') {
			((geometry as { geometries?: Array<{ type: string; coordinates: unknown }> }).geometries ?? [])
				.forEach((child) => visit(child.coordinates, child.type));
		}
	};
	visit(geometry.coordinates, geometry.type);
	return paths.join('');
}

function roundPixel(value: number): number { return Math.round(value * 100) / 100; }

export function clusterCandidates(features: CandidateFeature[], bounds: GeoBounds, width: number, height: number,
	cellSize = 46, project: CoordinateProjector = projectCoordinate): CandidateCluster[] {
	const clusters = new Map<string, CandidateCluster>();
	for (const feature of features) {
		const [x, y] = geometryCenter(feature.geometry, bounds, width, height, project);
		const key = `${Math.floor(x / cellSize)}:${Math.floor(y / cellSize)}`;
		const cluster = clusters.get(key) ?? { x: 0, y: 0, count: 0, validatedCount: 0, persistentCount: 0, features: [] };
		cluster.x += x; cluster.y += y; cluster.count += 1; cluster.features.push(feature);
		const temporalClass = temporalClassOf(feature.properties);
		if (temporalClass === 't4_validated_persistent') cluster.validatedCount += 1;
		if (temporalClass === 'persistent' || temporalClass === 't4_validated_persistent') cluster.persistentCount += 1;
		clusters.set(key, cluster);
	}
	return [...clusters.values()].map((cluster) => ({
		...cluster,
		x: cluster.x / cluster.count,
		y: cluster.y / cluster.count,
	}));
}

export function temporalClassOf(properties: CandidateProperties): TemporalClass {
	if (properties.t4_status === 'confirmed') return 't4_validated_persistent';
	if (properties.temporal_class === 't4_validated_persistent') return 't4_validated_persistent';
	if (properties.temporal_class === 'seasonal_transient' || properties.class_id === 2) return 'seasonal_transient';
	if (properties.temporal_class === 'persistent' || properties.class_id === 1) return 'persistent';
	if (properties.temporal_class === 'late_unclassified' || properties.class_id === 3) return 'late_unclassified';
	return 'other';
}

export function filterCandidates(features: CandidateFeature[], filter: CandidateFilter): CandidateFeature[] {
	return features.filter(({ properties }) => {
		const temporalClass = temporalClassOf(properties);
		switch (filter) {
			case 'all': return true;
			case 'seasonal_transient': return temporalClass === 'seasonal_transient';
			case 'persistent': return temporalClass === 'persistent' || temporalClass === 't4_validated_persistent';
			case 'validated': return temporalClass === 't4_validated_persistent';
			case 'late_unclassified': return temporalClass === 'late_unclassified';
			case 'qa_cleared': return properties.screening && (properties.screening as Record<string, unknown>).status === 'passes_initial_screen';
		}
	});
}

export function rankCandidates(features: CandidateFeature[], filter: CandidateFilter = 'all', limit = 8): CandidateFeature[] {
	return filterCandidates(features, filter).slice().sort((a, b) => {
		const scoreDifference = (b.properties.priority_score ?? 0) - (a.properties.priority_score ?? 0);
		return scoreDifference || String(a.properties.id).localeCompare(String(b.properties.id), undefined, { numeric: true });
	}).slice(0, limit);
}

export function funnelStages(contract: DashboardContract): FunnelStage[] {
	const counts = contract.counts ?? {};
	const validated = counts.t4_validated_persistent ?? 0;
	return [
		{ id: 'all', label: 'Detected', value: contract.metrics?.total_candidates_analyzed ?? Object.values(counts).reduce((sum, count) => sum + count, 0) },
		{ id: 'seasonal_transient', label: 'Seasonal', value: counts.seasonal_transient ?? 0 },
		{ id: 'persistent', label: 'Persistent', value: (counts.persistent ?? 0) + validated },
		{ id: 'validated', label: 'Independent support', value: validated },
	];
}
