import { expect, test } from '@playwright/test';

test('selected candidate supports chronological scrubbing, linked thumbnails and paused keyboard stepping', async ({ page }) => {
	const errors: string[] = [];
	page.on('pageerror', (error) => errors.push(error.message));
	page.on('console', (message) => { if (message.type() === 'error') errors.push(`${message.text()} ${message.location().url}`); });
	await page.goto('/');
	await page.locator('.ts-queue-item').first().click();
	const viewer = page.getByRole('region', { name: 'Temporal evidence viewer' });
	await expect(viewer).toBeVisible();
	await expect(viewer.getByTestId('temporal-date')).toHaveText('Apr 18, 2024');
	await expect(viewer.getByTestId('temporal-sensor')).toHaveText('RADARSAT-2');
	const scrubber = viewer.getByRole('slider', { name: 'Acquisition timeline' });
	await scrubber.fill('1');
	await expect(viewer.getByTestId('temporal-date')).toHaveText('Nov 20, 2024');
	await page.getByRole('button', { name: 'View acquisition Dec 21, 2024' }).click();
	await expect(scrubber).toHaveValue('2');
	await expect(viewer.getByTestId('temporal-role')).toHaveText('Independent support');
	await viewer.focus();
	await page.keyboard.press('ArrowRight');
	await expect(viewer.getByTestId('temporal-date')).toHaveText('Jan 07, 2025');
	await page.keyboard.press('ArrowRight');
	await expect(scrubber).toHaveValue('3');
	await page.keyboard.press('ArrowLeft');
	await expect(scrubber).toHaveValue('2');
	await expect(viewer.getByRole('button', { name: 'Play temporal sequence' })).toBeVisible();
	const crops = await page.locator('[data-temporal-crop]').evaluateAll((items) => items.map((item) => item.getAttribute('viewBox')));
	expect(crops.length).toBeGreaterThan(4);
	expect(new Set(crops).size).toBe(1);
	await viewer.getByRole('checkbox', { name: 'Candidate outline' }).uncheck();
	await expect(viewer.locator('.ts-temporal-frame path')).toHaveCount(0);
	expect(errors).toEqual([]);
	const filmstrip = page.locator('.ts-filmstrip');
	expect(await filmstrip.evaluate((element) => element.getBoundingClientRect().right <= window.innerWidth)).toBeTruthy();
});

test('playback advances, loops, pauses, and resets when a different candidate is opened', async ({ page }) => {
	await page.goto('/');
	await page.locator('.ts-queue-item').first().click();
	const viewer = page.getByRole('region', { name: 'Temporal evidence viewer' });
	await expect(viewer.locator('.ts-temporal-frame.is-active')).toHaveAttribute('data-frame-status', 'ready');
	await page.clock.install();
	const scrubber = viewer.getByRole('slider', { name: 'Acquisition timeline' });
	await viewer.getByRole('combobox', { name: 'Playback speed' }).selectOption('1200');
	await viewer.getByRole('button', { name: 'Play temporal sequence' }).click();
	await page.clock.fastForward(1200);
	await expect(scrubber).toHaveValue('1');
	await viewer.getByRole('button', { name: 'Pause temporal sequence' }).click();
	await page.clock.fastForward(5000);
	await expect(scrubber).toHaveValue('1');
	await scrubber.fill('3');
	await viewer.getByRole('button', { name: 'Play temporal sequence' }).click();
	await page.clock.fastForward(1200);
	await expect(scrubber).toHaveValue('0');
	await page.getByRole('button', { name: 'Back to queue' }).click();
	await page.locator('.ts-queue-item').nth(1).click();
	await expect(scrubber).toHaveValue('0');
	await expect(viewer.getByRole('button', { name: 'Play temporal sequence' })).toBeVisible();
});

test('N acquisitions sort by date and missing or unreadable imagery does not show a stale frame', async ({ page }) => {
	await page.route('**/data/derived/contract.json', async (route) => {
		const response = await route.fetch();
		const contract = await response.json();
		const base = contract.acquisitions[0];
		contract.acquisitions = [7, 2, 6, 1, 4, 3, 5].map((month) => ({
			...base, id: `test-${month}`, date: `2024-0${month}-01`, iso: undefined,
			image: month === 3 ? null : month === 5 ? 'derived/unreadable.png' : base.image,
		}));
		await route.fulfill({ json: contract });
	});
	await page.route('**/data/derived/unreadable.png', (route) => route.fulfill({ contentType: 'image/png', body: 'not an image' }));
	await page.goto('/');
	await page.locator('.ts-queue-item').first().click();
	const viewer = page.getByRole('region', { name: 'Temporal evidence viewer' });
	await expect(viewer.getByTestId('temporal-date')).toHaveText('Jan 01, 2024');
	await expect(page.locator('.ts-film-card')).toHaveCount(7);
	const scrubber = viewer.getByRole('slider', { name: 'Acquisition timeline' });
	await expect(scrubber).toHaveAttribute('max', '6');
	await scrubber.fill('2');
	await expect(viewer.getByText('No imagery available for this acquisition.')).toBeVisible();
	await expect(viewer.locator('.ts-temporal-frame.is-active [data-temporal-crop]')).toHaveCount(0);
	await scrubber.fill('4');
	await expect(viewer.getByText('This acquisition image could not be loaded.')).toBeVisible();
	await scrubber.fill('6');
	await expect(viewer.getByTestId('temporal-date')).toHaveText('Jul 01, 2024');
	await expect(viewer.locator('.ts-temporal-frame.is-active')).toHaveAttribute('data-frame-status', 'ready');
});

for (const count of [0, 1]) {
	test(`handles ${count} acquisitions without starting a playback timer`, async ({ page }) => {
		await page.route('**/data/derived/contract.json', async (route) => {
			const response = await route.fetch();
			const contract = await response.json();
			contract.acquisitions = contract.acquisitions.slice(0, count);
			await route.fulfill({ json: contract });
		});
		await page.goto('/');
		await page.locator('.ts-queue-item').first().click();
		const viewer = page.getByRole('region', { name: 'Temporal evidence viewer' });
		await expect(viewer).toBeVisible();
		if (count === 0) {
			await expect(viewer.getByText('No acquisition imagery has been supplied for this candidate.')).toBeVisible();
			await expect(viewer.getByRole('button', { name: 'Play temporal sequence' })).toHaveCount(0);
		} else {
			await expect(viewer.getByRole('button', { name: 'Play temporal sequence' })).toBeDisabled();
			await expect(viewer.getByRole('slider', { name: 'Acquisition timeline' })).toBeDisabled();
		}
	});
}
