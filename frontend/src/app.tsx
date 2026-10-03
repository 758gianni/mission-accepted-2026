import { useMemo, type FC } from 'react';
import { ChangeMap } from './components/change-map';
import type { ChangeMode } from './change-modes';
import type { ClearingType } from './types';

interface AppProps {
	mode: ChangeMode;
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

const App: FC<AppProps> = ({ mode, clearings, selectedClearingId, swipePosition, showClearings, showBoundary, sensitivity, onSwipePositionChange, onShowClearingsChange, onShowBoundaryChange, onSensitivityChange }) => {
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

				<ChangeMap mode={mode} clearings={clearings} selectedClearingId={selectedClearingId} swipePosition={swipePosition} showClearings={showClearings} showBoundary={showBoundary} sensitivity={sensitivity} onShowClearingsChange={onShowClearingsChange} onShowBoundaryChange={onShowBoundaryChange} onSensitivityChange={onSensitivityChange} />
			</div>

			<label className='ml-11 flex items-center gap-3.5 text-[15px]'>
				<span className='font-semibold'>Before</span>
				<input type='range' min='0' max='100' value={swipePosition} onChange={(event) => onSwipePositionChange(Number(event.target.value))} aria-label='Slide to compare the two dates' className='min-h-[32px] flex-1' />
				<span className='font-semibold'>After</span>
			</label>

			<section className='ml-11 flex flex-col gap-2 border-t border-rule pt-3'>
				<h2 className='text-base font-semibold'>{mode.signal}, {selected.name.toLowerCase()}</h2>
				<p className='max-w-[70ch] text-sm text-muted'>{mode.description}</p>
				<svg viewBox='0 0 900 140' className='h-[140px] w-full' role='img' aria-label={`${mode.signal}: illustrative trend, not measured data`}>
					<line x1='30' y1='110' x2='860' y2='110' stroke='#C9CFC7' />
					<polyline points={mode.points.map((y, i) => `${40 + i * 130},${y}`).join(' ')} fill='none' stroke={mode.color} strokeWidth={3} strokeLinejoin='round' />
					{mode.points.map((y, i) => <g key={i}><circle cx={40 + i * 130} cy={y} r='4' fill={mode.color} /><text x={40 + i * 130} y='130' textAnchor='middle' fill='#56625B' fontSize={12}>Sample {i + 1}</text></g>)}
					<text x='40' y='20' fill='#56625B' fontSize={13}>{mode.beforeLabel}</text>
					<text x='565' y='20' fill={mode.color} fontSize={13}>{mode.afterLabel}</text>
				</svg>
			</section>
		</main>
	);
};

export { App };
