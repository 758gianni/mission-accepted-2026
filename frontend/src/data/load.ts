import type { Contract, RegionCollection, TemporalClassId } from './types';

const BASE = import.meta.env.BASE_URL ?? '/';

export async function loadContract(): Promise<Contract> {
	const res = await fetch(`${BASE}data/derived/contract.json`);
	if (!res.ok) throw new Error(`contract.json ${res.status}`);
	return (await res.json()) as Contract;
}

export async function loadRegions(): Promise<RegionCollection> {
	const res = await fetch(`${BASE}data/derived/candidate_regions.geojson`);
	if (!res.ok) throw new Error(`candidate_regions.geojson ${res.status}`);
	return (await res.json()) as RegionCollection;
}

export function asset(path: string): string {
	return `${BASE}${path}`;
}

export function formatHa(value: number): string {
	return `${value.toFixed(1)} ha`;
}

export function formatDb(value: number): string {
	const sign = value > 0 ? '+' : '';
	return `${sign}${value.toFixed(1)} dB`;
}

export function formatScore(value: number): string {
	return value.toFixed(1);
}

export function classOf(classId: number, t4Status: string): TemporalClassId {
	if (classId === 1 && t4Status === 'confirmed') return 't4_validated_persistent';
	if (classId === 2) return 'seasonal_transient';
	if (classId === 3) return 'late_unclassified';
	return 'persistent';
}
