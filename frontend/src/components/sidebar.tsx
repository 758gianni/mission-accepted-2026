import { useMemo, type FC } from 'react';
import type { ClearingType } from '../types';

interface SidebarProps {
	clearings: ClearingType[];
	selectedClearingId: string;
	onSelectedClearingChange: (value: string) => void;
}

const Sidebar: FC<SidebarProps> = ({ clearings, selectedClearingId, onSelectedClearingChange }) => {
	const selected = useMemo(() => clearings.find((clearing) => clearing.id === selectedClearingId) ?? clearings[0], [clearings, selectedClearingId]);

	if (!selected) {
		return null;
	}

	return (
		<aside className='flex flex-[1_1_340px] flex-col gap-7 border-t border-rule bg-panel px-7 pb-8 pt-7 lg:max-w-[420px] lg:border-l lg:border-t-0'>
			<p className='max-w-[30ch] text-[22px] font-medium leading-[1.35]'>
				<strong className='font-semibold'>[000] ha</strong> of forest was cleared between [Date 1] and [Date 2].
				<strong className='font-semibold'>[00] ha</strong> of it is inside the reserve.
			</p>

			<section className='flex flex-col gap-1'>
				<h2 className='mb-2 text-base font-semibold'>3 new clearings, largest first</h2>

				<div className='flex flex-col gap-1'>
					{clearings.map((clearing) => {
						const isSelected = clearing.id === selected.id;

						return (
							<button key={clearing.id} type='button' aria-pressed={isSelected} onClick={() => onSelectedClearingChange(clearing.id)} className={`flex w-full items-center gap-3.5 rounded px-3 py-2.5 text-left ${isSelected ? 'bg-selected' : 'hover:bg-selected/60'}`}>
								<svg width='48' height='48' viewBox={clearing.viewBox} aria-hidden='true' className='flex-none'>
									<path d={clearing.path} fill={isSelected ? 'rgba(225,76,140,0.55)' : 'rgba(225,76,140,0.15)'} stroke='#B0306A' strokeWidth={4} strokeLinejoin='round' />
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

		</aside>
	);
};

export { Sidebar };
