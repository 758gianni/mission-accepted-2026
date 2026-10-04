import { expect, test } from '@playwright/test';

test('supports drag pan and wheel, button, and double-click zoom', async ({ page }) => {
	await page.goto('/');
	const map = page.getByTestId('map-viewport');
	await expect(map).toBeVisible();
	const centerBefore = await map.getAttribute('data-map-center');

	const box = await map.boundingBox();
	if (!box) throw new Error('Map viewport has no visible bounds');
	await page.mouse.move(box.x + box.width * 0.76, box.y + box.height * 0.54);
	await page.mouse.down();
	await page.mouse.move(box.x + box.width * 0.68, box.y + box.height * 0.61, { steps: 8 });
	await page.mouse.up();
	await expect.poll(() => map.getAttribute('data-map-center')).not.toBe(centerBefore);

	const zoomBeforeWheel = Number(await map.getAttribute('data-map-zoom'));
	await page.mouse.move(box.x + box.width * 0.72, box.y + box.height * 0.52);
	await page.mouse.wheel(0, -420);
	await expect.poll(async () => Number(await map.getAttribute('data-map-zoom'))).toBeGreaterThan(zoomBeforeWheel);

	const zoomBeforeButton = Number(await map.getAttribute('data-map-zoom'));
	await page.getByRole('button', { name: 'Zoom in' }).click();
	await expect.poll(async () => Number(await map.getAttribute('data-map-zoom'))).toBeGreaterThan(zoomBeforeButton);

	const zoomBeforeDoubleClick = Number(await map.getAttribute('data-map-zoom'));
	await page.mouse.dblclick(box.x + box.width * 0.52, box.y + box.height * 0.48);
	await expect.poll(async () => Number(await map.getAttribute('data-map-zoom'))).toBeGreaterThan(zoomBeforeDoubleClick);
});

test('candidate selection remains available after panning and return restores regional overview', async ({ page }) => {
	await page.goto('/');
	const map = page.getByTestId('map-viewport');
	const box = await map.boundingBox();
	if (!box) throw new Error('Map viewport has no visible bounds');
	await page.mouse.move(box.x + box.width * 0.76, box.y + box.height * 0.5);
	await page.mouse.down();
	await page.mouse.move(box.x + box.width * 0.66, box.y + box.height * 0.57, { steps: 8 });
	await page.mouse.up();

	const pannedCenter = await map.getAttribute('data-map-center');
	await page.locator('.ts-queue-item').first().click();
	await expect(page.getByLabel(/^Investigation for region/)).toBeVisible();
	const selectedCenter = await map.getAttribute('data-map-center');
	await page.mouse.move(box.x + box.width * 0.72, box.y + box.height * 0.52);
	await page.mouse.down();
	await page.mouse.move(box.x + box.width * 0.65, box.y + box.height * 0.58, { steps: 6 });
	await page.mouse.up();
	await expect.poll(() => map.getAttribute('data-map-center')).not.toBe(selectedCenter);

	await page.getByRole('button', { name: 'Back to queue' }).click();
	await expect(page.getByRole('complementary', { name: 'Ranked anomaly queue' })).toBeVisible();
	await expect(page.getByText('Region 2', { exact: true })).toBeVisible();
	await expect.poll(() => map.getAttribute('data-map-center')).toBe('700,450');
	await expect.poll(async () => Number(await map.getAttribute('data-map-zoom'))).toBe(1);
	await expect(map.getAttribute('data-map-center')).not.toBe(pannedCenter);
});

