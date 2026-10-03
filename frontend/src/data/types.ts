export type TemporalClassId =
	| 'seasonal_transient'
	| 'persistent'
	| 't4_validated_persistent'
	| 'late_unclassified';

export type T4Status = 'pending' | 'confirmed' | 'weakened' | 'outside_swath';

export interface RegionProperties {
	id: number;
	label: string;
	class_id: number;
	direction: 'increase' | 'decrease';
	area_ha: number;
	pixel_count: number;
	mean_change_db: number;
	max_change_db: number;
	mean_signed_db: number;
	first_observed: string;
	observation_interval: [string, string];
	persists_into_T3: boolean | null;
	mean_power_by_date: number[];
	interpretation: string;
	mean_slope_deg: number;
	max_slope_deg: number;
	mean_elevation_m: number;
	terrain_risk_pct: number;
	terrain_tier: string;
	priority_score: number;
	temporal_class: TemporalClassId;
	t4_status: T4Status;
	t4_mean_power: number | null;
	t4_mean_signed_db: number | null;
	t4_observation: string | null;
}

export interface RegionFeature {
	type: 'Feature';
	geometry: { type: 'Polygon' | 'MultiPolygon'; coordinates: unknown };
	properties: RegionProperties;
}

export interface RegionCollection {
	type: 'FeatureCollection';
	features: RegionFeature[];
}

export interface Acquisition {
	id: string;
	date: string;
	iso: string;
	beam: string;
	polarization: string;
	orbit: string;
	role: string;
}

export interface TemporalClassMeta {
	id: TemporalClassId;
	label: string;
	class_id: number;
	color: string;
	description: string;
}

export interface ComparisonSlot {
	id: string;
	date: string;
	image: string | null;
	available: boolean;
}

export interface PresentationStep {
	step: number;
	id: string;
	title: string;
	detail: string;
}

export interface Contract {
	contract_version: number;
	generated: string;
	scene: {
		footprint: { west: number; south: number; east: number; north: number };
		crs_analysis: string;
		crs_display: string;
		product: string;
		beam: string;
		polarization: string;
		orbit: string;
		resolution_m: number;
	};
	acquisitions: Acquisition[];
	temporal_classes: Record<TemporalClassId, TemporalClassMeta>;
	counts: Record<TemporalClassId, number>;
	metrics: {
		total_candidates_analyzed: number;
		persistent_candidates_total: number;
		persistent_high_priority_clean: number;
		persistent_terrain_flagged: number;
		seasonal_transient_valley_regions: number;
		terrain_risk_pixels_pct: number;
		elevation_range_m: [number, number];
		top_persistent_candidates: Array<RegionProperties & Record<string, unknown>>;
	};
	t4: {
		available: boolean;
		regions_tested: number;
		note: string;
	};
	presentation_path: PresentationStep[];
	imagery: Record<string, string>;
	comparisons: ComparisonSlot[];
	image_bounds: { west: number; south: number; east: number; north: number };
}
