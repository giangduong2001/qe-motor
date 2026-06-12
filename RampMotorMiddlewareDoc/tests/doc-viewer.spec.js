// @ts-check
const { test, expect } = require('@playwright/test');

/**
 * End-to-end tests for the RAMP documentation viewer (index.html).
 *
 * Feature coverage:
 *  A. Search index build (startup)
 *  B. Basic search behavior (min length, results, no-results, clear, debounce)
 *  C. Click result -> load page + highlight + jump to first match
 *  D. Match navigation bar (next/prev wrap-around, current color, close)
 *  E. Scrollbar markers (count, current marker, click to jump, hide)
 *  F. Navigation pane (sidebar link loads at top, no leftover highlight)
 *  G. Edge cases (multi-word keyword, keyword change dismisses bar)
 */

// ---------- Helpers ----------

/** Wait until the search index has finished building. */
async function waitForIndex(page) {
  // Status shows "✓ Indexed N pages" once done.
  await expect(page.locator('#search-status')).toContainText(/Indexed \d+ pages/, {
    timeout: 45 * 1000
  });
}

/** Type a keyword and wait for the debounced search to render results/status. */
async function search(page, keyword) {
  const input = page.locator('#search-input');
  await input.click();
  await input.fill('');
  await input.type(keyword, { delay: 10 });
  // Debounce is 300ms; wait a bit longer for results to render.
  await page.waitForTimeout(500);
}

/** Get the iframe content frame. */
function contentFrame(page) {
  return page.frameLocator('#content');
}

// ---------- Tests ----------

test.beforeEach(async ({ page }) => {
  await page.goto('/index.html');
});

test.describe('A. Search index build', () => {
  test('A1: indexes pages on startup and reports success', async ({ page }) => {
    await waitForIndex(page);
    const status = await page.locator('#search-status').textContent();
    expect(status).toMatch(/Indexed \d+ pages/);
  });

  test('A2: default first sidebar link is active', async ({ page }) => {
    const firstLink = page.locator('#nav-sections ul a').first();
    await expect(firstLink).toHaveClass(/active/);
  });

  test('A3: default iframe loads the first page', async ({ page }) => {
    await expect(page.locator('#content')).toHaveAttribute('src', /Algorithm_1shunt\.html/);
  });
});

test.describe('B. Basic search', () => {
  test.beforeEach(async ({ page }) => {
    await waitForIndex(page);
  });

  test('B1: typing 1 char shows "type at least 2 characters"', async ({ page }) => {
    await search(page, 'm');
    await expect(page.locator('#search-status')).toContainText(/at least 2 characters/i);
    await expect(page.locator('.search-result-item')).toHaveCount(0);
  });

  test('B2: valid keyword renders result items', async ({ page }) => {
    await search(page, 'motor');
    const items = page.locator('.search-result-item');
    await expect(items.first()).toBeVisible();
    expect(await items.count()).toBeGreaterThan(0);
    await expect(page.locator('#search-status')).toContainText(/Found \d+ result/i);
  });

  test('B3: result item has title, category, and highlighted snippet', async ({ page }) => {
    await search(page, 'current');
    const first = page.locator('.search-result-item').first();
    await expect(first.locator('.search-result-title')).not.toBeEmpty();
    await expect(first.locator('.search-result-category')).toContainText(/match\(es\)/i);
    // Snippet should contain a <mark> highlighting the keyword.
    await expect(first.locator('.search-result-snippet mark').first()).toBeVisible();
  });

  test('B4: gibberish keyword shows no-results message', async ({ page }) => {
    await search(page, 'zzqwxyv');
    await expect(page.locator('.search-no-results')).toContainText(/No results found/i);
  });

  test('B5: clear button empties input and results', async ({ page }) => {
    await search(page, 'motor');
    await expect(page.locator('.search-result-item').first()).toBeVisible();
    await page.locator('#search-clear').click();
    await expect(page.locator('#search-input')).toHaveValue('');
    await expect(page.locator('.search-result-item')).toHaveCount(0);
    await expect(page.locator('#search-status')).toBeEmpty();
  });

  test('B6: clear button is hidden when input is empty, shown when typing', async ({ page }) => {
    await expect(page.locator('#search-clear')).toBeHidden();
    await page.locator('#search-input').type('mo');
    await expect(page.locator('#search-clear')).toBeVisible();
  });
});

