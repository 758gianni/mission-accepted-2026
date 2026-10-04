import { ChevronLeft, ChevronRight, CircleDot, Pause, Play } from 'lucide-react';
import { useEffect, useId, useMemo, useState, type FC } from 'react';
import { geometryCenter, geometryPath, type Acquisition, type CandidateFeature, type DashboardContract } from '../terra-data';

type ImageStatus = 'loading' | 'ready' | 'failed' | 'missing';
const assetUrl = (value?: string | null) => value ? `/data/${value.replace(/^\//, '')}` : undefined;
const dateText = (value?: string) => value && Number.isFinite(Date.parse(value))
	? new Date(value).toLocaleDateString('en', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' }) : 'Date unavailable';
const timestamp = (observation: Acquisition) => {
	const preciseTime = Date.parse(observation.iso ?? '');
	return Number.isFinite(preciseTime) ? preciseTime : Date.parse(observation.date);
};

// Both the viewer and filmstrip use the existing common-grid crop. Quicklook
// headers are removed before projecting that grid; changing frames never pans it.
const EvidenceCrop: FC<{ contract: DashboardContract; observation: Acquisition; crop: string; path: string; outline: boolean; thumbnail?: boolean }> = ({ contract, observation, crop, path, outline, thumbnail }) => {
	const imageLayout = observation.image ? contract.imagery_layout?.[observation.image] : undefined;
	const cropId = useId();
	const [x, y, width, height] = crop.split(' ').map(Number);
	return <svg viewBox={crop} data-temporal-crop preserveAspectRatio='xMidYMid meet' role='img' aria-label={`${observation.sensor ?? 'Satellite'} ${observation.date} candidate-area crop`}>
		<defs><clipPath id={cropId}><rect x={x} y={y} width={width} height={height} /></clipPath></defs>
		<g clipPath={`url(#${cropId})`}>
		{imageLayout ? <svg x='0' y='0' width='1000' height='1060' viewBox={`${imageLayout.x} ${imageLayout.y} ${imageLayout.content_width} ${imageLayout.content_height}`} preserveAspectRatio='none' overflow='hidden'><image href={assetUrl(observation.image)} width={imageLayout.width} height={imageLayout.height} /></svg> : <image href={assetUrl(observation.image)} x='0' y='0' width='1000' height='1060' preserveAspectRatio='none' />}
		{outline && <path d={path} fill={thumbnail ? 'rgba(244,206,120,.2)' : 'none'} stroke='#f7d990' strokeWidth={thumbnail ? '1.5' : '2'} vectorEffect='non-scaling-stroke' />}
		</g>
	</svg>;
};

const AcquisitionEvidence: FC<{ contract: DashboardContract; candidate: CandidateFeature }> = ({ contract, candidate }) => {
	const bounds = contract.image_bounds ?? contract.scene?.footprint;
	const center = bounds ? geometryCenter(candidate.geometry, bounds, 1000, 1060) : [500, 530];
	const crop = `${center[0] - 115} ${center[1] - 82} 230 164`;
	const path = bounds ? geometryPath(candidate.geometry, bounds, 1000, 1060) : '';
	const observations = useMemo(() => [...(contract.acquisitions ?? [])].sort((a, b) =>
		(Number.isFinite(timestamp(a)) ? timestamp(a) : Infinity) - (Number.isFinite(timestamp(b)) ? timestamp(b) : Infinity)), [contract.acquisitions]);
	const supportingSources = (candidate.properties.t4_observation as { source_ids?: string[] } | null)?.source_ids ?? [];
	const isSupporting = (observation: Acquisition) => supportingSources.some((id) => observation.source_ids?.includes(id));
	const role = (observation: Acquisition) => isSupporting(observation) ? 'Independent support' : observation.role ?? 'Observation';
	const [index, setIndex] = useState(0);
	const [playing, setPlaying] = useState(false);
	const [delay, setDelay] = useState(2400);
	const [outline, setOutline] = useState(true);
	const [imageStatus, setImageStatus] = useState<Record<string, ImageStatus>>({});
	const current = observations[index];
	const currentStatus = current?.image ? imageStatus[current.image] ?? 'loading' : 'missing';
	const statusOf = (observation: Acquisition): ImageStatus => observation.image ? imageStatus[observation.image] ?? 'loading' : 'missing';
	const message = (status: ImageStatus) => status === 'failed' ? 'This acquisition image could not be loaded.' : status === 'missing' ? 'No imagery available for this acquisition.' : 'Loading acquisition image…';

	useEffect(() => {
		let active = true;
		const images = [...new Set(observations.map((observation) => observation.image).filter((asset): asset is string => Boolean(asset)))].map((asset) => {
			const image = new Image();
			image.onload = () => { if (active) setImageStatus((previous) => ({ ...previous, [asset]: 'ready' })); };
			image.onerror = () => { if (active) setImageStatus((previous) => ({ ...previous, [asset]: 'failed' })); };
			image.src = assetUrl(asset)!;
			return image;
		});
		return () => { active = false; images.forEach((image) => { image.onload = null; image.onerror = null; }); };
	}, [observations]);

	useEffect(() => {
		if (!playing || observations.length < 2 || currentStatus === 'loading') return;
		const timer = window.setTimeout(() => setIndex((previous) => (previous + 1) % observations.length), delay);
		return () => window.clearTimeout(timer);
		// Image readiness starts the dwell time, so a slow image cannot be skipped.
	}, [playing, index, delay, observations.length, currentStatus]);

	const select = (nextIndex: number) => { setPlaying(false); setIndex(Math.max(0, Math.min(observations.length - 1, nextIndex))); };

	return <section className='ts-evidence-section'>
		<div className='ts-section-kicker'><span>OBSERVATION SEQUENCE</span><span>{observations.length} observations · same ground crop</span></div>
		<section className='ts-temporal-viewer' aria-label='Temporal evidence viewer' tabIndex={0} onKeyDown={(event) => {
			if (event.target instanceof HTMLElement && ['INPUT', 'SELECT'].includes(event.target.tagName)) return;
			if (observations.length && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
				event.preventDefault(); select(index + (event.key === 'ArrowLeft' ? -1 : 1));
			}
		}}>
			{current ? <>
				<div className='ts-temporal-stage'>
					<div className='ts-temporal-image' aria-busy={statusOf(current) === 'loading'}>
						{observations.map((observation, frameIndex) => <div key={observation.id} className={`ts-temporal-frame ${index === frameIndex ? 'is-active' : ''}`} aria-hidden={index !== frameIndex} data-frame-status={statusOf(observation)}>
							{statusOf(observation) === 'ready' ? <EvidenceCrop contract={contract} observation={observation} crop={crop} path={path} outline={outline} /> : <div className='ts-temporal-unavailable'><CircleDot size={24} /><span>{message(statusOf(observation))}</span></div>}
						</div>)}
					</div>
					<div className='ts-temporal-caption' aria-live={playing ? 'off' : 'polite'}>
						<span className='ts-temporal-counter'>OBSERVATION {String(index + 1).padStart(2, '0')} / {String(observations.length).padStart(2, '0')}</span>
						<strong data-testid='temporal-date'>{dateText(current.date)}</strong>
						<span data-testid='temporal-sensor'>{current.sensor ?? 'EO source'}</span>
						<span className={`ts-temporal-role ${isSupporting(current) ? 'is-supporting' : ''}`} data-testid='temporal-role'>{role(current)}</span>
						<p>Follow the same ground through time. Radar appearance alone does not establish the cause of change.</p>
						<label className='ts-temporal-outline'><input type='checkbox' checked={outline} onChange={(event) => setOutline(event.target.checked)} />Candidate outline</label>
					</div>
				</div>
				<div className='ts-temporal-controls'>
					<button type='button' className={`ts-temporal-play ${playing ? 'is-playing' : ''}`} aria-label={playing ? 'Pause temporal sequence' : 'Play temporal sequence'} disabled={observations.length < 2} onClick={() => setPlaying((previous) => !previous)}>{playing ? <Pause size={16} /> : <Play size={16} />}{playing ? 'Pause' : 'Play sequence'}</button>
					<button type='button' aria-label='Previous acquisition' disabled={index === 0} onClick={() => select(index - 1)}><ChevronLeft size={16} /></button>
					<button type='button' aria-label='Next acquisition' disabled={index === observations.length - 1} onClick={() => select(index + 1)}><ChevronRight size={16} /></button>
					<label className='ts-temporal-speed'>Pace<select aria-label='Playback speed' value={delay} onChange={(event) => setDelay(Number(event.target.value))}><option value='3600'>Slow · 3.6s</option><option value='2400'>Normal · 2.4s</option><option value='1200'>Fast · 1.2s</option></select></label>
					<span className='ts-temporal-key-hint'>← → Step through dates</span>
				</div>
				<div className='ts-temporal-timeline'>
					<input type='range' aria-label='Acquisition timeline' aria-valuetext={`${dateText(current.date)} · ${current.sensor ?? 'EO source'} · ${role(current)}`} min='0' max={Math.max(0, observations.length - 1)} step='1' value={index} disabled={observations.length < 2} onChange={(event) => select(Number(event.target.value))} />
					<div className='ts-temporal-dates'>{observations.map((observation, frameIndex) => <button key={observation.id} type='button' className={index === frameIndex ? 'is-active' : ''} aria-label={`Scrub to acquisition ${dateText(observation.date)}`} aria-pressed={index === frameIndex} onClick={() => select(frameIndex)}><i /><span>{dateText(observation.date)}</span></button>)}</div>
				</div>
			</> : <div className='ts-temporal-unavailable'>No acquisition imagery has been supplied for this candidate.</div>}
		</section>
		<div className='ts-filmstrip'>
			{observations.map((observation, frameIndex) => <button type='button' className={`ts-film-card ${isSupporting(observation) ? 'is-supporting' : ''} ${index === frameIndex ? 'is-active' : ''}`} aria-label={`View acquisition ${dateText(observation.date)}`} aria-pressed={index === frameIndex} key={observation.id} onClick={() => select(frameIndex)}>
				<div className='ts-film-image'>
					{statusOf(observation) === 'ready' ? <EvidenceCrop contract={contract} observation={observation} crop={crop} path={path} outline={true} thumbnail /> : <div className='ts-film-unavailable'><CircleDot size={18} /><span>{statusOf(observation) === 'loading' ? 'Loading preview' : 'Preview unavailable'}</span></div>}
					<span className={`ts-film-role ${isSupporting(observation) ? 'is-validation' : ''}`}>{role(observation)}</span>
				</div>
				<div className='ts-film-meta'><strong>{dateText(observation.date)}</strong><span>{observation.sensor ?? 'EO source'}{observation.source_count && observation.source_count > 1 ? ` · ${observation.source_count} scenes` : ''}</span></div>
			</button>)}
		</div>
	</section>;
};

export { AcquisitionEvidence };
