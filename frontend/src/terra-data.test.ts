import { describe, expect, test } from 'bun:test';
import { clusterCandidates, filterCandidates, funnelStages, geometryCenter, geometryPath, getBasemapTiles, projectCoordinate, projectMapCoordinate, rankCandidates, temporalClassOf } from './terra-data';

	const candidate = (id: number, temporal_class: string, priority_score: number, t4_status = 'pending', class_id = ({ seasonal_transient: 2, persistent: 1, t4_validated_persistent: 1, late_unclassified: 3 } as Record<string, number>)[temporal_class] ?? 0) => ({
	type: 'Feature' as const,
	geometry: { type: 'Point', coordinates: [0, 0] },
	properties: { id, temporal_class, class_id, priority_score, t4_status },
});

const features = [
	candidate(7, 'persistent', 12),
	candidate(9, 'seasonal_transient', 55),
	candidate(12, 't4_validated_persistent', 40, 'confirmed'),
	candidate(19, 'late_unclassified', 2),
];

describe('TerraSignal data selectors', () => {
	test('maps temporal class IDs without treating persistence as validation', () => {
		expect(temporalClassOf(candidate(1, '', 0, 'pending', 1).properties)).toBe('persistent');
		expect(temporalClassOf(candidate(2, '', 0, 'pending', 2).properties)).toBe('seasonal_transient');
		expect(temporalClassOf(candidate(3, 'persistent', 0).properties)).toBe('persistent');
	});

	test('filters independently validated candidates from pending persistent detections', () => {
		expect(filterCandidates(features, 'validated').map((feature) => feature.properties.id)).toEqual([12]);
		expect(filterCandidates(features, 'persistent').map((feature) => feature.properties.id)).toEqual([7, 12]);
	});

	test('basemap tiles cover the visible AOI and adjacent tile bounds meet exactly', () => {
		const bounds = { west: 93, south: 17, east: 97, north: 25 };
		const tiles = getBasemapTiles(bounds, { zoom: 1, x: 700, y: 450 }, 1400, 900);
		expect(tiles.length).toBeGreaterThan(0);
		expect(tiles.every((tile) => tile.src === `https://tile.openstreetmap.org/${tile.z}/${tile.x}/${tile.y}.png`)).toBe(true);
		expect(tiles.every((tile) => tile.bounds.east > tile.bounds.west && tile.bounds.north > tile.bounds.south)).toBe(true);
		const first = tiles[0];
		const sameRowRight = tiles.find((tile) => tile.z === first.z && tile.y === first.y && tile.x === first.x + 1);
		if (sameRowRight) expect(first.bounds.east).toBe(sameRowRight.bounds.west);
	});

	test('ranks the selected class by supplied priority score', () => {
		expect(rankCandidates(features, 'persistent', 2).map((feature) => feature.properties.id)).toEqual([12, 7]);
	});

	test('builds funnel values from generated contract counts', () => {
		const stages = funnelStages({
			counts: { seasonal_transient: 440, persistent: 69, t4_validated_persistent: 11, late_unclassified: 5 },
			metrics: { total_candidates_analyzed: 525 },
		});
		expect(stages.map((stage) => stage.value)).toEqual([525, 440, 80, 11]);
	});

	test('projects candidate coordinates into the shared image bounds', () => {
		const bounds = { west: 90, south: 10, east: 100, north: 20 };
		expect(projectCoordinate([90, 10], bounds, 1000, 1060)).toEqual([0, 1060]);
		expect(projectCoordinate([100, 20], bounds, 1000, 1060)).toEqual([1000, 0]);
	});

	test('fits geographic map coordinates without stretching longitude against latitude', () => {
		const bounds = { west: 90, south: 10, east: 100, north: 20 };
		const westNorth = projectMapCoordinate([90, 20], bounds, 1000, 1000);
		const eastSouth = projectMapCoordinate([100, 10], bounds, 1000, 1000);
		expect(westNorth[1]).toBe(0);
		expect(eastSouth[1]).toBe(1000);
		expect(westNorth[0]).toBeGreaterThan(0);
		expect(eastSouth[0]).toBeLessThan(1000);
	});

	test('turns a GeoJSON polygon into an SVG overlay and computes its center', () => {
		const polygon = { type: 'Polygon', coordinates: [[[90, 20], [100, 20], [100, 10], [90, 10], [90, 20]]] };
		const bounds = { west: 90, south: 10, east: 100, north: 20 };
		expect(geometryPath(polygon, bounds, 1000, 1060)).toBe('M0 0L1000 0L1000 1060L0 1060L0 0Z');
		expect(geometryCenter(polygon, bounds, 1000, 1060)).toEqual([500, 530]);
	});

	test('clusters dense overview candidates without losing candidate membership', () => {
		const nearby = [candidate(1, 'persistent', 10), candidate(2, 'seasonal_transient', 5)];
		nearby[0].geometry = { type: 'Point', coordinates: [95, 15] };
		nearby[1].geometry = { type: 'Point', coordinates: [95.1, 15.1] };
		const clusters = clusterCandidates(nearby, { west: 90, south: 10, east: 100, north: 20 }, 1000, 1060, 50);
		expect(clusters).toHaveLength(1);
		expect(clusters[0].count).toBe(2);
		expect(clusters[0].features.map((feature) => feature.properties.id)).toEqual([1, 2]);
	});
});
