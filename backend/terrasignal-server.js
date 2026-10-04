import OpenAI from 'openai';
import { createTerraSignalApp } from './terrasignal-chat.js';

const apiKey = process.env.SWARMFORGE_MODEL_API_KEY;
const baseURL = process.env.SWARMFORGE_MODEL_BASE_URL;
const model = process.env.SWARMFORGE_MODEL_NAME;
if (!apiKey || !baseURL || !model) {
	console.error('OpenRouter chat configuration is incomplete. Set SWARMFORGE_MODEL_BASE_URL, SWARMFORGE_MODEL_API_KEY and SWARMFORGE_MODEL_NAME in the root .env file.');
	process.exit(1);
}

const client = new OpenAI({
	apiKey,
	baseURL,
	timeout: 45_000,
	maxRetries: 1,
	defaultHeaders: { 'X-Title': 'TerraSignal' },
});
const port = Number(process.env.TERRASIGNAL_CHAT_PORT ?? 3001);
createTerraSignalApp(client, model).listen(port, '127.0.0.1', () => {
	console.log(`TerraSignal chat API ready at http://127.0.0.1:${port}`);
});
