import { Compass, Play, Satellite, X } from 'lucide-react';
import type { FC } from 'react';
import type { DashboardContract } from '../terra-data';

interface HeaderProps {
	contract: DashboardContract;
	presentationActive: boolean;
	onTogglePresentation: () => void;
}

const Header: FC<HeaderProps> = ({ contract, presentationActive, onTogglePresentation }) => (
	<header className='ts-topbar'>
		<div className='ts-brand'>
			<div className='ts-brand-mark'><Satellite size={20} strokeWidth={1.8} /></div>
			<div>
				<h1>TerraSignal</h1>
				<p>EARTH OBSERVATION INTELLIGENCE</p>
			</div>
		</div>
		<div className='ts-topbar-context'>
			<span className='ts-live-dot' />
			<div><strong>{contract.scene?.name ?? 'Area of interest'}</strong><span> · {contract.tile_processing?.acquisition_count ?? contract.acquisitions?.length ?? 0} source acquisitions</span></div>
			<span className='ts-source-chip'><span>PRIMARY</span> RADARSAT-2</span>
		</div>
		<button type='button' className={`ts-present-button ${presentationActive ? 'is-active' : ''}`} onClick={onTogglePresentation}>
			{presentationActive ? <X size={15} /> : <Play size={15} fill='currentColor' />}
			{presentationActive ? 'Exit walkthrough' : 'Guided walkthrough'}
			<Compass size={15} className='ts-present-compass' />
		</button>
	</header>
);

export { Header };
