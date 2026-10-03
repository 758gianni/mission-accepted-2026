import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Contract, RegionCollection, RegionProperties, TemporalClassId } from './data/types';
import { loadContract, loadRegions } from './data/load';
import { MapView, type LayerKey } from './components/MapView';
import { Controls } from './components/Controls';
import { Inspector } from './components/Inspector';
import { Comparison } from './components/Comparison';
import { Timeline } from './components/Timeline';
import { Presenter } from './components/Presenter';

const ALL_CLASSES: TemporalClassId[] = [
	'seasonal_transient',
	'persistent',
	't4_validated_persistent',
	'late_unclassified',
];

const Root = () => {
	const [contract, setContract] = useState<Contract | null>(null);
	const [regions, setRegions] = useState<RegionCollection | null>(null);
	const [error, setError] = useState<string | null>(null);

	const [activeClasses, setActiveClasses] = useState<Set<TemporalClassId>>(new Set(ALL_CLASSES));
	const [layers, setLayers] = useState<Record<LayerKey, boolean>>({
		temporalRgb: true,
		seasonal: true,
		persistent: true,
		terrainQa: false,
	});
	const [selectedId, setSelectedId] = useState<number | null>(null);
	const [stepIndex, setStepIndex] = useState(0);
	const [focusBounds, setFocusBounds] = useState<[[number, number], [number, number]] | null>(null);

	useEffect(() => {
		let cancelled = false;
		Promise.all([loadContract(), loadRegions()])
			.then(([c, r]) => {
				if (cancelled) return;
				setContract(c);
				setRegions(r);
			})
			.catch((e: unknown) => {
				if (!cancelled) setError(e instanceof Error ? e.message : String(e));
			});
		return () => {
			cancelled = true;
		};
	}, []);

	const regionById = useMemo(() => {
		const map = new Map<number, RegionProperties>();
		regions?.features.forEach((f) => map.set(f.properties.id, f.properties));
		return map;
	}, [regions]);

	const selected = selectedId != null ? (regionById.get(selectedId) ?? null) : null;

	const toggleClass = useCallback((id: TemporalClassId) => {
		setActiveClasses((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});
	}, []);

	const toggleLayer = useCallback((key: LayerKey) => {
		setLayers((prev) => ({ ...prev, [key]: !prev[key] }));
	}, []);

	const applyStep = useCallback(
		(index: number) => {
			setStepIndex(index);
			const id = contract?.presentation_path[index]?.id;
			if (!id) return;
			if (id === 'overview') {
				setActiveClasses(new Set(ALL_CLASSES));
				setLayers({ temporalRgb: true, seasonal: true, persistent: true, terrainQa: false });
				setSelectedId(null);
				setFocusBounds(null);
			} else if (id === 'seasonal') {
				setActiveClasses(new Set<TemporalClassId>(['seasonal_transient']));
				setLayers({ temporalRgb: true, seasonal: true, persistent: true, terrainQa: false });
				setSelectedId(null);
				setFocusBounds(null);
			} else if (id === 'persistent') {
				setActiveClasses(new Set<TemporalClassId>(['persistent', 't4_validated_persistent']));
				setLayers({ temporalRgb: true, seasonal: true, persistent: true, terrainQa: false });
				setSelectedId(null);
				setFocusBounds(null);
			} else if (id === 'region-2') {
				setActiveClasses(new Set<TemporalClassId>(['persistent', 't4_validated_persistent']));
				setLayers({ temporalRgb: true, seasonal: false, persistent: true, terrainQa: false });
				setSelectedId(2);
				setFocusBounds([
					[22.62, 93.36],
					[23.79, 94.6],
				]);
			} else if (id === 'terrain-qa') {
				setActiveClasses(new Set<TemporalClassId>(['persistent', 't4_validated_persistent']));
				setLayers({ temporalRgb: true, seasonal: false, persistent: true, terrainQa: true });
				setSelectedId(2);
				setFocusBounds([
					[22.62, 93.36],
					[23.79, 94.6],
				]);
			} else if (id === 't4') {
				setActiveClasses(new Set<TemporalClassId>(['persistent', 't4_validated_persistent']));
				setLayers({ temporalRgb: true, seasonal: false, persistent: true, terrainQa: true });
				setSelectedId(2);
				setFocusBounds([
					[22.62, 93.36],
					[23.79, 94.6],
				]);
			}
		},
		[contract],
	);

	if (error) {
		return (
			<div className='flex min-h-screen items-center justify-center p-8'>
				<p className='text-sm text-loss'>Failed to load dashboard contract: {error}</p>
			</div>
		);
	}
	if (!contract || !regions) {
		return (
			<div className='flex min-h-screen items-center justify-center p-8'>
				<p className='text-sm text-muted'>Loading analysis contract…</p>
			</div>
		);
	}

	const m = contract.metrics;

	return (
		<div className='flex min-h-screen flex-col'>
			<header className='select-none border-b border-rule px-5 pb-4 pt-5 sm:px-8'>
				<div className='flex flex-wrap items-end justify-between gap-x-8 gap-y-3'>
					<div className='min-w-[min(100%,320px)] flex-1'>
						<h1 className='font-cond text-[32px] font-semibold leading-[1.05] tracking-[-0.01em] sm:text-[40px]'>
							RADARSAT-2 change candidates
						</h1>
						<p className='mt-1 text-[15px] text-body'>
							Myanmar · {contract.scene.beam} · {contract.scene.polarization} · {contract.scene.orbit} ·{' '}
							{contract.scene.resolution_m} m grid
						</p>
					</div>
					<div className='flex flex-wrap gap-x-6 gap-y-2 text-sm'>
						<div>
							<span className='block text-[11px] uppercase tracking-wide text-muted'>Candidates</span>
							<span className='text-lg font-semibold tabular-nums'>{m.total_candidates_analyzed}</span>
						</div>
						<div>
							<span className='block text-[11px] uppercase tracking-wide text-muted'>Seasonal</span>
							<span className='text-lg font-semibold tabular-nums text-[#22b8cf]'>
								{contract.counts.seasonal_transient}
							</span>
						</div>
						<div>
							<span className='block text-[11px] uppercase tracking-wide text-muted'>Persistent</span>
							<span className='text-lg font-semibold tabular-nums text-[#e14c8c]'>
								{contract.counts.persistent + contract.counts.t4_validated_persistent}
							</span>
						</div>
						<div>
							<span className='block text-[11px] uppercase tracking-wide text-muted'>T4-validated</span>
							<span className='text-lg font-semibold tabular-nums text-[#b0306a]'>
								{contract.counts.t4_validated_persistent}
							</span>
						</div>
					</div>
				</div>
				<p className='mt-2 text-xs text-muted'>
					Unvalidated radar-change candidates. Terrain, registration and surface moisture may explain the
					signal. No cause is asserted.
				</p>
			</header>

			<div className='flex flex-1 flex-wrap'>
				<main className='flex min-w-0 flex-[999_1_640px] flex-col gap-4 px-5 pb-8 pt-5 sm:px-8'>
					<MapView
						contract={contract}
						regions={regions}
						activeClasses={activeClasses}
						layers={layers}
						selectedId={selectedId}
						onSelect={setSelectedId}
						focusBounds={focusBounds}
					/>
					<Comparison contract={contract} />
				</main>

				<aside className='flex w-full flex-[1_1_320px] flex-col gap-5 border-t border-rule bg-panel px-5 pb-8 pt-5 lg:max-w-[400px] lg:border-l lg:border-t-0'>
					<Presenter contract={contract} stepIndex={stepIndex} onStep={applyStep} />
					<Controls
						contract={contract}
						activeClasses={activeClasses}
						onToggleClass={toggleClass}
						layers={layers}
						onToggleLayer={toggleLayer}
					/>
					<Inspector contract={contract} region={selected} />
					<Timeline contract={contract} />
				</aside>
			</div>
		</div>
	);
};

export { Root };
