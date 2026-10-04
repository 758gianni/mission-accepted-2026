import { motion, useReducedMotion } from 'framer-motion';
import { ArrowUp, MessageCircle, RotateCcw, X } from 'lucide-react';
import { localAssistant, type AssistantAction, type AssistantContext, type AssistantProvider, type AssistantReply } from '../assistant';
import { useEffect, useRef, useState, type ChangeEvent, type FormEvent, type KeyboardEvent } from 'react';

export type ChatRole = 'user' | 'assistant';

export interface ChatMessage {
	id: string;
	role: ChatRole;
	content: string;
	reply?: AssistantReply;
}

interface ChatPanelProps {
	context: AssistantContext;
	provider?: AssistantProvider;
	onAction: (action: AssistantAction) => void;
	suggestions?: string[];
	onClose?: () => void;
	isOpen?: boolean;
}

const MAX_INPUT_HEIGHT = 160;

const createId = () => crypto.randomUUID();

const ChatPanel = ({ context, provider = localAssistant, onAction, suggestions = [], onClose, isOpen = true }: ChatPanelProps) => {
	const title = 'Ask TerraSignal';
	const selectedClearing = context.selectedId == null ? context.contract.scene?.name ?? 'Current dataset' : `Region ${context.selectedId} selected`;
	const placeholder = 'Ask about evidence or find anomalies…';
	const reduceMotion = useReducedMotion();

	const [messages, setMessages] = useState<ChatMessage[]>([]);
	const [input, setInput] = useState<string>('');
	const [isSending, setIsSending] = useState<boolean>(false);
	const [error, setError] = useState<string | null>(null);

	const listRef = useRef<HTMLDivElement>(null);
	const inputRef = useRef<HTMLTextAreaElement>(null);

	const sendMessage = async (history: ChatMessage[]) => {
		const request = { question: history.at(-1)!.content, context };
		try { return await provider.answer(request); }
		catch (failure) {
			if (provider === localAssistant) throw failure;
			const fallback = await localAssistant.answer(request);
			return { ...fallback, text: `The configured provider is unavailable. Local dataset answer:\n\n${fallback.text}` };
		}
	};

	// Keep the newest message in view as the conversation grows.
	useEffect(() => {
		const list = listRef.current;

		if (!list) {
			return;
		}

		list.scrollTo({
			top: list.scrollHeight,
			behavior: reduceMotion ? 'auto' : 'smooth',
		});
	}, [messages, isSending, error, isOpen, reduceMotion]);

	const resizeInput = () => {
		const textarea = inputRef.current;

		if (!textarea) {
			return;
		}

		textarea.style.height = 'auto';
		textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_INPUT_HEIGHT)}px`;
	};

	const requestReply = async (history: ChatMessage[]) => {
		setIsSending(true);
		setError(null);

		try {
			const reply = await sendMessage(history);

			setMessages([
				...history,
				{
					id: createId(),
					role: 'assistant',
					content: reply.text,
					reply,
				},
			]);
		} catch (requestError) {
			setError(requestError instanceof Error ? requestError.message : "Couldn't get a reply. Check your connection and try again.");
		} finally {
			setIsSending(false);
		}
	};

	const handleSend = (text: string) => {
		const content = text.trim();

		if (!content || isSending) {
			return;
		}

		const history: ChatMessage[] = [...messages, { id: createId(), role: 'user', content }];

		setMessages(history);
		setInput('');

		// Collapse the textarea back to one line after the value clears.
		requestAnimationFrame(resizeInput);

		void requestReply(history);
	};

	// The failed request never appended a reply, so the last message is still the user's question.
	const handleRetry = () => {
		void requestReply(messages);
	};

	const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		handleSend(input);
	};

	const handleInputChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
		setInput(event.target.value);
		resizeInput();
	};

	// Enter sends, Shift+Enter adds a new line. Ignore Enter while an IME is composing text.
	const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
		if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
			e.preventDefault();
			handleSend(input);
		}
	};

	const canSend = input.trim().length > 0 && !isSending;
	const isEmpty = messages.length === 0;

	return (
		<section aria-label={title} className='ts-assistant-panel flex min-h-0 flex-1 flex-col'>
			<header className='flex items-center gap-3 border-b border-rule px-5 py-4'>
				<span className='flex size-10 shrink-0 items-center justify-center rounded-xl bg-selected'>
					<MessageCircle className='size-5' aria-hidden='true' />
				</span>

				<div className='min-w-0 flex-1'>
					<h2 className='text-base font-semibold'>{title}</h2>
					<p className='truncate text-xs text-muted'>{selectedClearing}</p><small className='ts-assistant-provider'>{provider.name}</small>
				</div>

				{onClose && (
					<motion.button type='button' onClick={onClose} aria-label='Close chat' whileTap={{ scale: 0.9 }} className='flex size-9 shrink-0 items-center justify-center rounded-full hover:bg-selected'>
						<X className='size-4' aria-hidden='true' />
					</motion.button>
				)}
			</header>

			<div ref={listRef} className='min-h-0 flex-1 overflow-y-auto px-5 py-4' aria-live='polite'>
				{isEmpty ? (
					<div className='flex flex-col gap-3'>
						<p className='max-w-[40ch] text-[15px] leading-normal text-muted'>Ask about recorded evidence, dataset results, or matching anomalies. Suggested map actions are optional and reversible.</p>

						{suggestions.length > 0 && (
							<div className='flex flex-col gap-2'>
								{suggestions.map((suggestion) => (
									<motion.button whileHover={{ x: reduceMotion ? 0 : 3 }} whileTap={{ scale: reduceMotion ? 1 : 0.98 }} key={suggestion} type='button' onClick={() => handleSend(suggestion)} disabled={isSending} className='min-h-[44px] rounded border border-edge px-3.5 py-2 text-left text-[15px] hover:bg-selected disabled:opacity-50'>
										{suggestion}
									</motion.button>
								))}
							</div>
						)}
					</div>
				) : (
					<ol className='flex flex-col gap-4'>
						{messages.map((message) => (
							<motion.li initial={{ opacity: 0, y: reduceMotion ? 0 : 8 }} animate={{ opacity: 1, y: 0 }} key={message.id} className={message.role === 'user' ? 'flex flex-col items-end' : 'flex flex-col items-start'}>
								<p className={message.role === 'user' ? 'max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-selected px-3.5 py-2.5 text-[15px] leading-normal' : 'max-w-[92%] whitespace-pre-wrap text-[15px] leading-relaxed text-body'}>
									<span className='sr-only'>{message.role === 'user' ? 'You: ' : 'Assistant: '}</span>
									{message.content}
								</p>
								{message.reply && <div className='ts-assistant-evidence'>
									{message.reply.candidates.map((candidate) => <button type='button' key={candidate.id} className='ts-assistant-candidate' aria-label={`Investigate ${candidate.label}`} onClick={() => onAction({ type: 'select', id: candidate.id })}><strong>{candidate.label}</strong><span>{candidate.summary}</span></button>)}
									{message.reply.actions.map((item) => <button type='button' className='ts-assistant-action' key={item.label} onClick={() => onAction(item.action)}>{item.label}</button>)}
									{message.reply.sources.length > 0 && <details className='ts-assistant-sources'><summary>Evidence sources</summary>{message.reply.sources.map((source) => <a key={source.label} href={source.href.startsWith('/data/derived/') ? source.href : undefined} target='_blank' rel='noreferrer'>{source.label}</a>)}</details>}
								</div>}
							</motion.li>
						))}
					</ol>
				)}

				{isSending && (
					<motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} role='status' className='mt-4 text-[15px] text-muted motion-safe:animate-pulse'>
						Thinking…
					</motion.p>
				)}

				{error && (
					<div role='alert' className='mt-4 flex flex-wrap items-center gap-3'>
						<p className='text-[15px] text-loss'>{error}</p>

						<button type='button' onClick={handleRetry} className='inline-flex min-h-[44px] items-center gap-2 rounded border border-edge px-3.5 text-[15px] hover:bg-selected'>
							<RotateCcw className='size-4 shrink-0' aria-hidden='true' />
							Try again
						</button>
					</div>
				)}
			</div>

			<form onSubmit={handleSubmit} className='flex items-end gap-2 border-t border-rule px-4 py-3'>
				<label htmlFor='chat-input' className='sr-only'>
					Message
				</label>

				<textarea id='chat-input' ref={inputRef} rows={1} maxLength={4000} value={input} onChange={handleInputChange} onKeyDown={handleKeyDown} placeholder={placeholder} className='min-h-[44px] min-w-0 flex-1 resize-none rounded-xl border border-edge bg-paper px-3 py-2.5 text-[15px] leading-normal placeholder:text-muted' />

				<button type='submit' disabled={!canSend} aria-label='Send message' className='inline-flex size-11 shrink-0 items-center justify-center rounded bg-ink text-panel hover:bg-ink/90 disabled:opacity-40'>
					<ArrowUp className='size-5' aria-hidden='true' />
				</button>
			</form>
		</section>
	);
};

export { ChatPanel };