test('renders every georeferenced processing tile and uses configured AOI metadata', async ({ page, request }) => {
	const contractResponse = await request.get('/data/derived/contract.json');
	expect(contractResponse.ok()).toBeTruthy();
	const contract = await contractResponse.json();
	const candidateResponse = await request.get('/data/derived/candidate_regions.geojson');
	const candidateData = await candidateResponse.json();
	expect(contract.tiles.length).toBeGreaterThan(1);
	expect(contract.tile_candidate_support.temporally_classifiable).toBeGreaterThan(0);
	for (const edge of ['west', 'south', 'east', 'north'] as const) {
		if (edge === 'west' || edge === 'south') expect(contract.map_bounds[edge]).toBeLessThanOrEqual(contract.image_bounds[edge]);
		else expect(contract.map_bounds[edge]).toBeGreaterThanOrEqual(contract.image_bounds[edge]);
	}
	await page.goto('/');
	await expect(page.getByText(contract.scene.name, { exact: true })).toBeVisible();
	await expect(page.getByText(/coastal/i)).toHaveCount(0);
	const metrics = page.getByRole('region', { name: 'AOI, coverage, and storage reduction metrics' });
	await expect(metrics.getByText(`${Math.round(contract.tile_processing.analysis_valid_area_km2).toLocaleString()} km²`)).toBeVisible();
	await expect(metrics.getByText(`${Math.round(contract.tile_processing.satellite_coverage_area_km2).toLocaleString()} km²`)).toBeVisible();
	await expect(metrics.getByText('Not recorded')).toBeVisible();
	const tiles = page.locator('.ts-map-tile');
	await expect(tiles).toHaveCount(contract.tiles.length);
	const mappedIds = await page.locator('.ts-candidate-point').evaluateAll((items) => items.map((item) => item.getAttribute('data-candidate-id')));
	expect(mappedIds.length).toBe(candidateData.features.length);
	await expect(page.locator('.ts-context-land').first()).toBeAttached();
	await expect(page.getByText('© OpenStreetMap contributors', { exact: false })).toBeVisible();
	const mapCount = async () => page.locator('.ts-candidate-point').count();
	const filters = page.getByRole('navigation', { name: 'Filter anomaly classes' });
	await filters.getByRole('button', { name: /^Seasonal/ }).click();
	await expect.poll(mapCount).toBe(440);
	await filters.getByRole('button', { name: /^Persistent/ }).click();
	await expect.poll(mapCount).toBe(80);
	await filters.getByRole('button', { name: /^Independent support/ }).click();
	await expect.poll(mapCount).toBe(11);
	await filters.getByRole('button', { name: /^All signals/ }).click();
	await expect.poll(mapCount).toBe(525);
	const ids = await tiles.evaluateAll((items) => items.map((item) => item.getAttribute('data-tile-id')));
	expect(new Set(ids).size).toBe(contract.tiles.length);
	const placement = await tiles.evaluateAll((items) => items.map((item) => ({
		transform: item.getAttribute('transform'),
		bounds: JSON.parse(item.getAttribute('data-display-bounds') ?? '{}'),
		asset: item.querySelector('image')?.getAttribute('href'),
		imageWidth: Number(item.querySelector('image')?.getAttribute('width')),
		imageHeight: Number(item.querySelector('image')?.getAttribute('height')),
	})));
	expect(new Set(placement.map((item) => item.transform)).size).toBeGreaterThan(1);
	for (let index = 0; index < placement.length; index++) {
		const tile = contract.tiles[index];
		const tilePlacement = placement.find((item) => item.bounds.west === tile.display_bounds.west && item.bounds.north === tile.display_bounds.north);
		expect(tilePlacement, `tile ${tile.tile_id} has a map placement`).toBeTruthy();
		expect([tilePlacement!.imageWidth, tilePlacement!.imageHeight]).toEqual([tile.preview_width, tile.preview_height]);
		const [a, b, c, d, e, f] = tilePlacement!.transform!.match(/matrix\(([^)]+)\)/)![1].split(/\s+/).map(Number);
		const mapBounds = contract.map_bounds;
		const cosine = Math.cos((mapBounds.north + mapBounds.south) * Math.PI / 360);
		const spanX = (mapBounds.east - mapBounds.west) * cosine;
		const spanY = mapBounds.north - mapBounds.south;
		const mapScale = Math.min(1400 / spanX, 900 / spanY);
		const offsetX = (1400 - spanX * mapScale) / 2;
		const offsetY = (900 - spanY * mapScale) / 2;
		const project = (longitude: number, latitude: number) => [offsetX + (longitude - mapBounds.west) * cosine * mapScale, offsetY + (mapBounds.north - latitude) * mapScale];
		const [expectedX, expectedY] = project(tile.display_bounds.west, tile.display_bounds.north);
		const [eastX] = project(tile.display_bounds.east, tile.display_bounds.north);
		const [, southY] = project(tile.display_bounds.west, tile.display_bounds.south);
		const expectedWidth = (eastX - expectedX) / tile.preview_width;
		const expectedHeight = (southY - expectedY) / tile.preview_height;
		expect([a, b, c, d, e, f]).toEqual([expect.closeTo(expectedWidth, 2), 0, 0, expect.closeTo(expectedHeight, 2), expect.closeTo(expectedX, 1), expect.closeTo(expectedY, 1)]);
		const response = await request.get(tilePlacement!.asset!);
		expect(response.ok(), `tile asset ${tilePlacement!.asset} loads`).toBeTruthy();
	}
	await expect(page.getByText(/14 source acquisitions/)).toBeVisible();
	await expect(page.getByText(/40 tiles · 14 acquisitions · 6 dates/).first()).toBeVisible();
	await page.getByRole('button', { name: 'Observation coverage' }).click();
	await expect.poll(() => page.locator('.ts-map-tile image').first().getAttribute('href'))
		.toMatch(/-coverage\.png$/);
	await expect.poll(mapCount).toBe(525);
});

