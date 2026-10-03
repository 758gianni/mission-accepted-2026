import type { Contract } from '../data/types';
import { asset } from '../data/load';

interface Props {
	contract: Contract;
}

const Comparison = ({ contract }: Props) => {
	return (
		<section aria-label='Acquisition comparison'>
			<h2 className='mb-2 text-sm font-semibold uppercase tracking-wide text-muted'>
				T1 / T2 / T3 / T4 comparison
			</h2>
			<div className='grid grid-cols-2 gap-2 sm:grid-cols-4'>
				{contract.comparisons.map((slot) => (
					<figure key={slot.id} className='overflow-hidden rounded border border-rule bg-panel'>
						{slot.available && slot.image ? (
							<img src={asset(slot.image)} alt={`${slot.id} quicklook ${slot.date}`} className='block aspect-square w-full object-cover' />
						) : (
							<div className='flex aspect-square w-full items-center justify-center bg-radar/10 text-xs text-muted'>
								{slot.id === 'T4' ? 'T4 pending' : 'unavailable'}
							</div>
						)}
						<figcaption className='border-t border-rule px-2 py-1'>
							<span className='block text-xs font-semibold'>{slot.id}</span>
							<span className='block text-[11px] text-muted'>{slot.date}</span>
						</figcaption>
					</figure>
				))}
			</div>
			<p className='mt-2 text-[11px] leading-relaxed text-muted'>
				All four slots are the same spatial crop of calibrated HH backscatter. T4 is held open; when the
				2024-12-21 acquisition lands, the contract JSON is updated and this slot fills in without any
				frontend change.
			</p>
		</section>
	);
};

export { Comparison };
