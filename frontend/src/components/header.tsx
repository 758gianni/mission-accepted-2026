import { Download } from 'lucide-react';

const Header = () => {
	return (
		<header className='select-none px-5 pb-4 pt-5 sm:px-8 flex flex-wrap items-center justify-center gap-x-8 gap-y-3 border-b border-rule'>
			<div className='min-w-[min(100%,360px)] flex flex-1 flex-col gap-0.5'>
				<h1 className='font-cond font-semibold text-[32px] sm:text-[40px] leading-[1.05] tracking-[-0.01em]'>Team Satellites</h1>
				<span className='text-[15px] font-semibold'>Mission Accepted 2026</span>
			</div>

			<div className='flex flex-wrap items-end justify-center gap-3'>
				<select className='min-h-11 rounded border border-edge bg-panel px-3 text-[15px] text-ink'>
					<option>[Date 1] to [Date 2]</option>
				</select>

				<button type='button' onClick={() => {}} className='button border-edge'>
					Download clearings
					<Download className='size-4 shrink-0' />
				</button>
			</div>
		</header>
	);
};

export { Header };