for (const viewport of [{ width: 1440, height: 900 }, { width: 1920, height: 1080 }, { width: 2560, height: 1440 }]) {
	test(`top-level metrics do not overlap at ${viewport.width}×${viewport.height}`, async ({ page }) => {
		await page.setViewportSize(viewport);
		await page.goto('/');
		const flow = page.getByTestId('data-reduction');
		await expect(flow).toBeVisible();
		const values = await flow.locator('[data-metric-value]').evaluateAll((items) => items.map((item) => {
			const rect = item.getBoundingClientRect();
			return { x: rect.x, y: rect.y, right: rect.right, bottom: rect.bottom };
		}));
		for (let i = 0; i < values.length; i++) for (let j = i + 1; j < values.length; j++) {
			const overlaps = values[i].x < values[j].right && values[i].right > values[j].x && values[i].y < values[j].bottom && values[i].bottom > values[j].y;
			expect(overlaps, `metric values ${i} and ${j} overlap`).toBeFalsy();
		}
	});
}

test('loads the overview without browser console or page errors', async ({ page }) => {
	const errors: string[] = [];
	page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
	page.on('pageerror', (error) => errors.push(error.message));
	await page.goto('/');
	await expect(page.getByRole('heading', { name: 'TerraSignal', exact: true })).toBeVisible();
	await expect(page.getByTestId('map-viewport')).toBeVisible();
	await expect.poll(() => page.locator('.ts-map-tile').count()).toBeGreaterThan(1);
	await page.waitForTimeout(250);
	expect(errors).toEqual([]);
});

test('judge flow preserves detections through clustering and opens the investigation hypothesis', async ({ page }) => {
	await page.goto('/');
	await expect(page.getByRole('region', { name: 'Candidate reduction funnel' })).toBeInViewport();
	await expect(page.locator('.ts-candidate-point')).toHaveCount(525);
	await page.getByRole('button', { name: /Individual detections/ }).click();
	const ids = await page.locator('.ts-cluster-marker').evaluateAll((items) => items.flatMap((item) => JSON.parse(item.getAttribute('data-candidate-ids') ?? '[]')));
	expect(new Set(ids).size).toBe(525);
	await page.getByRole('button', { name: /Clustered detections/ }).click();
	await expect(page.locator('.ts-candidate-point')).toHaveCount(525);
	await page.getByRole('button', { name: /Guided walkthrough/ }).click();
	await page.getByRole('button', { name: 'Go to Independently supported', exact: true }).click();
	await expect(page.locator('.ts-candidate-point')).toHaveCount(11);
	await page.getByRole('button', { name: 'Go to Investigate', exact: true }).click();
	const investigation = page.getByLabel(/^Investigation for region/);
	await expect(investigation).toBeVisible();
	await expect(investigation.getByText('Possible new water body', { exact: true })).toBeVisible();
	await expect(investigation.getByText('INVESTIGATION HYPOTHESIS · UNVERIFIED', { exact: true })).toBeVisible();
	const contract = await (await page.request.get('/data/derived/contract.json')).json();
	await expect(page.locator('.ts-map-evidence .ts-film-card')).toHaveCount(contract.acquisitions.length);
});