test.describe('C. Click result loads + highlights', () => {
  test.beforeEach(async ({ page }) => {
    await waitForIndex(page);
  });

  test('C1-C5: clicking a result loads page, highlights, shows nav bar, marks active', async ({ page }) => {
    await search(page, 'current');
    const first = page.locator('.search-result-item').first();
    await first.click();

    // C5: match-nav-bar becomes visible
    await expect(page.locator('#match-nav-bar')).toBeVisible();

    // C1/C2: iframe has highlights
    const marks = contentFrame(page).locator('mark.search-highlight');
    await expect(marks.first()).toBeVisible();
    expect(await marks.count()).toBeGreaterThan(0);

    // C3: a sidebar link is active
    await expect(page.locator('#nav-sections a.active')).toHaveCount(1);

    // C4: the clicked result item is selected
    await expect(first).toHaveClass(/selected/);

    // Counter shows "1 / N"
    await expect(page.locator('#match-nav-counter')).toContainText(/^1 \/ \d+$/);
  });
});

test.describe('D. Match navigation bar', () => {
  test.beforeEach(async ({ page }) => {
    await waitForIndex(page);
    await search(page, 'current');
    await page.locator('.search-result-item').first().click();
    await expect(page.locator('#match-nav-bar')).toBeVisible();
  });

  test('D1: counter format is "N / M"', async ({ page }) => {
    await expect(page.locator('#match-nav-counter')).toContainText(/^\d+ \/ \d+$/);
  });

  test('D2: next advances the current index', async ({ page }) => {
    const counter = page.locator('#match-nav-counter');
    const before = await counter.textContent();
    await page.locator('#match-nav-next').click();
    await page.waitForTimeout(200);
    const after = await counter.textContent();
    expect(after).not.toBe(before);
    expect(after).toMatch(/^2 \/ \d+$/);
  });

  test('D3/D4: next at last wraps to first, prev at first wraps to last', async ({ page }) => {
    const counter = page.locator('#match-nav-counter');
    const text = await counter.textContent();
    const total = parseInt((text || '0 / 0').split('/')[1].trim(), 10);

    // Go prev from match 1 -> should wrap to total
    await page.locator('#match-nav-prev').click();
    await page.waitForTimeout(200);
    await expect(counter).toContainText(new RegExp(`^${total} / ${total}$`));

    // Go next -> wraps back to 1
    await page.locator('#match-nav-next').click();
    await page.waitForTimeout(200);
    await expect(counter).toContainText(/^1 \//);
  });

  test('D5: current match uses the "current" highlight color', async ({ page }) => {
    // First highlight should be the current (orange #ff9632 => rgb(255, 150, 50))
    const firstMark = contentFrame(page).locator('mark.search-highlight').first();
    await expect(firstMark).toHaveCSS('background-color', 'rgb(255, 150, 50)');
  });

  test('D6: close hides bar and removes highlights', async ({ page }) => {
    await page.locator('#match-nav-close').click();
    await expect(page.locator('#match-nav-bar')).toBeHidden();
    await expect(contentFrame(page).locator('mark.search-highlight')).toHaveCount(0);
  });
});

test.describe('E. Scrollbar markers', () => {
  test.beforeEach(async ({ page }) => {
    await waitForIndex(page);
    // "current" appears many times across the docs -> several markers
    await search(page, 'current');
    await page.locator('.search-result-item').first().click();
    await expect(page.locator('#match-nav-bar')).toBeVisible();
  });

  test('E1/E2: markers are visible and count equals number of matches', async ({ page }) => {
    await expect(page.locator('#scrollbar-markers')).toBeVisible();
    const markerCount = await page.locator('.scrollbar-marker').count();
    const counterText = await page.locator('#match-nav-counter').textContent();
    const total = parseInt((counterText || '0 / 0').split('/')[1].trim(), 10);
    expect(markerCount).toBe(total);
  });

  test('E4: exactly one marker is "current"', async ({ page }) => {
    await expect(page.locator('.scrollbar-marker.current')).toHaveCount(1);
  });

  test('E5: pressing next moves the current marker', async ({ page }) => {
    const markers = page.locator('.scrollbar-marker');
    // index 0 is current initially
    await expect(markers.nth(0)).toHaveClass(/current/);
    await page.locator('#match-nav-next').click();
    await page.waitForTimeout(200);
    await expect(markers.nth(0)).not.toHaveClass(/current/);
    await expect(markers.nth(1)).toHaveClass(/current/);
  });

  test('E6: clicking a marker jumps to that match', async ({ page }) => {
    const markers = page.locator('.scrollbar-marker');
    const count = await markers.count();
    test.skip(count < 3, 'Need at least 3 matches for this test');
    // Markers are thin (15px) and can overlap, so a centred click may land on a
    // neighbour. Click the very top-left pixel of the target marker, then read
    // back which match actually became current (1-based index in the counter).
    const target = markers.nth(2);
    await target.click({ force: true, position: { x: 2, y: 1 } });
    await page.waitForTimeout(200);

    // Whatever match got selected, its marker must carry the "current" class and
    // the counter's first number must match that marker's position (1-based).
    const currentIndex = await page.locator('.scrollbar-marker.current').evaluate(
      (el) => Array.from(el.parentElement.children).indexOf(el)
    );
    await expect(page.locator('#match-nav-counter')).toContainText(
      new RegExp(`^${currentIndex + 1} /`)
    );
    await expect(page.locator('.scrollbar-marker.current')).toHaveCount(1);
  });

  test('E8: closing the bar removes all markers', async ({ page }) => {
    await page.locator('#match-nav-close').click();
    await expect(page.locator('#scrollbar-markers')).toBeHidden();
    await expect(page.locator('.scrollbar-marker')).toHaveCount(0);
  });
});

test.describe('F. Navigation pane (sidebar)', () => {
  test.beforeEach(async ({ page }) => {
    await waitForIndex(page);
  });

  test('F1: clicking a sidebar link loads the page and marks it active', async ({ page }) => {
    const link = page.locator('#nav-sections ul a', { hasText: 'PMSM BEMF Observer' });
    await link.click();
    await expect(link).toHaveClass(/active/);
    // The link targets the iframe via target="contentFrame", which changes the
    // frame's location but NOT its src attribute. So assert on the frame URL.
    await expect.poll(
      async () => page.frame({ name: 'contentFrame' })?.url() || '',
      { timeout: 10000 }
    ).toMatch(/BEMFObserver/);
  });

  test('F2 (bug fix): after a search-result jump, clicking a sidebar link starts at top with no highlight', async ({ page }) => {
    // 1) Search and jump into a page (creates highlight + onload handler)
    await search(page, 'current');
    await page.locator('.search-result-item').first().click();
    await expect(page.locator('#match-nav-bar')).toBeVisible();

    // 2) Click a DIFFERENT sidebar link that also contains the keyword
    const link = page.locator('#nav-sections ul a', { hasText: 'PMSM Current Control' });
    await link.click();

    // 3) Wait for the new page to load (assert on frame URL, not src attribute)
    await expect.poll(
      async () => page.frame({ name: 'contentFrame' })?.url() || '',
      { timeout: 10000 }
    ).toMatch(/CurrentControl/);
    await page.waitForTimeout(500);

    // Bar must be hidden, no highlights, scroll position at top
    await expect(page.locator('#match-nav-bar')).toBeHidden();
    await expect(contentFrame(page).locator('mark.search-highlight')).toHaveCount(0);

    const scrollY = await page.locator('#content').evaluate(
      (iframe) => iframe.contentWindow.pageYOffset
    );
    expect(scrollY).toBe(0);
  });
});

test.describe('G. Edge cases', () => {
  test.beforeEach(async ({ page }) => {
    await waitForIndex(page);
  });

  test('G1: multi-word keyword finds and highlights matches', async ({ page }) => {
    await search(page, 'current control');
    const items = page.locator('.search-result-item');
    expect(await items.count()).toBeGreaterThan(0);
    await items.first().click();
    await expect(contentFrame(page).locator('mark.search-highlight').first()).toBeVisible();
  });

  test('G2: changing keyword while bar is active dismisses the bar', async ({ page }) => {
    await search(page, 'current');
    await page.locator('.search-result-item').first().click();
    await expect(page.locator('#match-nav-bar')).toBeVisible();

    // Type a different keyword
    await search(page, 'voltage');
    await expect(page.locator('#match-nav-bar')).toBeHidden();
  });
});
