import type { Contract } from '../data/types';

interface Props {
	contract: Contract;
}

const Timeline = ({ contract }: Props) => {
	const items = contract.acquisitions;
	return (
		<section aria-label='Acquisition timeline'>
			<h2 className='mb-2 text-sm font-semibold uppercase tracking-wide text-muted'>Acquisition timeline</h2>
			<ol className='relative flex flex-col gap-3 border-l border-rule pl-4'>
				{items.map((a, i) => (
					<li key={a.id} className='relative'>
						<span
							className='absolute -left-[21px] top-1 size-2.5 rounded-full border border-ink'
							style={{ background: i === items.length - 1 && a.id === 'T4' ? '#fff' : '#1c2620' }}
						/>
						<div className='flex flex-wrap items-baseline gap-x-2'>
							<span className='text-sm font-semibold'>{a.id}</span>
							<span className='text-sm tabular-nums'>{a.date}</span>
							<span className='text-[11px] text-muted'>
								{a.beam} · {a.polarization} · {a.orbit}
							</span>
						</div>
						<p className='text-xs text-body'>{a.role}</p>
					</li>
				))}
			</ol>
			{!contract.t4.available && (
				<p className='mt-3 rounded border border-dashed border-edge bg-panel/60 px-2.5 py-2 text-[11px] leading-relaxed text-muted'>
					{contract.t4.note}
				</p>
			)}
		</section>
	);
};

export { Timeline };
