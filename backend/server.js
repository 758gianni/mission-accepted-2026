import OpenAI from 'openai';
import { createApp } from './chat.js';

if (!process.env.OPENAI_API_KEY) {
	console.error('OPENAI_API_KEY is missing. Configure backend/.env.local before starting the server.');
	process.exit(1);
}

const client = new OpenAI({
	timeout: 45_000,
	maxRetries: 1,
});

const port = Number(process.env.PORT ?? 3001);

createApp(client, process.env.OPENAI_MODEL).listen(port, '127.0.0.1', () => {
	console.log(`Chat API ready at http://127.0.0.1:${port}`);
});
