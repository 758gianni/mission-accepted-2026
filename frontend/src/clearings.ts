import type { ClearingType } from './types';

const clearings: ClearingType[] = [
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

export { clearings };
