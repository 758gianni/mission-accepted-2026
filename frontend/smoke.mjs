import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';

const BASE = process.env.DASH_URL ?? 'http://127.0.0.1:5199';
const OUT = '/tmp/dash-shots';
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
await cdp.send('Network.enable');
await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });

const errors = [];
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
page.on('console', (m) => {
	if (m.type() === 'error') errors.push(`console: ${m.text()}`);
});

await page.goto(BASE, { waitUntil: 'networkidle' });

// app must load the contract and render the map
await page.waitForSelector('text=RADARSAT-2 change candidates', { timeout: 15000 });
await page.waitForSelector('.leaflet-container', { timeout: 15000 });
await page.waitForSelector('text=Presentation path', { timeout: 15000 });

const checks = [];
const has = async (name, sel) => {
	const n = await page.locator(sel).count();
	checks.push({ name, ok: n > 0, count: n });
};

await has('map', '.leaflet-container');
await has('temporal class filters', 'text=Temporal class');
await has('layer toggles', 'text=Temporal RGB');
await has('terrain QA layer', 'text=Terrain QA');
await has('comparison strip', 'text=T1 / T2 / T3 / T4 comparison');
await has('timeline', 'text=Acquisition timeline');
await has('presenter', 'text=Presentation path');
await has('seasonal class', 'text=Seasonal / transient');
await has('persistent class', 'text=Persistent');
await has('t4 class', 'text=T4-validated persistent');

// T4 validation must be reflected in the live contract
const t4validated = await page.evaluate(async () => {
	const res = await fetch('/data/derived/contract.json');
	const c = await res.json();
	return {
		t4_available: c.t4?.available ?? false,
		t4_validated: c.counts?.t4_validated_persistent ?? 0,
		has_t4_acquisition: (c.acquisitions ?? []).some((a) => a.id === 'T4'),
		t4_comparison_ready: (c.comparisons ?? []).some((s) => s.id === 'T4' && s.available),
	};
});
checks.push({ name: 't4 available', ok: t4validated.t4_available, count: t4validated.t4_available ? 1 : 0 });
checks.push({ name: 't4_validated count > 0', ok: t4validated.t4_validated > 0, count: t4validated.t4_validated });
checks.push({ name: 'T4 acquisition on timeline', ok: t4validated.has_t4_acquisition, count: t4validated.has_t4_acquisition ? 1 : 0 });
checks.push({ name: 'T4 comparison slot ready', ok: t4validated.t4_comparison_ready, count: t4validated.t4_comparison_ready ? 1 : 0 });

// header must show the live T4-validated count
const headerT4 = await page.evaluate(() => {
	const labels = Array.from(document.querySelectorAll('span'));
	const hit = labels.find((s) => s.textContent?.trim().toLowerCase() === 't4-validated');
	return hit?.parentElement?.querySelector('.tabular-nums')?.textContent?.trim() ?? null;
});
checks.push({
	name: 'header shows T4-validated > 0',
	ok: headerT4 != null && Number(headerT4) > 0,
	count: Number(headerT4 ?? 0),
	headerT4,
});

// header counts
const seasonalCount = await page.locator('text=Seasonal').first().locator('xpath=../span').textContent().catch(() => null);

await page.screenshot({ path: `${OUT}/01-overview.png`, fullPage: true });

// step through the presentation path
const stepTitles = ['Seasonal river signal', 'Persistent anomalies', 'Region 2', 'Terrain QA', 'T4 validation'];
for (let i = 0; i < stepTitles.length; i++) {
	const btn = page.locator(`button:has-text("${stepTitles[i]}")`).first();
	await btn.click();
	await page.waitForTimeout(600);
	await page.screenshot({ path: `${OUT}/0${i + 2}-${stepTitles[i].toLowerCase().replace(/[^a-z0-9]+/g, '-')}.png`, fullPage: true });
}

// select Region 2 and read inspector values
await page.locator('button:has-text("Region 2")').first().click();
await page.waitForTimeout(500);
const inspector = await page.locator('text=Region 2').first().isVisible();
checks.push({ name: 'inspector shows Region 2', ok: inspector, count: inspector ? 1 : 0 });

const bodyText = await page.locator('body').innerText();
const lower = bodyText.toLowerCase();
const expects = [
	['area ha', /\d+\.\d+ ha/],
	['db change', /[+-]\d+\.\d+\s*db/],
	['terrain risk', /terrain risk/],
	['priority score', /priority score/],
	['temporal class', /temporal class/],
];
for (const [name, re] of expects) {
	const ok = re.test(lower);
	checks.push({ name, ok, count: ok ? 1 : 0 });
}

// comparison strip images must actually decode
const brokenImgs = await page.evaluate(() =>
	Array.from(document.querySelectorAll('img'))
		.filter((img) => !img.complete || img.naturalWidth === 0)
		.map((img) => img.getAttribute('src')),
);
checks.push({ name: 'all images decoded', ok: brokenImgs.length === 0, count: brokenImgs.length, brokenImgs });

await browser.close();

const failed = checks.filter((c) => !c.ok);
console.log(JSON.stringify({ checks, errors, seasonalCount, failed: failed.length }, null, 2));
if (failed.length || errors.length) {
	console.error('SMOKE FAILED');
	process.exit(1);
}
console.log('SMOKE OK');
