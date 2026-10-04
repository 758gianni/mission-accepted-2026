import { ArrowLeft, ArrowRight, AlertTriangle, Database, LoaderCircle, MapPinned, Satellite, ScanLine } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { App, type MapLayer } from './app';
import { FloatingChat } from './components/floating-chat';
import { openRouterAssistant, queryCandidates, queryLabel, type AssistantAction, type CandidateQuery } from './assistant';
import { Header } from './components/header';
import { ReductionFunnel } from './components/reduction-funnel';
import { Sidebar } from './components/sidebar';
import { MAP_VIEW_HEIGHT, MAP_VIEW_WIDTH } from './components/change-map';
import { filterCandidates, funnelStages, geometryCenter, geometryExtent, projectMapCoordinate, rankCandidates, type CandidateFeature, type CandidateFilter, type DashboardContract, type GeoBounds } from './terra-data';

interface FeatureCollection { type: 'FeatureCollection'; features: CandidateFeature[] }
interface ProductData { contract: DashboardContract; features: CandidateFeature[] }
interface StoryStep { id: string; title: string; detail: string; filter: CandidateFilter; layer: MapLayer; candidateId?: string | number }

const bytesText = (value?: number) => {
	if (!value) return '—';
	const units = ['B', 'KB', 'MB', 'GB', 'TB'];
	const unit = Math.min(Math.floor(Math.log(value) / Math.log(1000)), units.length - 1);
	return `${(value / 1000 ** unit).toLocaleString('en', { maximumFractionDigits: unit ? 1 : 0 })} ${units[unit]}`;
};
const km2Text = (value?: number | null) => typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value).toLocaleString()} km²` : 'Not in run manifest';
const overviewView = { zoom: 1, x: MAP_VIEW_WIDTH / 2, y: MAP_VIEW_HEIGHT / 2 };
interface ExplorationState { view: typeof overviewView; filter: CandidateFilter; layer: MapLayer; showFootprints: boolean; query: CandidateQuery | null }
interface AssistantUndo extends ExplorationState { selectedId: string | number | null; exploration: ExplorationState | null }

const Root = () => {
	const [data, setData] = useState<ProductData | null>(null);
	const [loadError, setLoadError] = useState('');
	const [filter, setFilter] = useState<CandidateFilter>('all');
	const [selectedId, setSelectedId] = useState<string | number | null>(null);
	const [layer, setLayer] = useState<MapLayer>('temporal_rgb');
	const [showFootprints, setShowFootprints] = useState(true);
	const [view, setView] = useState(overviewView);
	const [presentationIndex, setPresentationIndex] = useState<number | null>(null);
	const exploration = useRef<ExplorationState | null>(null);
	const [assistantQuery, setAssistantQuery] = useState<CandidateQuery | null>(null);
	const [assistantUndo, setAssistantUndo] = useState<AssistantUndo | null>(null);

	useEffect(() => {
		let active = true;
		Promise.all([
			fetch('/data/derived/contract.json').then((response) => { if (!response.ok) throw new Error('Could not load the generated TerraSignal contract.'); return response.json() as Promise<DashboardContract>; }),
			fetch('/data/derived/candidate_regions.geojson').then((response) => { if (!response.ok) throw new Error('Could not load the generated candidate catalogue.'); return response.json() as Promise<FeatureCollection>; }),
		]).then(([contract, catalogue]) => {
			if (active) setData({ contract, features: catalogue.features });
		}).catch((error: unknown) => { if (active) setLoadError(error instanceof Error ? error.message : 'TerraSignal data could not be loaded.'); });
		return () => { active = false; };
	}, []);

	const allFeatures = useMemo(() => data?.features ?? [], [data]);
	const queryFeatures = useMemo(() => assistantQuery ? queryCandidates(allFeatures, assistantQuery) : allFeatures, [allFeatures, assistantQuery]);
	const topCandidate = useMemo(() => rankCandidates(allFeatures, 'persistent', 1)[0] ?? rankCandidates(allFeatures, 'all', 1)[0], [allFeatures]);
	const stages = useMemo(() => data ? funnelStages(data.contract) : [], [data]);
	const storySteps = useMemo<StoryStep[]>(() => {
		if (!data) return [];
		const count = (name: string) => data.contract.counts[name] ?? 0;
		const observations = data.contract.tile_processing?.acquisition_count ?? data.contract.acquisitions?.length ?? 0;
		const dates = data.contract.tile_processing?.acquisition_dates?.length ?? data.contract.acquisitions?.length ?? 0;
		return [
			{ id: 'scale', title: 'Scale', detail: `${observations} source acquisitions across ${dates} dates · ${data.contract.scene?.name ?? 'the analyzed area'}`, filter: 'all', layer: 'temporal_rgb' },
			{ id: 'detection', title: 'Detection', detail: `${data.contract.metrics?.total_candidates_analyzed ?? allFeatures.length} spatial candidates from the RADARSAT-2 discovery set`, filter: 'all', layer: 'temporal_rgb' },
			{ id: 'seasonal', title: 'Seasonal', detail: `${count('seasonal_transient')} seasonal or transient radar signals`, filter: 'seasonal_transient', layer: 'seasonal_transient' },
			{ id: 'persistent', title: 'Persistent', detail: `${count('persistent') + count('t4_validated_persistent')} candidates persist beyond the first change observation`, filter: 'persistent', layer: 'persistent_candidates' },
			{ id: 'validated', title: 'Independently supported', detail: `${count('t4_validated_persistent')} persistent candidates have independent later-observation support`, filter: 'validated', layer: 'persistent_candidates' },
			{ id: 'investigate', title: 'Investigate', detail: topCandidate ? `Open the highest-ranked supported candidate, Region ${topCandidate.properties.id}` : 'Open the highest-ranked available candidate', filter: 'persistent', layer: 'persistent_candidates', candidateId: topCandidate?.properties.id },
			{ id: 'evidence', title: 'Evidence', detail: 'Review the acquisition sequence, terrain context, and coverage caveats', filter: 'persistent', layer: 'persistent_candidates', candidateId: topCandidate?.properties.id },
		];
	}, [data, allFeatures.length, topCandidate]);

	const focusCandidate = useCallback((feature: CandidateFeature | undefined) => {
		if (!feature || !data) return;
		const bounds = (data.contract.map_bounds ?? data.contract.image_bounds ?? data.contract.scene?.footprint) as GeoBounds | undefined;
		if (!bounds) return;
		const [x, y] = geometryCenter(feature.geometry, bounds, MAP_VIEW_WIDTH, MAP_VIEW_HEIGHT, projectMapCoordinate);
		setSelectedId(feature.properties.id);
		const [left, top, right, bottom] = geometryExtent(feature.geometry, bounds, MAP_VIEW_WIDTH, MAP_VIEW_HEIGHT, projectMapCoordinate);
		const focusZoom = Math.max(4.1, Math.min(96, MAP_VIEW_WIDTH / Math.max(16, (right - left) * 10), MAP_VIEW_HEIGHT / Math.max(16, (bottom - top) * 10)));
		setView({ zoom: focusZoom, x, y });
	}, [data]);

	const applyStep = useCallback((index: number) => {
		if (!storySteps.length) return;
		const nextIndex = Math.max(0, Math.min(storySteps.length - 1, index));
		const step = storySteps[nextIndex];
		setAssistantQuery(null);
		setPresentationIndex(nextIndex);
		setFilter(step.filter);
		setLayer(step.layer);
		setShowFootprints(step.id === 'scale' || step.id === 'detection' || step.id === 'evidence');
		if (step.candidateId != null) focusCandidate(allFeatures.find((feature) => String(feature.properties.id) === String(step.candidateId)));
		else {
			setSelectedId(null);
			setView(overviewView);
		}
	}, [storySteps, allFeatures, focusCandidate]);

	const updateFilter = (nextFilter: CandidateFilter) => {
		setAssistantQuery(null);
		setPresentationIndex(null);
		setFilter(nextFilter);
		if (selectedId != null && data && !filterCandidates(data.features, nextFilter).some((feature) => String(feature.properties.id) === String(selectedId))) setSelectedId(null);
	};
	const selectCandidate = (feature: CandidateFeature) => {
		if (selectedId == null) exploration.current = { view, filter, layer, showFootprints, query: assistantQuery };
		setPresentationIndex(null);
		focusCandidate(feature);
	};
	const togglePresentation = () => {
		if (presentationIndex != null) setPresentationIndex(null);
		else { exploration.current = null; applyStep(0); }
	};
	const returnToOverview = () => {
		exploration.current = null;
		setAssistantQuery(null);
		setPresentationIndex(null);
		setSelectedId(null);
		setFilter('all');
		setView(overviewView);
	};

	const closeInvestigation = () => {
		const previous = exploration.current;
		if (!previous) { returnToOverview(); return; }
		setPresentationIndex(null);
		setSelectedId(null);
		setAssistantQuery(previous.query);
		setFilter(previous.filter);
		setLayer(previous.layer);
		setShowFootprints(previous.showFootprints);
		setView(previous.view);
		exploration.current = null;
	};

	const applyAssistantAction = (action: AssistantAction) => {
		const candidate = action.type === 'select' ? allFeatures.find((feature) => String(feature.properties.id) === action.id) : undefined;
		if (action.type === 'select' && !candidate) return;
		if (action.type === 'query' && (!['all', 'persistent', 'validated', 'seasonal_transient', 'late_unclassified', 'qa_cleared'].includes(action.query.filter) || [action.query.minAreaHa, action.query.maxAreaHa].some((value) => value != null && (!Number.isFinite(value) || value < 0)))) return;
		if (action.type !== 'query' && action.type !== 'select') return;
		setAssistantUndo({ view, filter, layer, showFootprints, query: assistantQuery, selectedId, exploration: exploration.current });
		setPresentationIndex(null);
		if (candidate) {
			selectCandidate(candidate);
			setAssistantQuery(null);
			setFilter('all');
		} else if (action.type === 'query') {
			exploration.current = null;
			setAssistantQuery(action.query);
			setFilter(action.query.filter);
			setSelectedId(null);
			setView(overviewView);
		}
	};
	const undoAssistantAction = () => {
		if (!assistantUndo) return;
		setView(assistantUndo.view); setFilter(assistantUndo.filter); setLayer(assistantUndo.layer);
		setShowFootprints(assistantUndo.showFootprints); setSelectedId(assistantUndo.selectedId); setAssistantQuery(assistantUndo.query);
		exploration.current = assistantUndo.exploration;
		setAssistantUndo(null);
	};

	if (loadError) return <main className='ts-load-state is-error'><AlertTriangle size={23} /><h1>TerraSignal data unavailable</h1><p>{loadError}</p><span>Expected generated files under /data/derived/.</span></main>;
	if (!data) return <main className='ts-load-state'><LoaderCircle size={23} className='ts-loading-icon' /><h1>TerraSignal</h1><p>Loading compact anomaly catalogue…</p></main>;

	const { contract } = data;
	const processing = contract.processing ?? {};
	const rawBytes = typeof processing.raw_bytes === 'number' ? processing.raw_bytes : undefined;
	const derivedBytes = typeof processing.total_derived_bytes === 'number' ? processing.total_derived_bytes : undefined;
	const reduction = typeof processing.raw_to_all_derived_ratio === 'number' ? processing.raw_to_all_derived_ratio : undefined;
	const tileProcessing = contract.tile_processing;
	const baselineArea = contract.baseline_comparability?.common_valid_grid_area_km2;
	const analysisArea = tileProcessing?.analysis_valid_area_km2 ?? baselineArea;
	const activeStep = presentationIndex == null ? null : storySteps[presentationIndex];
	const pairQa = contract.registration_qa?.pair_checks;
	const candidateQa = contract.registration_qa?.candidate_subset;
	const currentFilters = filterCandidates(queryFeatures, filter);

	return <div className='ts-app'>
		<Header contract={contract} presentationActive={presentationIndex != null} onTogglePresentation={togglePresentation} />
		<main className='ts-main'>
			<ReductionFunnel stages={stages} activeFilter={filter} onSelect={updateFilter} />
			<section className='ts-overview-bar' aria-label='AOI, coverage, and storage reduction metrics'>
				<div className='ts-overview-block ts-area-overview'><ScanLine size={17} /><div><span>Analysis-valid area</span><strong data-metric-value>{km2Text(analysisArea)}</strong></div></div>
				<div className='ts-overview-block'><Satellite size={16} /><div><span>Satellite coverage</span><strong data-metric-value>{km2Text(tileProcessing?.satellite_coverage_area_km2)}</strong></div></div>
				{tileProcessing?.requested_aoi_geometry_available && typeof tileProcessing.requested_aoi_area_km2 === 'number' && <div className='ts-overview-block ts-aoi-overview'><MapPinned size={16} /><div><span>Requested AOI</span><strong data-metric-value>{km2Text(tileProcessing.requested_aoi_area_km2)}</strong></div></div>}
				<div className='ts-overview-block ts-tile-overview'><Database size={16} /><div><span>Observations</span><strong>{tileProcessing ? `${tileProcessing.acquisition_count ?? 0} acquisitions · ${tileProcessing.acquisition_dates?.length ?? 0} dates` : `${contract.acquisitions?.length ?? 0} dates`}</strong>{tileProcessing && <small>{tileProcessing.tile_count ?? 0} geographic tiles</small>}</div></div>
				<div className='ts-storage-flow' data-testid='data-reduction'><span className='ts-storage-label'>Data reduction</span><div className='ts-storage-values'><strong data-metric-value>{bytesText(rawBytes)}</strong><ArrowRightIcon /><strong data-metric-value className='ts-derived-size'>{bytesText(derivedBytes)}</strong></div>
					<div className='ts-storage-result'><span data-metric-value className='ts-reduction-pill'>{reduction ? `${reduction.toFixed(1)}× smaller` : '—'}</span><small>{typeof processing.source_scene_count === 'number' ? `${processing.source_scene_count} source scenes · raw archive to all derived products` : 'Raw archive → analysis-ready + temporal + candidates'}</small></div>
				</div>
			</section>
			<div className='ts-primary-layout'>
				<App contract={contract} features={queryFeatures} assistantQueryLabel={assistantQuery ? `${queryLabel(assistantQuery)} · ${queryFeatures.length} matches` : undefined} onClearAssistantQuery={() => setAssistantQuery(null)} filter={filter} selectedId={selectedId} layer={layer} view={view} showFootprints={showFootprints}
					onFilter={updateFilter} onSelect={selectCandidate} onLayer={(next) => { setPresentationIndex(null); setLayer(next); }} onView={setView}
					onToggleFootprints={() => setShowFootprints((value) => !value)} onReset={returnToOverview} />
				<Sidebar contract={contract} features={currentFilters} filter={filter} selectedId={selectedId} onSelect={selectCandidate} onClear={closeInvestigation} />
			</div>
			{pairQa && <details className='ts-qa-status'><summary><AlertTriangle size={14} /><strong>QA status</strong><span>Registration evidence is partial</span><span className='ts-qa-expand'>Details & limitations</span></summary><div className='ts-qa-ribbon' aria-label='Registration quality caveat'>
				<div className='ts-qa-icon'><AlertTriangle size={15} /></div>
				<div className='ts-qa-copy'><strong>Registration evidence is bounded</strong><span>{pairQa.passing}/{pairQa.comparisons} tile/date comparisons pass local residual checks · {pairQa.insufficient} have insufficient evidence · {pairQa.review_required} require measured review.</span></div>
				{candidateQa && <div className='ts-qa-subset'><span>Separate candidate QA subset</span><strong>{candidateQa.registration_passing}/{candidateQa.screened} registration-supported <i>·</i> {candidateQa.initial_screen_passing} pass combined initial screen</strong></div>}
				<span className='ts-qa-footnote'>{contract.registration_qa?.interpretation}</span>
			</div>
			<footer className='ts-data-footer'><span>DISCOVERY SOURCE <strong>{contract.scene?.product ?? 'RADARSAT-2'} · primary</strong></span><span>SUPPORTING CONTEXT <strong>terrain QA · independent acquisitions</strong></span><span>DETECTIONS ARE RADAR CHANGE CANDIDATES, NOT LAND-COVER CONFIRMATIONS</span></footer></details>}
		</main>
		<FloatingChat context={{ contract, features: allFeatures, selectedId }} provider={openRouterAssistant} onAction={applyAssistantAction} onUndo={undoAssistantAction} canUndo={assistantUndo != null} presentationActive={presentationIndex != null} />
		{activeStep && <nav className='ts-presentation-dock' aria-label='Guided presentation steps'>
			<div className='ts-presentation-progress'><span style={{ width: `${((presentationIndex! + 1) / storySteps.length) * 100}%` }} /></div>
			<button type='button' aria-label='Previous presentation step' disabled={presentationIndex === 0} onClick={() => applyStep(presentationIndex! - 1)}><ArrowLeft size={16} /></button>
			<div className='ts-presentation-copy'><span>GUIDED PRESENTATION · {presentationIndex! + 1} / {storySteps.length}</span><strong>{activeStep.title}</strong><small>{activeStep.detail}</small></div>
			<div className='ts-presentation-steps'>{storySteps.map((step, index) => <button key={step.id} type='button' title={step.title} aria-label={`Go to ${step.title}`} aria-current={presentationIndex === index ? 'step' : undefined} onClick={() => applyStep(index)}><span>{String(index + 1).padStart(2, '0')}</span></button>)}</div>
			<button type='button' aria-label='Next presentation step' disabled={presentationIndex === storySteps.length - 1} onClick={() => applyStep(presentationIndex! + 1)}><ArrowRight size={16} /></button>
		</nav>}
	</div>;
};

const ArrowRightIcon = () => <span className='ts-storage-arrow' aria-hidden='true'>→</span>;

export { Root };
