import { useMemo, useState } from 'react';

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

const Sidebar = () => {
	const [selectedId, setSelectedId] = useState('c1');
	const selected = useMemo(() => clearings.find((clearing) => clearing.id === selectedId) ?? clearings[0], [selectedId]);

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
	);
};

export { Sidebar };
