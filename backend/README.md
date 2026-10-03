# OpenAI chatbot

The existing floating chat calls `POST /api/chat`. This Node server uses the official OpenAI SDK and Responses API. The key stays in the ignored `backend/.env.local` file and is never sent to the browser.

Run the API in one terminal:

```sh
cd backend
npm install
npm run dev
```

Run the frontend in another terminal:

```sh
cd frontend
npm install
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:3001`. Restart Vite after changing its configuration. `npm run preview` does not provide the API proxy; for deployment, route `/api/chat` to the backend on the same origin and supply `OPENAI_API_KEY` through the host's secret environment settings. The server binds to localhost for local development.

Optional backend settings: `OPENAI_MODEL` (default `gpt-5.4-mini`) and `PORT` (default `3001`; update the Vite proxy if changed). The provisioned key expires after seven days; replace it when it expires.

The assistant receives conversation history, selected clearing, radar sensitivity, and the dashboard's clearing locations. It is instructed to acknowledge placeholder measurements and dates. It has no live imagery access. API billing is separate from ChatGPT subscriptions.

Run backend checks with `npm test`.
