import { useState } from 'react';
import { App } from './app';
import { clearings } from './clearings';
import { Header } from './components/header';
import { Sidebar } from './components/sidebar';
import { FloatingChat } from './components/floating-chat';

const Root = () => {
	const [selectedId, setSelectedId] = useState('c1');
	const [swipePosition, setSwipePosition] = useState(50);
	const [showClearings, setShowClearings] = useState(true);
	const [showBoundary, setShowBoundary] = useState(true);
	const [sensitivity, setSensitivity] = useState(3);

	return (
		<div className='min-h-screen flex flex-col'>
			<Header />

			<div className='flex flex-1 flex-wrap'>
				<App clearings={clearings} selectedClearingId={selectedId} swipePosition={swipePosition} showClearings={showClearings} showBoundary={showBoundary} sensitivity={sensitivity} onSwipePositionChange={setSwipePosition} onShowClearingsChange={setShowClearings} onShowBoundaryChange={setShowBoundary} onSensitivityChange={setSensitivity} />
				<Sidebar clearings={clearings} selectedClearingId={selectedId} onSelectedClearingChange={setSelectedId} />
			</div>

			<FloatingChat selectedClearing={clearings.find((clearing) => clearing.id === selectedId)?.title ?? 'Selected area'} sensitivityDb={sensitivity} />
		</div>
	);
};

export { Root };
