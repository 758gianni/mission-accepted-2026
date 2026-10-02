import { useMemo, useState } from 'react';
import { Header } from './components/header';

const clearings = [
	{
		id: 'c1',
		name: 'North block',
		title: 'North block clearing',
		where: 'Inside the reserve',
		area: '[00] ha',
		viewBox: '545 120 160 155',
		path: 'M560 150 L655 135 L690 205 L640 260 L570 240 Z',
		detail: 'Inside the reserve boundary, about [0] km from the nearest road. First seen on [Date 2].',
	},
	{
		id: 'c2',
		name: 'Northeast edge',
		title: 'Northeast edge clearing',
		where: 'Just outside the boundary',
		area: '[00] ha',
		viewBox: '705 45 125 110',
		path: 'M720 60 L800 70 L815 130 L745 140 Z',
		detail: 'In the buffer zone, [00] m outside the boundary. First seen on [Date 2].',
	},
	{
		id: 'c3',
		name: 'Access road',
		title: 'Access road clearing',
		where: 'Outside the reserve',
		area: '[00] ha',
		viewBox: '365 440 120 115',
		path: 'M380 470 L450 455 L470 520 L400 540 Z',
		detail: 'Next to the access road, outside the reserve. First seen on [Date 2].',
	},
];

const App = () => {
	const [selectedId, setSelectedId] = useState('c1');
	const [swipePosition, setSwipePosition] = useState(50);
	const [showClearings, setShowClearings] = useState(true);
	const [showBoundary, setShowBoundary] = useState(true);
	const [sensitivity, setSensitivity] = useState(3);

	const selected = useMemo(() => clearings.find((clearing) => clearing.id === selectedId) ?? clearings[0], [selectedId]);

	return (
		<div className='flex min-h-screen flex-col'>
			<Header />

			<div className='flex flex-1 flex-wrap'>
				<main className='flex min-w-0 flex-[999_1_640px] flex-col gap-3.5 px-5 pb-8 pt-6 sm:px-8'>
					<div className='grid grid-cols-[44px_minmax(0,1fr)] grid-rows-[22px_auto]'>
						<div></div>
						<div className='flex justify-between px-0.5 font-cond text-[13px] text-muted'>
							<span>[lon 1]</span>
							<span>[lon 2]</span>
							<span>[lon 3]</span>
							<span>[lon 4]</span>
							<span>[lon 5]</span>
						</div>
						<div className='flex flex-col justify-between py-1.5 font-cond text-[13px] text-muted'>
							<span>[lat 1]</span>
							<span>[lat 2]</span>
							<span>[lat 3]</span>
						</div>

						<div className='relative h-[420px] overflow-hidden rounded-md bg-radar sm:h-[580px]'>
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

							<span className='absolute left-3.5 top-3.5 rounded-[3px] bg-panel px-2.5 py-1 font-cond text-base font-semibold'>[Date 1]</span>
							<span className='absolute right-[70px] top-3.5 rounded-[3px] bg-panel px-2.5 py-1 font-cond text-base font-semibold'>[Date 2]</span>

							<div className='absolute right-3 top-3 flex flex-col overflow-hidden rounded bg-panel'>
								<button type='button' aria-label='Zoom in' className='flex h-11 w-11 items-center justify-center hover:bg-selected'>
									<svg width='18' height='18' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth={2} strokeLinecap='round' aria-hidden='true'>
										<path d='M12 5v14M5 12h14' />
									</svg>
								</button>
								<button type='button' aria-label='Zoom out' className='flex h-11 w-11 items-center justify-center border-t border-rule hover:bg-selected'>
									<svg width='18' height='18' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth={2} strokeLinecap='round' aria-hidden='true'>
										<path d='M5 12h14' />
									</svg>
								</button>
							</div>

							<div className='absolute bottom-3.5 left-3.5 flex max-w-[calc(100%-150px)] flex-wrap items-center gap-x-[18px] gap-y-1.5 rounded bg-panel px-3.5 py-2 text-sm'>
								<label className='flex min-h-[36px] cursor-pointer items-center gap-2'>
									<input type='checkbox' checked={showClearings} onChange={() => setShowClearings((current) => !current)} className='h-[18px] w-[18px]' />
									<span className='h-3.5 w-3.5 rounded-sm border-2 border-loss bg-lossBright/45'></span>
									Clearings
								</label>
								<label className='flex min-h-[36px] cursor-pointer items-center gap-2'>
									<input type='checkbox' checked={showBoundary} onChange={() => setShowBoundary((current) => !current)} className='h-[18px] w-[18px]' />
									<span className='w-[18px] border-t-[3px] border-dashed border-boundary'></span>
									Reserve boundary
								</label>
								<label className='flex min-h-[36px] items-center gap-2.5'>
									Sensitivity
									<input type='range' min='1' max='6' step='0.5' value={sensitivity} onChange={(event) => setSensitivity(Number(event.target.value))} className='w-[110px]' />
									<span className='min-w-[44px]'>{sensitivity} dB</span>
								</label>
							</div>

							<div className='absolute bottom-3.5 right-3.5 flex flex-col gap-[3px] rounded bg-panel px-2.5 py-1.5 text-[13px]'>
								<div className='h-1.5 w-[90px] border-2 border-t-0 border-ink'></div>
								[1 km]
							</div>
						</div>
					</div>

					<label className='ml-11 flex items-center gap-3.5 text-[15px]'>
						<span className='font-semibold'>[Date 1]</span>
						<input type='range' min='0' max='100' value={swipePosition} onChange={(event) => setSwipePosition(Number(event.target.value))} aria-label='Slide to compare the two dates' className='min-h-[32px] flex-1' />
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
									<button key={clearing.id} type='button' aria-pressed={isSelected} onClick={() => setSelectedId(clearing.id)} className={`flex w-full items-center gap-3.5 rounded px-3 py-2.5 text-left ${isSelected ? 'bg-selected' : 'hover:bg-selected/60'}`}>
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

					<section className='mt-auto flex flex-col gap-3 border-t border-rule pt-5'>
						<h2 className='text-xl font-semibold'>{selected.title}</h2>
						<p className='text-[15px] leading-normal text-body'>{selected.detail}</p>
						<div className='flex flex-wrap gap-2.5'>
							<button type='button' className='min-h-[44px] rounded bg-ink px-[18px] text-[15px] font-semibold text-panel hover:bg-ink/90'>
								Send to reserve team
							</button>
							<button type='button' className='min-h-[44px] rounded border border-edge px-[18px] text-[15px] hover:bg-selected'>
								Mark as reviewed
							</button>
						</div>
					</section>
				</aside>
			</div>
		</div>
	);
};

export { App };
