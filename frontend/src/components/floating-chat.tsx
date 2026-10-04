import { motion, MotionConfig, useReducedMotion } from 'framer-motion';
import { MessageCircle, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { type AssistantAction, type AssistantContext, type AssistantProvider } from '../assistant';
import { ChatPanel } from './chat-panel';

interface FloatingChatProps {
	context: AssistantContext;
	provider?: AssistantProvider;
	onAction: (action: AssistantAction) => void;
	onUndo: () => void;
	canUndo: boolean;
	presentationActive: boolean;
}

const FloatingChat = ({ context, provider, onAction, onUndo, canUndo, presentationActive }: FloatingChatProps) => {
	const [isOpen, setIsOpen] = useState(false);
	const launcherRef = useRef<HTMLButtonElement>(null);
	const panelRef = useRef<HTMLDivElement>(null);
	const reduceMotion = useReducedMotion();

	const close = useCallback(() => {
		setIsOpen(false);
		launcherRef.current?.focus();
	}, []);

	useEffect(() => {
		if (!isOpen) return;
		const handleEscape = (event: KeyboardEvent) => {
			if (event.key !== 'Escape') return;
			event.preventDefault();
			close();
		};
		window.addEventListener('keydown', handleEscape);
		return () => window.removeEventListener('keydown', handleEscape);
	}, [isOpen, close]);

	return (
		<MotionConfig reducedMotion='user' transition={{ type: 'spring', stiffness: 380, damping: 32 }}>
			<div className={`ts-assistant-host ${presentationActive ? 'is-presenting' : ''}`}>
				<motion.div
					ref={panelRef}
					id='area-chat'
					role='dialog'
					aria-label='Ask TerraSignal'
					aria-hidden={!isOpen}
					inert={!isOpen}
					initial={false}
					animate={{ opacity: isOpen ? 1 : 0, y: isOpen || reduceMotion ? 0 : 20, scale: isOpen || reduceMotion ? 1 : 0.95 }}
					style={{ pointerEvents: isOpen ? 'auto' : 'none', transformOrigin: 'bottom right' }}
					onAnimationComplete={() => {
						if (isOpen && document.activeElement === launcherRef.current) panelRef.current?.querySelector('textarea')?.focus({ preventScroll: true });
					}}
					className='ts-assistant-dialog flex overflow-hidden'>
					<ChatPanel context={context} provider={provider} onAction={(action) => { onAction(action); close(); }} suggestions={[context.selectedId == null ? 'Which candidates are independently supported?' : `Why is Region ${context.selectedId} ranked here?`, 'Show persistent anomalies larger than 100 ha.', 'How much data was reduced?', 'What evidence supports the possible new water body interpretation?']} onClose={close} isOpen={isOpen} />
				</motion.div>

				{canUndo && <button type='button' className='ts-assistant-undo' onClick={onUndo}>Undo map action</button>}
				<motion.button
					ref={launcherRef}
					type='button'
					aria-label={isOpen ? 'Close Ask TerraSignal' : 'Ask TerraSignal'}
					aria-expanded={isOpen}
					aria-controls='area-chat'
					onClick={() => (isOpen ? close() : setIsOpen(true))}
					whileHover={{ scale: reduceMotion ? 1 : 1.06 }}
					whileTap={{ scale: reduceMotion ? 1 : 0.94 }}
					className='ts-assistant-launcher'>
					<motion.span initial={false} animate={{ rotate: isOpen && !reduceMotion ? 90 : 0 }}>
						{isOpen ? <X className='size-6' aria-hidden='true' /> : <MessageCircle className='size-6' aria-hidden='true' />}
					</motion.span><span>Ask TerraSignal</span>
				</motion.button>
			</div>
		</MotionConfig>
	);
};

export { FloatingChat };
