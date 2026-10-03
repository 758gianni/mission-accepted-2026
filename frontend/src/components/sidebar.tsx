import { useMemo, type FC } from 'react';
import type { ChangeMode } from '../change-modes';
import type { ClearingType } from '../types';

interface SidebarProps {
	mode: ChangeMode;
	clearings: ClearingType[];
	selectedClearingId: string;
	onSelectedClearingChange: (value: string) => void;
}

const Sidebar: FC<SidebarProps> = ({ mode, clearings, selectedClearingId, onSelectedClearingChange }) => {
	const selected = useMemo(() => clearings.find((clearing) => clearing.id === selectedClearingId) ?? clearings[0], [clearings, selectedClearingId]);

	if (!selected) {
		return null;
	}

	return (
		<aside className='flex flex-[1_1_340px] flex-col gap-7 border-t border-rule bg-panel px-7 pb-8 pt-7 lg:max-w-[420px] lg:border-l lg:border-t-0'>
			<p className='max-w-[30ch] text-[22px] font-medium leading-[1.35]'>{mode.summary}</p>

			<section className='flex flex-col gap-1'>
				<h2 className='mb-2 text-base font-semibold'>{clearings.length} example changes</h2>

				<div className='flex flex-col gap-1'>
					{clearings.map((clearing) => {
						const isSelected = clearing.id === selected.id;

						return (
							<button key={clearing.id} type='button' aria-pressed={isSelected} onClick={() => onSelectedClearingChange(clearing.id)} className={`flex w-full items-center gap-3.5 rounded px-3 py-2.5 text-left ${isSelected ? 'bg-selected' : 'hover:bg-selected/60'}`}>
								<svg width='48' height='48' viewBox={clearing.viewBox} aria-hidden='true' className='flex-none'>
									<path d={clearing.path} fill={clearing.direction === 'loss' ? '#a9692a' : mode.color} fillOpacity={isSelected ? 0.55 : 0.15} fillRule='evenodd' stroke={clearing.direction === 'loss' ? '#a9692a' : mode.color} strokeWidth={4} strokeLinejoin='round' />
								</svg>

								<span className='flex min-w-0 flex-1 flex-col gap-0.5'>
									<span className='text-base font-semibold'>{clearing.name}</span>
									<span className='text-sm text-muted'>{clearing.where}</span>
								</span>

								<span className='whitespace-nowrap text-base font-medium'>{clearing.area}</span>
							</button>
						);
					})}
				</div>
			</section>

			<section className='border-t border-rule pt-5'><h2 className='mb-2 text-base font-semibold'>{selected.title}</h2><p className='text-sm leading-relaxed text-muted'>{selected.detail}</p><span className='mt-3 inline-block rounded-full bg-selected px-3 py-1 text-xs'>Example · Needs validation</span></section>
		</aside>
	);
};

export { Sidebar };
