import { Minus, Plus } from 'lucide-react';
import { useMemo, type FC } from 'react';
import type { ClearingType } from '../types';

interface ChangeMapProps {
	clearings: ClearingType[];
	selectedClearingId: string;
	swipePosition: number;
	showClearings: boolean;
	showBoundary: boolean;
	sensitivity: number;
	onShowClearingsChange: (value: boolean) => void;
	onShowBoundaryChange: (value: boolean) => void;
	onSensitivityChange: (value: number) => void;
}

const ChangeMap: FC<ChangeMapProps> = ({ clearings, selectedClearingId, swipePosition, showClearings, showBoundary, sensitivity, onShowClearingsChange, onShowBoundaryChange, onSensitivityChange }) => {
	const selected = useMemo(() => clearings?.find((clearing: ClearingType) => clearing?.id === selectedClearingId) ?? clearings[0], [clearings, selectedClearingId]);

	return (
		<div className='relative h-105 sm:h-145 rounded-md bg-radar overflow-hidden'>
			<svg viewBox='0 0 1000 600' preserveAspectRatio='xMidYMid slice' className='absolute inset-0 h-full w-full' aria-hidden='true'>
				<defs>
					<filter id='sarBefore' x='0' y='0' width='100%' height='100%'>
						<feTurbulence type='fractalNoise' baseFrequency='0.95' numOctaves={2} seed={4} />
						<feColorMatrix type='matrix' values='0.75 0 0 0 0.08  0.75 0 0 0 0.08  0.75 0 0 0 0.08  0 0 0 0 1' />
					</filter>
				</defs>
				<rect width='1000' height='600' filter='url(#sarBefore)' />
				<path d='M-20 420 C 120 380, 200 470, 340 430 S 560 330, 700 380 S 900 470, 1020 430' fill='none' stroke='#0A0B0A' strokeWidth={22} />
				<path d='M80 -10 C 140 120, 300 160, 360 300 S 420 520, 470 620' fill='none' stroke='#9A9E9B' strokeWidth={3} />
			</svg>

			<div className='absolute inset-0' style={{ clipPath: `inset(0 0 0 ${swipePosition}%)` }}>
				<svg viewBox='0 0 1000 600' preserveAspectRatio='xMidYMid slice' className='absolute inset-0 h-full w-full' aria-hidden='true'>
					<defs>
						<filter id='sarAfter' x='0' y='0' width='100%' height='100%'>
							<feTurbulence type='fractalNoise' baseFrequency='0.95' numOctaves={2} seed={4} />
							<feColorMatrix type='matrix' values='0.75 0 0 0 0.08  0.75 0 0 0 0.08  0.75 0 0 0 0.08  0 0 0 0 1' />
						</filter>
						<filter id='sarCleared' x='0' y='0' width='100%' height='100%'>
							<feTurbulence type='fractalNoise' baseFrequency='1.3' numOctaves={1} seed={9} />
							<feColorMatrix type='matrix' values='0.16 0 0 0 0.02  0.16 0 0 0 0.02  0.16 0 0 0 0.02  0 0 0 0 1' />
						</filter>
						<clipPath id='clearedShapes'>
							<path d='M560 150 L655 135 L690 205 L640 260 L570 240 Z' />
							<path d='M720 60 L800 70 L815 130 L745 140 Z' />
							<path d='M380 470 L450 455 L470 520 L400 540 Z' />
						</clipPath>
					</defs>
					<rect width='1000' height='600' filter='url(#sarAfter)' />
					<rect width='1000' height='600' filter='url(#sarCleared)' clipPath='url(#clearedShapes)' />
					<path d='M-20 420 C 120 380, 200 470, 340 430 S 560 330, 700 380 S 900 470, 1020 430' fill='none' stroke='#0A0B0A' strokeWidth={22} />
					<path d='M80 -10 C 140 120, 300 160, 360 300 S 420 520, 470 620' fill='none' stroke='#9A9E9B' strokeWidth={3} />
					<path d='M360 300 C 450 260, 520 220, 600 200' fill='none' stroke='#9A9E9B' strokeWidth={3} />
					<g stroke='#E14C8C' strokeLinejoin='round' style={{ opacity: showClearings ? 1 : 0 }}>
						{clearings.map((clearing) => (
							<path key={clearing.id} d={clearing.path} fill={selected.id === clearing.id ? 'rgba(225,76,140,0.55)' : 'rgba(225,76,140,0.28)'} stroke='#B0306A' strokeWidth={selected.id === clearing.id ? 4 : 2} strokeLinejoin='round' />
						))}
					</g>
				</svg>
			</div>

			<svg viewBox='0 0 1000 600' preserveAspectRatio='xMidYMid slice' className='pointer-events-none absolute inset-0 h-full w-full' aria-hidden='true' style={{ opacity: showBoundary ? 1 : 0 }}>
				<path d='M250 0V600M500 0V600M750 0V600M0 200H1000M0 400H1000' stroke='rgba(255,255,255,0.16)' strokeWidth={1} />
				<g fill='none' strokeLinejoin='round'>
					<path d='M470 90 L760 40 L880 180 L820 330 L560 360 L440 240 Z' stroke='#F6F7F3' strokeWidth={6} />
					<path d='M470 90 L760 40 L880 180 L820 330 L560 360 L440 240 Z' stroke='#163F52' strokeWidth={3} strokeDasharray='12 6' />
				</g>
			</svg>

			<div className='pointer-events-none absolute inset-y-0 -ml-px w-0.5 bg-panel' style={{ left: `${swipePosition}%` }}>
				<div className='absolute left-1/2 top-1/2 flex h-10 w-10 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-panel shadow-[0_1px_4px_rgba(0,0,0,0.35)]'>
					<svg width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='#1C2620' strokeWidth={2} strokeLinecap='round' strokeLinejoin='round' aria-hidden='true'>
						<path d='M9 6l-6 6 6 6M15 6l6 6-6 6' />
					</svg>
				</div>
			</div>

			<span className='absolute left-3.5 top-3.5 px-2.5 py-1.5 leading-none [text-box:trim-both_cap_alphabetic] rounded-[3px] bg-panel font-cond font-semibold text-base'>[Date 1]</span>
			<span className='absolute right-17.5 top-3.5 px-2.5 py-1.5 leading-none [text-box:trim-both_cap_alphabetic] rounded-[3px] bg-panel font-cond font-semibold text-base'>[Date 2]</span>

			<div className='absolute right-3 top-3 flex flex-col rounded bg-panel overflow-hidden'>
				<button
					//
					type='button'
					onClick={() => {}}
					className='size-11 inline-flex items-center justify-center gap-2 hover:bg-selected'
					aria-label='Zoom in'>
					<Plus className='size-4.5 shrink-0' />
				</button>

				<button
					//
					type='button'
					onClick={() => {}}
					className='size-11 inline-flex items-center justify-center gap-2 hover:bg-selected'
					aria-label='Zoom out'>
					<Minus className='size-4.5 shrink-0' />
				</button>
			</div>

			<div className='absolute bottom-3.5 left-3.5 flex max-w-[calc(100%-150px)] flex-wrap items-center gap-x-[18px] gap-y-1.5 rounded bg-panel px-3.5 py-2 text-sm'>
				<label className='flex min-h-[36px] cursor-pointer items-center gap-2'>
					<input type='checkbox' checked={showClearings} onChange={() => onShowClearingsChange(!showClearings)} className='h-[18px] w-[18px]' />
					<span className='h-3.5 w-3.5 rounded-sm border-2 border-loss bg-lossBright/45'></span>
					Clearings
				</label>

				<label className='flex min-h-[36px] cursor-pointer items-center gap-2'>
					<input type='checkbox' checked={showBoundary} onChange={() => onShowBoundaryChange(!showBoundary)} className='h-[18px] w-[18px]' />
					<span className='w-[18px] border-t-[3px] border-dashed border-boundary'></span>
					Reserve boundary
				</label>

				<label className='flex min-h-[36px] items-center gap-2.5'>
					Sensitivity
					<input type='range' min='1' max='6' step='0.5' value={sensitivity} onChange={(event) => onSensitivityChange(Number(event.target.value))} className='w-[110px]' />
					<span className='min-w-[44px]'>{sensitivity} dB</span>
				</label>
			</div>

			<div className='absolute bottom-3.5 right-3.5 px-2.5 py-1.5 flex flex-col gap-0.75 rounded bg-panel text-[13px]'>
				<div className='h-1.5 w-22.5 border-2 border-t-0 border-ink'></div>
				[1 km]
			</div>
		</div>
	);
};

export { ChangeMap };
