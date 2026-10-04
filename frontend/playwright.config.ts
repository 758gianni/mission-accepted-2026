import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
	testDir: './e2e',
	fullyParallel: false,
	reporter: 'list',
	use: {
		baseURL: 'http://127.0.0.1:53147',
		browserName: 'chromium',
		launchOptions: { executablePath: '/home/overlord/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome' },
		...devices['Desktop Chrome'],
		viewport: { width: 1920, height: 1080 },
	},
	webServer: {
		command: 'npm run dev -- --host 127.0.0.1 --port 53147 --strictPort',
		url: 'http://127.0.0.1:53147',
		reuseExistingServer: false,
		timeout: 30_000,
	},
});
