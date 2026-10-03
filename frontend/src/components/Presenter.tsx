import type { Contract, PresentationStep } from '../data/types';

interface Props {
	contract: Contract;
	stepIndex: number;
	onStep: (index: number) => void;
}

const Presenter = ({ contract, stepIndex, onStep }: Props) => {
	const steps: PresentationStep[] = contract.presentation_path;
	return (
		<section aria-label='Presentation path' className='rounded border border-rule bg-panel p-4'>
			<div className='mb-3 flex items-center justify-between gap-3'>
				<h2 className='text-sm font-semibold uppercase tracking-wide text-muted'>Presentation path</h2>
				<span className='text-xs tabular-nums text-muted'>
					{stepIndex + 1} / {steps.length}
				</span>
			</div>
			<ol className='flex flex-col gap-1'>
				{steps.map((s, i) => {
					const active = i === stepIndex;
					const done = i < stepIndex;
					return (
						<li key={s.id}>
							<button
								type='button'
								onClick={() => onStep(i)}
								className={`flex w-full items-start gap-2.5 rounded px-2.5 py-2 text-left transition-colors ${
									active ? 'bg-ink text-panel' : done ? 'bg-selected' : 'hover:bg-selected/60'
								}`}
							>
								<span className={`mt-0.5 text-xs font-semibold tabular-nums ${active ? 'text-panel/80' : 'text-muted'}`}>
									{s.step}
								</span>
								<span className='flex-1'>
									<span className='block text-sm font-semibold'>{s.title}</span>
									{active && <span className='mt-0.5 block text-xs leading-relaxed opacity-90'>{s.detail}</span>}
								</span>
							</button>
						</li>
					);
				})}
			</ol>
			<div className='mt-3 flex gap-2'>
				<button
					type='button'
					disabled={stepIndex === 0}
					onClick={() => onStep(stepIndex - 1)}
					className='button flex-1 disabled:opacity-40'
				>
					Back
				</button>
				<button
					type='button'
					disabled={stepIndex >= steps.length - 1}
					onClick={() => onStep(stepIndex + 1)}
					className='button flex-1 disabled:opacity-40'
				>
					Next
				</button>
			</div>
		</section>
	);
};

export { Presenter };
