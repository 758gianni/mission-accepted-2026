import { ArrowDownRight, ArrowRight, Check, CircleDot, Filter, ScanSearch } from 'lucide-react';
import type { FC } from 'react';
import type { CandidateFilter } from '../terra-data';
import type { FunnelStage } from '../terra-data';

interface ReductionFunnelProps {
	stages: FunnelStage[];
	activeFilter: CandidateFilter;
	onSelect: (filter: CandidateFilter) => void;
}

const icons = [ScanSearch, Filter, ArrowDownRight, Check];

const ReductionFunnel: FC<ReductionFunnelProps> = ({ stages, activeFilter, onSelect }) => <section className='ts-funnel' aria-label='Candidate reduction funnel'>
	<div className='ts-funnel-heading'>
		<div><span className='ts-eyebrow'>From signal volume to investigation</span><h2>Find the changes that persist.</h2></div>
		<p>Explore the signals. Isolate persistence. Inspect the evidence.</p>
	</div>
	<div className='ts-funnel-flow'>
		<div className='ts-flow-track' aria-hidden='true'><span /></div>
		{stages.map((stage, index) => {
			const Icon = icons[index] ?? CircleDot;
			const active = activeFilter === stage.id;
			return <button type='button' key={stage.id} className={`ts-funnel-stage ${active ? 'is-active' : ''} stage-${index}`} aria-pressed={active} onClick={() => onSelect(stage.id)}>
				<span className='ts-stage-symbol'><Icon size={15} /></span>
				<strong>{stage.value.toLocaleString()}</strong>
				<span className='ts-stage-label'>{stage.label}</span>
				{index < stages.length - 1 && <ArrowRight className='ts-stage-arrow' size={15} aria-hidden='true' />}
			</button>;
		})}
	</div>
</section>;

export { ReductionFunnel };
