import { Eye, EyeOff, Layers, LocateFixed, Mountain, Satellite, Waves } from 'lucide-react';
import { useMemo, type FC } from 'react';
import { ChangeMap, type MapView } from './components/change-map';
import { AcquisitionEvidence } from './components/sidebar';
import { filterCandidates, type CandidateFeature, type CandidateFilter, type DashboardContract } from './terra-data';

export type MapLayer = 'temporal_rgb' | 'seasonal_transient' | 'persistent_candidates' | 'terrain_qa_mask' | 'coverage';

interface AppProps {
	contract: DashboardContract;
	features: CandidateFeature[];
	filter: CandidateFilter;
	selectedId: string | number | null;
	layer: MapLayer;
	view: MapView;
	showFootprints: boolean;
	onFilter: (filter: CandidateFilter) => void;
	onSelect: (feature: CandidateFeature) => void;
	onLayer: (layer: MapLayer) => void;
	onView: (view: MapView) => void;
	onToggleFootprints: () => void;
	onReset: () => void;
}

const filterOptions: Array<{ id: CandidateFilter; label: string }> = [
	{ id: 'all', label: 'All signals' },
	{ id: 'seasonal_transient', label: 'Seasonal' },
	{ id: 'persistent', label: 'Persistent' },
	{ id: 'validated', label: 'Independent support' },
	{ id: 'late_unclassified', label: 'Late / new' },
	{ id: 'qa_cleared', label: 'QA screen pass' },
];

const layers: Array<{ id: MapLayer; label: string; icon: typeof Layers }> = [
	{ id: 'temporal_rgb', label: 'Temporal behaviour', icon: Satellite },
	{ id: 'seasonal_transient', label: 'Seasonal signal', icon: Waves },
	{ id: 'persistent_candidates', label: 'Persistent signal', icon: LocateFixed },
	{ id: 'coverage', label: 'Observation coverage', icon: Waves },
	{ id: 'terrain_qa_mask', label: 'Terrain QA', icon: Mountain },
];

const App: FC<AppProps> = ({ contract, features, filter, selectedId, layer, view, showFootprints, onFilter, onSelect, onLayer, onView, onToggleFootprints, onReset }) => {
	const visible = useMemo(() => filterCandidates(features, filter), [features, filter]);
	const availableFilters = filterOptions.filter((item) => item.id !== 'qa_cleared' || features.some((feature) => Boolean(feature.properties.screening)));
	const counts = Object.fromEntries(availableFilters.map((item) => [item.id, filterCandidates(features, item.id).length]));
	const selected = visible.find((feature) => String(feature.properties.id) === String(selectedId));

	return (
		<section className='ts-map-column' aria-label='Regional anomaly map'>
			<div className='ts-map-heading'>
				<div><span className='ts-eyebrow'>REGIONAL DISCOVERY</span><h2>Change intelligence map</h2></div>
				<div className='ts-map-heading-meta'><span className='ts-orbit-mark' />{contract.scene?.beam ?? 'SAR'} · {contract.scene?.polarization ?? 'multi-band'} · {contract.scene?.orbit ?? 'multi-orbit'}</div>
			</div>
			<nav className='ts-filter-rail' aria-label='Filter anomaly classes'>
				{availableFilters.map((item) => <button key={item.id} type='button' aria-pressed={filter === item.id} onClick={() => onFilter(item.id)}>
					<span>{item.label}</span><span className='ts-filter-count'>{counts[item.id] ?? 0}</span>
				</button>)}
			</nav>
			<div className='ts-map-frame'>
				<ChangeMap contract={contract} features={visible} selectedId={selectedId} layer={layer} view={view} showFootprints={showFootprints} onSelect={onSelect} onView={onView} />
				<div className='ts-map-tools'>
					<div className='ts-layer-menu' aria-label='Visualization layer'>
						<span className='ts-layer-title'><Layers size={13} /> LAYER</span>
						{layers.map(({ id, label, icon: Icon }) => <button key={id} type='button' title={label} aria-label={label} aria-pressed={layer === id} onClick={() => onLayer(id)}><Icon size={15} /><span>{label}</span></button>)}
					</div>
					<button className={`ts-map-toggle ${showFootprints ? 'is-on' : ''}`} type='button' aria-pressed={showFootprints} onClick={onToggleFootprints}>
						{showFootprints ? <Eye size={14} /> : <EyeOff size={14} />} Acquisition footprints
					</button>
				</div>
				<div className='ts-map-footer'>
					<div className='ts-map-legend'>{layer === 'coverage' ? <><span className='ts-legend-dot is-coverage' /> 1–2 dates <span className='ts-legend-dot is-coverage-strong' /> 3+ dates <span className='ts-legend-dot is-uncovered' /> No observation</> : <><span className='ts-legend-dot is-seasonal' /> Seasonal <span className='ts-legend-dot is-persistent' /> Persistent <span className='ts-legend-dot is-validated' /> Independently supported</>}</div>
					<div className='ts-map-footer-right'><span>{contract.tile_processing ? `${visible.length.toLocaleString()} detections shown · ${contract.tile_processing.tile_count ?? 0} tiles · ${contract.tile_processing.acquisition_count ?? 0} acquisitions · ${contract.tile_processing.acquisition_dates?.length ?? 0} dates` : `${visible.length.toLocaleString()} candidates`}{selected ? ` · Region ${selected.properties.id} focused` : ''}</span><a className='ts-map-attribution' href='https://www.openstreetmap.org/copyright' target='_blank' rel='noreferrer'>Natural Earth · © OpenStreetMap contributors</a><button type='button' onClick={onReset}><LocateFixed size={13} /> Fit study area</button></div>
				</div>
			</div>
			{selected && <div className='ts-map-evidence'><AcquisitionEvidence contract={contract} candidate={selected} /></div>}
		</section>
	);
};

export { App };
