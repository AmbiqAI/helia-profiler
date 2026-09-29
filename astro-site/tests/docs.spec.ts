import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function contentRoutes(directory = path.resolve('dist'), prefix = ''): string[] {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) return contentRoutes(file, `${prefix}${entry.name}/`);
    return entry.name === 'index.html' && fs.readFileSync(file, 'utf8').includes('sl-markdown-content')
      ? [prefix]
      : [];
  });
}

for (const [width, theme] of [[390, 'light'], [1029, 'dark']] as const) {
  test(`content routes fit at ${width}px in ${theme} mode`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.setViewportSize({ width, height: 889 });
    await page.addInitScript((theme) => localStorage.setItem('starlight-theme', theme), theme);
    for (const route of contentRoutes()) {
      await page.goto(route || './');
      await expect(page.locator('h1'), route).toHaveCount(1);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), route).toBe(true);
    }
    expect(errors).toEqual([]);
  });
}

test('example filters combine, clear and expose an empty state', async ({ page }) => {
  await page.goto('examples/');
  const browser = page.getByRole('region', { name: 'Search examples', exact: true });
  await expect(browser).toHaveAttribute('data-ready', 'true');
  await browser.getByLabel('Task', { exact: true }).selectOption('Power');
  await browser.getByLabel('Setup', { exact: true }).selectOption('Saved results');
  await expect(browser.locator('tbody tr')).toHaveCount(2);
  await browser.getByRole('searchbox').fill('no-such-example');
  await expect(browser).toContainText('No matching examples');
  await browser.getByRole('button', { name: /clear|reset/i }).first().click();
  await expect(browser.locator('tbody tr')).toHaveCount(13);
  await browser.getByRole('searchbox').fill('FPGA');
  await expect(browser.getByRole('link', { name: 'Atomiq110 NPU profiling' })).toBeVisible();
});

test('examples remain navigable without JavaScript', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  await page.goto('http://127.0.0.1:8874/helia-profiler/examples/');
  await expect(page.locator('.helia-reference-browser tbody tr')).toHaveCount(13);
  await expect(page.locator('.helia-reference-browser').getByRole('link', { name: 'Compare engines' })).toBeVisible();
  await context.close();
});

test('installation tabs are keyboard accessible', async ({ page }) => {
  await page.goto('getting-started/install/');
  const tabs = page.getByRole('tablist').first();
  await tabs.getByRole('tab', { name: 'uv', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(tabs.getByRole('tab', { name: 'pip', exact: true })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(tabs.getByRole('tab', { name: 'pip', exact: true })).toHaveAttribute('aria-selected', 'true');
});

test('site search finds the measurement guide', async ({ page }) => {
  await page.goto('./');
  await page.getByRole('button', { name: /search/i }).first().click();
  const dialog = page.getByRole('dialog');
  await dialog.locator('input').fill('measurement windows');
  await expect(dialog.locator('a[href="/helia-profiler/guide/power-windows/"]')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
});

test('host OS tabs stay synchronized without changing installer choice', async ({ page }) => {
  await page.goto('getting-started/install/');
  await page.getByRole('tab', { name: 'macOS', exact: true }).first().click();
  await expect(page.locator('[data-sync-key="hpx-host-os"] [role="tab"][aria-selected="true"]')).toHaveText(['macOS', 'macOS', 'macOS']);
  await expect(page.getByRole('tab', { name: 'uv', exact: true })).toHaveAttribute('aria-selected', 'true');
});

test('setup terminal retains output, replays, and copies only its command', async ({ page, context }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('getting-started/validate-your-setup/');
  const terminal = page.locator('helia-ascii-terminal').first();
  await terminal.scrollIntoViewIfNeeded();
  await expect(terminal.locator('[data-line-text]').last()).toHaveText('  All required tools found.');
  await expect(terminal).not.toHaveAttribute('data-playing', 'true');
  await terminal.getByRole('button', { name: 'Copy commands', exact: true }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('hpx doctor');
  await terminal.locator('[data-replay-button]').click();
  await expect(terminal).toHaveAttribute('data-playing', 'true');
  await expect(terminal).not.toHaveAttribute('data-playing', 'true', { timeout: 10000 });
  await expect(terminal.locator('[data-line-text]').last()).toHaveText('  All required tools found.');
});
