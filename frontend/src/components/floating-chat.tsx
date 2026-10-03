import { motion, MotionConfig, useReducedMotion } from 'framer-motion';
import { MessageCircle, X } from 'lucide-react';
import { useRef, useState } from 'react';
import { ChatPanel } from './chat-panel';

interface FloatingChatProps {
	selectedClearing: string;
	sensitivityDb: number;
}

const suggestions = ['Which clearings are inside the reserve?', 'When did this clearing start?', 'How reliable is this detection?'];

const FloatingChat = ({ selectedClearing, sensitivityDb }: FloatingChatProps) => {
	const [isOpen, setIsOpen] = useState(false);
	const launcherRef = useRef<HTMLButtonElement>(null);
	const panelRef = useRef<HTMLDivElement>(null);
	const reduceMotion = useReducedMotion();

	const close = () => {
		setIsOpen(false);
		launcherRef.current?.focus();
	};

	return (
		<MotionConfig reducedMotion='user' transition={{ type: 'spring', stiffness: 380, damping: 32 }}>
			<div className='fixed bottom-[max(1rem,env(safe-area-inset-bottom))] right-[max(1rem,env(safe-area-inset-right))] z-50 flex flex-col items-end gap-3 sm:bottom-6 sm:right-6'>
				<motion.div
					ref={panelRef}
					id='area-chat'
					role='dialog'
					aria-label='Ask about this area'
					aria-hidden={!isOpen}
					inert={!isOpen}
					initial={false}
					animate={{ opacity: isOpen ? 1 : 0, y: isOpen || reduceMotion ? 0 : 20, scale: isOpen || reduceMotion ? 1 : 0.95 }}
					style={{ pointerEvents: isOpen ? 'auto' : 'none', transformOrigin: 'bottom right' }}
					onAnimationComplete={() => {
						if (isOpen && document.activeElement === launcherRef.current) panelRef.current?.querySelector('textarea')?.focus({ preventScroll: true });
					}}
					onKeyDown={(event) => {
						if (event.key === 'Escape') {
							event.preventDefault();
							close();
						}
					}}
					className='flex h-[min(560px,calc(100dvh-112px))] w-[min(400px,calc(100vw-32px))] overflow-hidden rounded-2xl border border-edge bg-panel shadow-[0_16px_60px_-12px_rgba(28,38,32,0.3)]'>
					<ChatPanel selectedClearing={selectedClearing} sensitivityDb={sensitivityDb} suggestions={suggestions} onClose={close} isOpen={isOpen} />
				</motion.div>

				<motion.button
					ref={launcherRef}
					type='button'
					aria-label={isOpen ? 'Close chat' : 'Open chat'}
					aria-expanded={isOpen}
					aria-controls='area-chat'
					onClick={() => (isOpen ? close() : setIsOpen(true))}
					whileHover={{ scale: reduceMotion ? 1 : 1.06 }}
					whileTap={{ scale: reduceMotion ? 1 : 0.94 }}
					className='inline-flex size-14 items-center justify-center rounded-full bg-ink text-panel shadow-lg [transition-property:background-color] hover:bg-ink/90'>
					<motion.span initial={false} animate={{ rotate: isOpen && !reduceMotion ? 90 : 0 }}>
						{isOpen ? <X className='size-6' aria-hidden='true' /> : <MessageCircle className='size-6' aria-hidden='true' />}
					</motion.span>
				</motion.button>
			</div>
		</MotionConfig>
	);
};

export { FloatingChat };
