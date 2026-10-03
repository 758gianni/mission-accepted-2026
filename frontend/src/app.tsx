import { useMemo, type FC } from 'react';
import { ChangeMap } from './components/change-map';
import type { ClearingType } from './types';

interface AppProps {
	clearings: ClearingType[];
	selectedClearingId: string;
	swipePosition: number;
	showClearings: boolean;
	showBoundary: boolean;
	sensitivity: number;
	onSwipePositionChange: (value: number) => void;
	onShowClearingsChange: (value: boolean) => void;
	onShowBoundaryChange: (value: boolean) => void;
	onSensitivityChange: (value: number) => void;
}

const App: FC<AppProps> = ({ clearings, selectedClearingId, swipePosition, showClearings, showBoundary, sensitivity, onSwipePositionChange, onShowClearingsChange, onShowBoundaryChange, onSensitivityChange }) => {
	const selected = useMemo(() => clearings.find((clearing) => clearing.id === selectedClearingId) ?? clearings[0], [clearings, selectedClearingId]);

	return (
		<main className='min-w-0 px-5 pb-8 pt-6 sm:px-8 flex flex-[999_1_640px] flex-col gap-3.5'>
			<div className='grid grid-cols-[44px_minmax(0,1fr)] grid-rows-[22px_auto]'>
				<div />

				<div className='px-0.5 flex flex-row justify-between font-cond text-[13px] text-muted'>
					<span>[lon 1]</span>
					<span>[lon 2]</span>
					<span>[lon 3]</span>
					<span>[lon 4]</span>
					<span>[lon 5]</span>
				</div>

				<div className='py-1.5 flex flex-col justify-between font-cond text-[13px] text-muted'>
					<span>[lat 1]</span>
					<span>[lat 2]</span>
					<span>[lat 3]</span>
				</div>

				<ChangeMap clearings={clearings} selectedClearingId={selectedClearingId} swipePosition={swipePosition} showClearings={showClearings} showBoundary={showBoundary} sensitivity={sensitivity} onShowClearingsChange={onShowClearingsChange} onShowBoundaryChange={onShowBoundaryChange} onSensitivityChange={onSensitivityChange} />
			</div>

			<label className='ml-11 flex items-center gap-3.5 text-[15px]'>
				<span className='font-semibold'>[Date 1]</span>
				<input type='range' min='0' max='100' value={swipePosition} onChange={(event) => onSwipePositionChange(Number(event.target.value))} aria-label='Slide to compare the two dates' className='min-h-[32px] flex-1' />
				<span className='font-semibold'>[Date 2]</span>
			</label>

			<section className='ml-11 flex flex-col gap-1.5 border-t border-rule pt-3'>
				<h2 className='text-base font-semibold'>
					Radar return over time, <span>{selected.name.toLowerCase()}</span>
				</h2>

				<p className='max-w-[62ch] text-sm text-muted'>Standing forest scatters the radar back strongly. A sharp, lasting drop means the canopy is gone.</p>

				<svg viewBox='0 0 900 130' className='h-[130px] w-full' role='img' aria-label='Illustrative series: the radar return holds steady, then drops between the fourth and fifth acquisitions and stays low'>
					<line x1='0' y1='105' x2='900' y2='105' stroke='#C9CFC7' />
					<line x1='0' y1='34' x2='900' y2='34' stroke='#C9CFC7' strokeDasharray='2 6' />
					<polyline points='40,32 170,36 300,30 430,34' fill='none' stroke='#1C2620' strokeWidth={2.5} strokeLinejoin='round' />
					<polyline points='430,34 560,86' fill='none' stroke='#B0306A' strokeWidth={3} />
					<polyline points='560,86 690,90 820,85' fill='none' stroke='#1C2620' strokeWidth={2.5} strokeLinejoin='round' />
					<g fill='#1C2620'>
						<circle cx='40' cy='32' r='4' />
						<circle cx='170' cy='36' r='4' />
						<circle cx='300' cy='30' r='4' />
						<circle cx='430' cy='34' r='4' />
						<circle cx='690' cy='90' r='4' />
						<circle cx='820' cy='85' r='4' />
					</g>
					<circle cx='560' cy='86' r='6' fill='#B0306A' />
					<text x='40' y='24' fill='#56625B' fontSize={13} fontFamily='Barlow, sans-serif'>
						Forest
					</text>
					<text x='572' y='70' fill='#B0306A' fontSize={14} fontWeight={600} fontFamily='Barlow, sans-serif'>
						Cleared
					</text>
					<g fill='#56625B' fontSize={13} fontFamily='Barlow Condensed, sans-serif' textAnchor='middle'>
						<text x='40' y='124'>
							[D1]
						</text>
						<text x='170' y='124'>
							[D2]
						</text>
						<text x='300' y='124'>
							[D3]
						</text>
						<text x='430' y='124'>
							[D4]
						</text>
						<text x='560' y='124'>
							[D5]
						</text>
						<text x='690' y='124'>
							[D6]
						</text>
						<text x='820' y='124'>
							[D7]
						</text>
					</g>
				</svg>
			</section>
		</main>
	);
};

export { App };
