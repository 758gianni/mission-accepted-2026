import type { Contract, TemporalClassId } from '../data/types';
import type { LayerKey } from './MapView';

interface Props {
	contract: Contract;
	activeClasses: Set<TemporalClassId>;
	onToggleClass: (id: TemporalClassId) => void;
	layers: Record<LayerKey, boolean>;
	onToggleLayer: (key: LayerKey) => void;
}

const LAYER_LABELS: Array<{ key: LayerKey; label: string }> = [
	{ key: 'temporalRgb', label: 'Temporal RGB' },
	{ key: 'seasonal', label: 'Seasonal / transient' },
	{ key: 'persistent', label: 'Persistent candidates' },
	{ key: 'terrainQa', label: 'Terrain QA' },
];

const Controls = ({ contract, activeClasses, onToggleClass, layers, onToggleLayer }: Props) => {
	const order: TemporalClassId[] = ['seasonal_transient', 'persistent', 't4_validated_persistent', 'late_unclassified'];
	return (
		<div className='flex flex-col gap-5'>
			<section>
				<h2 className='mb-2 text-sm font-semibold uppercase tracking-wide text-muted'>Temporal class</h2>
				<div className='flex flex-col gap-1.5'>
					{order.map((id) => {
						const meta = contract.temporal_classes[id];
						const count = contract.counts[id] ?? 0;
						const on = activeClasses.has(id);
						return (
							<button
								key={id}
								type='button'
								aria-pressed={on}
								onClick={() => onToggleClass(id)}
								className={`flex items-center gap-2.5 rounded border px-3 py-2 text-left text-sm transition-colors ${
									on ? 'border-ink bg-selected' : 'border-edge bg-panel hover:bg-selected/60'
								}`}
							>
								<span className='size-3 shrink-0 rounded-sm' style={{ background: meta.color, opacity: on ? 1 : 0.35 }} />
								<span className='flex-1 font-medium'>{meta.label}</span>
								<span className='tabular-nums text-muted'>{count}</span>
							</button>
						);
					})}
				</div>
			</section>

			<section>
				<h2 className='mb-2 text-sm font-semibold uppercase tracking-wide text-muted'>Layers</h2>
				<div className='flex flex-col gap-1.5'>
					{LAYER_LABELS.map(({ key, label }) => (
						<label key={key} className='flex cursor-pointer items-center gap-2.5 rounded border border-edge bg-panel px-3 py-2 text-sm hover:bg-selected/60'>
							<input type='checkbox' checked={layers[key]} onChange={() => onToggleLayer(key)} className='size-4' />
							<span>{label}</span>
						</label>
					))}
				</div>
			</section>

			<section className='rounded border border-rule bg-panel/60 p-3 text-xs leading-relaxed text-body'>
				<p className='mb-1 font-semibold text-ink'>Reading the classes</p>
				{order.map((id) => (
					<p key={id} className='mb-1'>
						<span className='font-medium' style={{ color: contract.temporal_classes[id].color }}>
							{contract.temporal_classes[id].label}.
						</span>{' '}
						{contract.temporal_classes[id].description}
					</p>
				))}
			</section>
		</div>
	);
};

export { Controls };
