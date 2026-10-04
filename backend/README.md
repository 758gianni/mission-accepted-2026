# TerraSignal Ask assistant

The floating dashboard assistant sends natural-language explanations to the TerraSignal API, which calls the configured OpenRouter-compatible model. Candidate filtering and map actions are evaluated locally from the loaded catalogue; only concise explanations go to the model. The API receives the grounded local answer and a small set of dataset evidence. The API key stays in the server process.

The root `.env` supplies `SWARMFORGE_MODEL_BASE_URL`, `SWARMFORGE_MODEL_API_KEY`, and `SWARMFORGE_MODEL_NAME`. Do not copy the key into frontend configuration.

Start the API in one terminal:

```sh
cd backend
npm install
npm run dev:terrasignal
```

Start the dashboard in another terminal:

```sh
cd frontend
npm install
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:3001`. The API binds to localhost for local development. If OpenRouter is unavailable, the chat falls back to the deterministic local dataset assistant. Set `TERRASIGNAL_CHAT_PORT` to change the API port and update the Vite proxy to match.

The earlier Mission Accepted demo endpoint remains available through `npm run dev` and `/api/chat`; it uses its separate `backend/.env.local` OpenAI configuration. TerraSignal uses `/api/terrasignal-chat`.

Run backend checks with `npm test` and the dashboard checks with `cd frontend && npm test`.
