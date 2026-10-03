import type { Contract, RegionProperties, TemporalClassId } from '../data/types';
import { asset, formatDb, formatHa, formatScore } from '../data/load';

interface Props {
	contract: Contract;
	region: RegionProperties | null;
}

const Row = ({ label, value, accent }: { label: string; value: string; accent?: string }) => (
	<div className='flex items-baseline justify-between gap-3 border-b border-rule/70 py-1.5 last:border-b-0'>
		<span className='text-xs uppercase tracking-wide text-muted'>{label}</span>
		<span className='text-right text-sm font-semibold tabular-nums' style={accent ? { color: accent } : undefined}>
			{value}
		</span>
	</div>
);

const Inspector = ({ contract, region }: Props) => {
	if (!region) {
		return (
			<div className='rounded border border-rule bg-panel/60 p-5 text-sm text-muted'>
				Select a region on the map to inspect area, dB change, terrain risk, temporal class and priority score.
			</div>
		);
	}

	const meta = contract.temporal_classes[region.temporal_class as TemporalClassId];
	const cropSrc = region.id === 2 ? asset('data/imagery/top_candidate_T2.png') : null;

	return (
		<div className='flex flex-col gap-4 rounded border border-rule bg-panel p-5'>
			<header className='flex items-start justify-between gap-3'>
				<div>
					<p className='text-xs uppercase tracking-wide text-muted'>Region {region.id}</p>
					<h2 className='text-xl font-semibold leading-tight'>{meta.label}</h2>
					<p className='text-xs text-muted'>
						{region.direction === 'decrease' ? 'Backscatter decrease' : 'Backscatter increase'} · {region.label}
					</p>
				</div>
				<span className='rounded px-2 py-1 text-xs font-semibold text-panel' style={{ background: meta.color }}>
					{formatScore(region.priority_score)}
				</span>
			</header>

			{cropSrc && (
				<figure className='overflow-hidden rounded border border-rule'>
					<img src={cropSrc} alt={`Region ${region.id} T2 crop`} className='block w-full' />
					<figcaption className='border-t border-rule bg-panel/70 px-2 py-1 text-[11px] text-muted'>
						Real SAR crop · T2 (2024-11-20) · not interpreted as a land-cover class
					</figcaption>
				</figure>
			)}

			<div>
				<Row label='Area' value={formatHa(region.area_ha)} />
				<Row label='Pixels (60 m)' value={String(region.pixel_count)} />
				<Row label='Mean signed change' value={formatDb(region.mean_signed_db)} accent={region.mean_signed_db < 0 ? '#b0306a' : '#163f52'} />
				<Row label='Max change' value={formatDb(region.max_change_db)} />
				<Row label='Terrain risk' value={`${region.terrain_risk_pct.toFixed(1)}%`} />
				<Row label='Mean slope' value={`${region.mean_slope_deg.toFixed(2)}°`} />
				<Row label='Mean elevation' value={`${region.mean_elevation_m.toFixed(0)} m`} />
				<Row label='Terrain tier' value={region.terrain_tier} />
				<Row label='Temporal class' value={meta.label} accent={meta.color} />
				<Row label='Priority score' value={formatScore(region.priority_score)} />
				<Row label='First observed' value={region.first_observed.slice(0, 10)} />
				<Row label='T4 status' value={region.t4_status} />
			</div>

			<section>
				<h3 className='mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted'>Power by acquisition</h3>
				<div className='flex items-end gap-2'>
					{region.mean_power_by_date.map((value, i) => {
						const max = Math.max(...region.mean_power_by_date, 1e-6);
						const h = Math.max(6, (value / max) * 72);
						return (
							<div key={i} className='flex flex-1 flex-col items-center gap-1'>
								<span className='text-[10px] tabular-nums text-muted'>{value.toFixed(3)}</span>
								<div className='w-full rounded-t bg-radar' style={{ height: h }} />
								<span className='text-[10px] font-semibold text-muted'>T{i + 1}</span>
							</div>
						);
					})}
					{contract.t4.available && (
						<div className='flex flex-1 flex-col items-center gap-1 opacity-45'>
							<span className='text-[10px] tabular-nums text-muted'>{region.t4_mean_power?.toFixed(3) ?? '—'}</span>
							<div className='w-full rounded-t border border-dashed border-edge bg-transparent' style={{ height: 40 }} />
							<span className='text-[10px] font-semibold text-muted'>T4</span>
						</div>
					)}
				</div>
			</section>

			<p className='text-xs leading-relaxed text-body'>{region.interpretation}</p>
		</div>
	);
};

export { Inspector };
