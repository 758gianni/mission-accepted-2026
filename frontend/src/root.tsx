import { useState } from 'react';
import { App } from './app';
import { changeModes } from './change-modes';
import { Header } from './components/header';
import { Sidebar } from './components/sidebar';
import { FloatingChat } from './components/floating-chat';

const Root = () => {
	const [modeId, setModeId] = useState('deforestation');
	const mode = changeModes.find((item) => item.id === modeId) ?? changeModes[0];
	const clearings = mode.events;
	const [selectedId, setSelectedId] = useState('c1');
	const [swipePosition, setSwipePosition] = useState(50);
	const [showClearings, setShowClearings] = useState(true);
	const [showBoundary, setShowBoundary] = useState(true);
	const [sensitivity, setSensitivity] = useState(3);

	return (
		<div className='min-h-screen flex flex-col'>
			<Header mode={mode} />
			<section className='border-b border-rule px-5 py-4 sm:px-8' aria-label='Change categories'>
				<p className='mb-3 text-sm text-muted'>Illustrative demo · No live detections or measured areas</p>
				<div className='flex flex-wrap gap-2'>{changeModes.map((item) => <button key={item.id} type='button' aria-pressed={modeId === item.id} onClick={() => { setModeId(item.id); setSelectedId(item.events[0].id); }} className={`min-h-11 rounded-full border px-5 text-sm font-semibold ${modeId === item.id ? 'border-ink bg-ink text-panel' : 'border-edge bg-panel hover:bg-selected'}`}>{item.label}</button>)}</div>
			</section>

			<div className='flex flex-1 flex-wrap'>
				<App mode={mode} clearings={clearings} selectedClearingId={selectedId} swipePosition={swipePosition} showClearings={showClearings} showBoundary={showBoundary} sensitivity={sensitivity} onSwipePositionChange={setSwipePosition} onShowClearingsChange={setShowClearings} onShowBoundaryChange={setShowBoundary} onSensitivityChange={setSensitivity} />
				<Sidebar mode={mode} clearings={clearings} selectedClearingId={selectedId} onSelectedClearingChange={setSelectedId} />
			</div>

			<FloatingChat mode={mode} selectedClearing={clearings.find((clearing) => clearing.id === selectedId)?.title ?? 'Selected area'} sensitivityDb={sensitivity} />
		</div>
	);
};

export { Root };
