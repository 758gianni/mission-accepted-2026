import { expect, test } from '@playwright/test';

test('assistant queries real candidates, applies an explicit area filter and undoes it', async ({ page }) => {
	const errors: string[] = [];
	page.on('pageerror', (error) => errors.push(error.message));
	page.on('console', (message) => { if (message.type() === 'error') errors.push(`${message.text()} ${message.location().url}`); });
	const catalogue = await (await page.request.get('/data/derived/candidate_regions.geojson')).json();
	const expected = catalogue.features.filter((feature: { properties: { class_id: number; area_ha: number } }) => feature.properties.class_id === 1 && feature.properties.area_ha > 100).length;
	await page.goto('/');
	await page.getByRole('button', { name: 'Ask TerraSignal', exact: true }).click();
	const chat = page.getByRole('dialog', { name: 'Ask TerraSignal' });
	await expect(chat.getByText('TerraSignal AI · OpenRouter', { exact: true })).toBeVisible();
	await chat.getByRole('button', { name: 'Show persistent anomalies larger than 100 ha.', exact: true }).click();
	await expect(chat.getByRole('button', { name: `Show ${expected} matches on map` })).toBeVisible();
	// Merely asking must not mutate the map.
	await expect(page.locator('.ts-map-query')).toHaveCount(0);
	await chat.getByRole('button', { name: `Show ${expected} matches on map` }).click();
	await expect(chat).not.toBeVisible();
	await expect(page.locator('.ts-map-query')).toContainText('> 100 ha');
	await expect.poll(() => page.locator('.ts-cluster-marker').evaluateAll((items) => items.reduce((count, item) => count + JSON.parse(item.getAttribute('data-candidate-ids') ?? '[]').length, 0))).toBe(expected);
	await page.getByRole('button', { name: 'Undo map action' }).click();
	await expect(page.locator('.ts-map-query')).toHaveCount(0);
	await expect.poll(() => page.locator('.ts-cluster-marker').evaluateAll((items) => items.reduce((count, item) => count + JSON.parse(item.getAttribute('data-candidate-ids') ?? '[]').length, 0))).toBe(catalogue.features.length);
	expect(errors).toEqual([]);
});

test('assistant references select real anomalies with reversible camera state and retain unverified wording', async ({ page }) => {
	await page.route('**/api/terrasignal-chat', async (route) => {
		const body = route.request().postDataJSON();
		await route.fulfill({ json: { reply: body.evidence.groundedAnswer } });
	});
	await page.goto('/');
	await page.getByRole('button', { name: 'Ask TerraSignal', exact: true }).click();
	const chat = page.getByRole('dialog', { name: 'Ask TerraSignal' });
	await chat.getByRole('button', { name: 'What evidence supports the possible new water body interpretation?', exact: true }).click();
	await expect(chat.getByText(/Unverified interpretation — Possible new water body/)).toBeVisible();
	await chat.getByText('Evidence sources', { exact: true }).click();
	await expect(chat.getByRole('link', { name: /Project note/ })).toBeVisible();
	await chat.getByRole('button', { name: 'Investigate Region 2' }).click();
	await expect(page.getByLabel('Investigation for region 2')).toBeVisible();
	await expect.poll(async () => Number(await page.getByTestId('map-viewport').getAttribute('data-map-zoom'))).toBeGreaterThan(10);
	await page.getByRole('button', { name: 'Undo map action' }).click();
	await expect(page.getByLabel('Ranked anomaly queue')).toBeVisible();
	await expect.poll(() => page.getByTestId('map-viewport').getAttribute('data-map-center')).toBe('700,450');
	await page.getByRole('button', { name: 'Ask TerraSignal', exact: true }).click();
	await chat.getByRole('textbox', { name: 'Message', exact: true }).fill('How much data was reduced?');
	await chat.getByRole('button', { name: 'Send message' }).click();
	await expect(chat.getByText(/Measured reduction: 162.9×/)).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(chat).not.toBeVisible();
});
