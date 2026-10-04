import { ArrowLeft, ArrowUpRight, Check, ChevronRight, CircleDot, Mountain, ShieldAlert, Sparkles } from 'lucide-react';
import { useMemo, type FC } from 'react';
import { geometryCenter, geometryPath, rankCandidates, temporalClassOf, type CandidateFeature, type CandidateFilter, type DashboardContract, type GeoBounds } from '../terra-data';

interface SidebarProps {
	contract: DashboardContract;
	features: CandidateFeature[];
	filter: CandidateFilter;
	selectedId: string | number | null;
	onSelect: (feature: CandidateFeature) => void;
	onClear: () => void;
}

const assetUrl = (value?: string | null) => value ? `/data/${value.replace(/^\//, '')}` : undefined;
const dateText = (value?: string) => value ? new Date(value).toLocaleDateString('en', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' }) : '—';
const number = (value: unknown, digits = 1) => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('en', { maximumFractionDigits: digits }) : '—';

const validationStatus = (status: unknown) => {
	switch (status) {
		case 'confirmed': return { label: 'Independent observation · stronger support', tone: 'supported' };
		case 'moderate_support': return { label: 'Independent observation · moderate support', tone: 'moderate' };
		case 'outside_coverage': return { label: 'Independent coverage unavailable', tone: 'muted' };
		case 'weakened': return { label: 'Independent signal weakened', tone: 'review' };
		default: return { label: 'No independent observation', tone: 'muted' };
	}
};

const temporalLabel = (candidate: CandidateFeature) => {
	const temporalClass = temporalClassOf(candidate.properties);
	if (temporalClass === 'seasonal_transient') return 'Seasonal / transient';
	if (temporalClass === 'persistent') return 'Persistent surface-change anomaly';
	if (temporalClass === 't4_validated_persistent') return 'Persistent surface-change anomaly';
	if (temporalClass === 'late_unclassified') return 'Late change · persistence unknown';
	return 'Unclassified radar anomaly';
};

const AcquisitionEvidence: FC<{ contract: DashboardContract; candidate: CandidateFeature }> = ({ contract, candidate }) => {
	const bounds = (contract.image_bounds ?? contract.scene?.footprint) as GeoBounds | undefined;
	const center = bounds ? geometryCenter(candidate.geometry, bounds, 1000, 1060) : [500, 530] as [number, number];
	const crop = `${center[0] - 115} ${center[1] - 82} 230 164`;
	const path = bounds ? geometryPath(candidate.geometry, bounds, 1000, 1060) : '';
	const observations = contract.acquisitions ?? [];
	const supportingSources = (candidate.properties.t4_observation as { source_ids?: string[] } | null)?.source_ids ?? [];

	return <section className='ts-evidence-section'>
		<div className='ts-section-kicker'><span>OBSERVATION SEQUENCE</span><span>{observations.length} dates · same ground crop</span></div>
		<div className='ts-filmstrip'>
			{observations.map((observation) => {
				const image = assetUrl(observation.image);
				const imageLayout = observation.image ? contract.imagery_layout?.[observation.image] : undefined;
				const isValidation = supportingSources.some((id) => observation.source_ids?.includes(id));
				return <article className={`ts-film-card ${isValidation ? 'is-supporting' : ''}`} tabIndex={0} key={observation.id}>
					<div className='ts-film-image'>
						{image ? <svg viewBox={crop} preserveAspectRatio='xMidYMid slice' role='img' aria-label={`${observation.sensor ?? 'Satellite'} ${observation.date} candidate-area crop`}>
							{imageLayout ? <svg x='0' y='0' width='1000' height='1060' viewBox={`${imageLayout.x} ${imageLayout.y} ${imageLayout.content_width} ${imageLayout.content_height}`} preserveAspectRatio='none' overflow='hidden'><image href={image} width={imageLayout.width} height={imageLayout.height} /></svg> : <image href={image} x='0' y='0' width='1000' height='1060' preserveAspectRatio='none' />}
							<path d={path} fill='rgba(244,206,120,.32)' stroke='#f7d990' strokeWidth='2.5' vectorEffect='non-scaling-stroke' />
						</svg> : <div className='ts-film-unavailable'><CircleDot size={18} /><span>No preview asset</span></div>}
				<span className={`ts-film-role ${isValidation ? 'is-validation' : ''}`}>{isValidation ? 'INDEPENDENT SUPPORT' : observation.role ?? 'OBSERVATION'}</span>
					</div>
					<div className='ts-film-meta'><strong>{dateText(observation.date)}</strong><span>{observation.sensor ?? 'EO source'}{observation.source_count && observation.source_count > 1 ? ` · ${observation.source_count} scenes` : ''}</span></div>
				</article>;
			})}
		</div>
	</section>;
};

const Sidebar: FC<SidebarProps> = ({ contract, features, filter, selectedId, onSelect, onClear }) => {
	const selected = features.find((candidate) => String(candidate.properties.id) === String(selectedId));
	const ranked = useMemo(() => rankCandidates(features, filter, 9), [features, filter]);
	const rank = (feature: CandidateFeature) => ranked.findIndex((candidate) => String(candidate.properties.id) === String(feature.properties.id)) + 1;

	if (!selected) return (
		<aside className='ts-side-panel ts-queue-panel' aria-label='Ranked anomaly queue'>
			<div className='ts-side-heading'><div><span className='ts-eyebrow'>PRIORITIZED FOR REVIEW</span><h2>Investigation queue</h2></div><span className='ts-queue-count'>{features.length}</span></div>
			<p className='ts-side-intro'>Ranked radar-change candidates. Persistence and visual salience are evidence to inspect, not proof of cause.</p>
			<div className='ts-queue-list'>
				{ranked.map((feature) => {
					const props = feature.properties;
					const status = validationStatus(props.t4_status);
					return <button key={String(props.id)} type='button' className='ts-queue-item' onClick={() => onSelect(feature)}>
						<span className='ts-queue-rank'>{String(rank(feature)).padStart(2, '0')}</span>
						<span className='ts-queue-body'><span className='ts-queue-title'>Region {props.id}<span className={`ts-class-dot is-${temporalClassOf(props)}`} /></span><span className='ts-queue-sub'>{number(props.area_ha, 0)} ha <i>·</i> {contract.candidate_notes?.[String(props.id)]?.hypothesis ?? temporalLabel(feature)}</span><span className={`ts-mini-status is-${status.tone}`}>{props.t4_status === 'confirmed' || props.t4_status === 'moderate_support' ? <Check size={11} /> : <CircleDot size={10} />}{status.label}</span></span>
						<span className='ts-queue-score'>{number(props.priority_score, 1)}<small>PRIORITY</small></span>
						<ChevronRight size={15} className='ts-queue-chevron' />
					</button>;
				})}
				{ranked.length === 0 && <div className='ts-empty-queue'><Sparkles size={19} /><span>No candidates match this filter.</span></div>}
			</div>
			<div className='ts-queue-foot'><span>Sorted by candidate priority</span><span>NOT A CONFIDENCE SCORE</span></div>
		</aside>
	);

	const props = selected.properties;
	const support = validationStatus(props.t4_status);
	const trajectory = props.temporal_trajectory as Array<{ date?: string }> | undefined;
	const t4SourceIds = (props.t4_observation as { source_ids?: string[] } | null)?.source_ids ?? [];
	const lastEvidence = t4SourceIds.length
		? contract.acquisitions?.find((acquisition) => acquisition.source_ids?.some((id) => t4SourceIds.includes(id)))?.date
		: trajectory?.at(-1)?.date ?? props.observation_interval?.at(-1);
	const firstObserved = props.first_observed ?? props.observation_interval?.[1];

	return <aside className='ts-side-panel ts-investigation-panel' aria-label={`Investigation for region ${props.id}`}>
		<div className='ts-investigation-top'><button type='button' className='ts-back-button' onClick={onClear}><ArrowLeft size={14} /> Back to queue</button><span className='ts-investigation-index'>RANK {String(rank(selected) || '—').padStart(2, '0')}</span></div>
		<div className='ts-candidate-title'><div className='ts-candidate-id'>REGION {props.id}<span className={`ts-class-dot is-${temporalClassOf(props)}`} /></div><h2>{temporalLabel(selected)}</h2><span className={`ts-status-badge is-${support.tone}`}>{support.tone === 'supported' || support.tone === 'moderate' ? <Check size={13} /> : <CircleDot size={12} />}{support.label}</span></div>
		{contract.candidate_notes?.[String(props.id)] && <div className='ts-investigation-hypothesis'><span>INVESTIGATION HYPOTHESIS · UNVERIFIED</span><strong>{contract.candidate_notes[String(props.id)].hypothesis}</strong><p>{contract.candidate_notes[String(props.id)].detail}</p><small>{contract.candidate_notes[String(props.id)].attribution}</small></div>}
		<div className='ts-candidate-lead'>
			<div><strong>{number(props.area_ha, 1)}<small> ha</small></strong><span>Candidate area</span></div>
			<div><strong className={typeof props.mean_signed_db === 'number' && props.mean_signed_db < 0 ? 'is-negative' : 'is-positive'}>{typeof props.mean_signed_db === 'number' ? `${props.mean_signed_db > 0 ? '+' : ''}${number(props.mean_signed_db, 1)}` : `±${number(props.mean_change_db, 1)}`}<small> dB</small></strong><span>Mean radar change</span></div>
		</div>
		<div className='ts-detail-grid'>
			<div className='ts-detail-cell'><span>Persistence</span><strong>{props.persists_into_T3 === true ? 'Held in later dry season' : props.persists_into_T3 === false ? 'Returned toward baseline' : temporalClassOf(props) === 'late_unclassified' ? 'Not yet established' : 'Persistent signal'}</strong></div>
			<div className='ts-detail-cell'><span>Terrain risk</span><strong><Mountain size={13} /> {props.terrain_risk_pct == null ? 'Unknown' : `${number(props.terrain_risk_pct, 1)}%`}<small>{typeof props.terrain_tier === 'string' ? props.terrain_tier : ''}</small></strong></div>
			<div className='ts-detail-cell'><span>Priority score</span><strong>{number(props.priority_score, 1)}<small>heuristic · not confidence</small></strong></div>
			<div className='ts-detail-cell'><span>Observation interval</span><strong>{dateText(firstObserved)} <ArrowUpRight size={12} /> {dateText(lastEvidence)}</strong></div>
		</div>
		<div className='ts-source-evidence'>
			<div className='ts-section-kicker'><span>SOURCE EVIDENCE</span><span>{contract.scene?.product ?? 'Earth observation'}</span></div>
			<div className='ts-source-row'><span>Primary discovery</span><strong>{contract.scene?.product ?? 'RADARSAT-2 SLC'} · {contract.scene?.polarization ?? 'SAR'}</strong></div>
			{Array.isArray(props.source_record_ids) && <div className='ts-source-row'><span>Linked source records</span><strong>{props.source_record_ids.join(' · ')}</strong></div>}
			{props.t4_mean_signed_db != null && <div className='ts-source-row'><span>Independent observation change</span><strong>{typeof props.t4_mean_signed_db === 'number' ? `${props.t4_mean_signed_db > 0 ? '+' : ''}${number(props.t4_mean_signed_db, 2)} dB` : 'Observed'}</strong></div>}
			{props.t4_status === 'outside_coverage' && <p className='ts-coverage-note'><ShieldAlert size={13} /> This candidate is outside the independent observation footprint.</p>}
		</div>
		<div className='ts-science-note'><ShieldAlert size={13} /><span>Radar change is not a land-cover label. Terrain, moisture, residual alignment, and acquisition coverage can affect this signal.</span></div>
	</aside>;
};

export { Sidebar, AcquisitionEvidence };
