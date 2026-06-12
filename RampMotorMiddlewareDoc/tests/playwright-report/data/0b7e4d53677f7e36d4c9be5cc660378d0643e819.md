# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: doc-viewer.spec.js >> D. Match navigation bar >> D5: current match uses the "current" highlight color
- Location: doc-viewer.spec.js:181:3

# Error details

```
Error: expect(locator).toHaveCSS(expected) failed

Locator:  locator('#content').contentFrame().locator('mark.search-highlight').first()
Expected: "rgb(255, 150, 50)"
Received: "rgb(255, 209, 102)"
Timeout:  10000ms

Call log:
  - Expect "toHaveCSS" with timeout 10000ms
  - waiting for locator('#content').contentFrame().locator('mark.search-highlight').first()
    23 × locator resolved to <mark class="search-highlight">Current</mark>
       - unexpected value "rgb(255, 209, 102)"

```

```yaml
- mark: Current
```

# Test source

```ts
  84  |   test('B3: result item has title, category, and highlighted snippet', async ({ page }) => {
  85  |     await search(page, 'current');
  86  |     const first = page.locator('.search-result-item').first();
  87  |     await expect(first.locator('.search-result-title')).not.toBeEmpty();
  88  |     await expect(first.locator('.search-result-category')).toContainText(/match\(es\)/i);
  89  |     // Snippet should contain a <mark> highlighting the keyword.
  90  |     await expect(first.locator('.search-result-snippet mark').first()).toBeVisible();
  91  |   });
  92  | 
  93  |   test('B4: gibberish keyword shows no-results message', async ({ page }) => {
  94  |     await search(page, 'zzqwxyv');
  95  |     await expect(page.locator('.search-no-results')).toContainText(/No results found/i);
  96  |   });
  97  | 
  98  |   test('B5: clear button empties input and results', async ({ page }) => {
  99  |     await search(page, 'motor');
  100 |     await expect(page.locator('.search-result-item').first()).toBeVisible();
  101 |     await page.locator('#search-clear').click();
  102 |     await expect(page.locator('#search-input')).toHaveValue('');
  103 |     await expect(page.locator('.search-result-item')).toHaveCount(0);
  104 |     await expect(page.locator('#search-status')).toBeEmpty();
  105 |   });
  106 | 
  107 |   test('B6: clear button is hidden when input is empty, shown when typing', async ({ page }) => {
  108 |     await expect(page.locator('#search-clear')).toBeHidden();
  109 |     await page.locator('#search-input').type('mo');
  110 |     await expect(page.locator('#search-clear')).toBeVisible();
  111 |   });
  112 | });
  113 | 
  114 | test.describe('C. Click result loads + highlights', () => {
  115 |   test.beforeEach(async ({ page }) => {
  116 |     await waitForIndex(page);
  117 |   });
  118 | 
  119 |   test('C1-C5: clicking a result loads page, highlights, shows nav bar, marks active', async ({ page }) => {
  120 |     await search(page, 'current');
  121 |     const first = page.locator('.search-result-item').first();
  122 |     await first.click();
  123 | 
  124 |     // C5: match-nav-bar becomes visible
  125 |     await expect(page.locator('#match-nav-bar')).toBeVisible();
  126 | 
  127 |     // C1/C2: iframe has highlights
  128 |     const marks = contentFrame(page).locator('mark.search-highlight');
  129 |     await expect(marks.first()).toBeVisible();
  130 |     expect(await marks.count()).toBeGreaterThan(0);
  131 | 
  132 |     // C3: a sidebar link is active
  133 |     await expect(page.locator('#nav-sections a.active')).toHaveCount(1);
  134 | 
  135 |     // C4: the clicked result item is selected
  136 |     await expect(first).toHaveClass(/selected/);
  137 | 
  138 |     // Counter shows "1 / N"
  139 |     await expect(page.locator('#match-nav-counter')).toContainText(/^1 \/ \d+$/);
  140 |   });
  141 | });
  142 | 
  143 | test.describe('D. Match navigation bar', () => {
  144 |   test.beforeEach(async ({ page }) => {
  145 |     await waitForIndex(page);
  146 |     await search(page, 'current');
  147 |     await page.locator('.search-result-item').first().click();
  148 |     await expect(page.locator('#match-nav-bar')).toBeVisible();
  149 |   });
  150 | 
  151 |   test('D1: counter format is "N / M"', async ({ page }) => {
  152 |     await expect(page.locator('#match-nav-counter')).toContainText(/^\d+ \/ \d+$/);
  153 |   });
  154 | 
  155 |   test('D2: next advances the current index', async ({ page }) => {
  156 |     const counter = page.locator('#match-nav-counter');
  157 |     const before = await counter.textContent();
  158 |     await page.locator('#match-nav-next').click();
  159 |     await page.waitForTimeout(200);
  160 |     const after = await counter.textContent();
  161 |     expect(after).not.toBe(before);
  162 |     expect(after).toMatch(/^2 \/ \d+$/);
  163 |   });
  164 | 
  165 |   test('D3/D4: next at last wraps to first, prev at first wraps to last', async ({ page }) => {
  166 |     const counter = page.locator('#match-nav-counter');
  167 |     const text = await counter.textContent();
  168 |     const total = parseInt((text || '0 / 0').split('/')[1].trim(), 10);
  169 | 
  170 |     // Go prev from match 1 -> should wrap to total
  171 |     await page.locator('#match-nav-prev').click();
  172 |     await page.waitForTimeout(200);
  173 |     await expect(counter).toContainText(new RegExp(`^${total} / ${total}$`));
  174 | 
  175 |     // Go next -> wraps back to 1
  176 |     await page.locator('#match-nav-next').click();
  177 |     await page.waitForTimeout(200);
  178 |     await expect(counter).toContainText(/^1 \//);
  179 |   });
  180 | 
  181 |   test('D5: current match uses the "current" highlight color', async ({ page }) => {
  182 |     // First highlight should be the current (orange #ff9632 => rgb(255, 150, 50))
  183 |     const firstMark = contentFrame(page).locator('mark.search-highlight').first();
> 184 |     await expect(firstMark).toHaveCSS('background-color', 'rgb(255, 150, 50)');
      |                             ^ Error: expect(locator).toHaveCSS(expected) failed
  185 |   });
  186 | 
  187 |   test('D6: close hides bar and removes highlights', async ({ page }) => {
  188 |     await page.locator('#match-nav-close').click();
  189 |     await expect(page.locator('#match-nav-bar')).toBeHidden();
  190 |     await expect(contentFrame(page).locator('mark.search-highlight')).toHaveCount(0);
  191 |   });
  192 | });
  193 | 
  194 | test.describe('E. Scrollbar markers', () => {
  195 |   test.beforeEach(async ({ page }) => {
  196 |     await waitForIndex(page);
  197 |     // "current" appears many times across the docs -> several markers
  198 |     await search(page, 'current');
  199 |     await page.locator('.search-result-item').first().click();
  200 |     await expect(page.locator('#match-nav-bar')).toBeVisible();
  201 |   });
  202 | 
  203 |   test('E1/E2: markers are visible and count equals number of matches', async ({ page }) => {
  204 |     await expect(page.locator('#scrollbar-markers')).toBeVisible();
  205 |     const markerCount = await page.locator('.scrollbar-marker').count();
  206 |     const counterText = await page.locator('#match-nav-counter').textContent();
  207 |     const total = parseInt((counterText || '0 / 0').split('/')[1].trim(), 10);
  208 |     expect(markerCount).toBe(total);
  209 |   });
  210 | 
  211 |   test('E4: exactly one marker is "current"', async ({ page }) => {
  212 |     await expect(page.locator('.scrollbar-marker.current')).toHaveCount(1);
  213 |   });
  214 | 
  215 |   test('E5: pressing next moves the current marker', async ({ page }) => {
  216 |     const markers = page.locator('.scrollbar-marker');
  217 |     // index 0 is current initially
  218 |     await expect(markers.nth(0)).toHaveClass(/current/);
  219 |     await page.locator('#match-nav-next').click();
  220 |     await page.waitForTimeout(200);
  221 |     await expect(markers.nth(0)).not.toHaveClass(/current/);
  222 |     await expect(markers.nth(1)).toHaveClass(/current/);
  223 |   });
  224 | 
  225 |   test('E6: clicking a marker jumps to that match', async ({ page }) => {
  226 |     const markers = page.locator('.scrollbar-marker');
  227 |     const count = await markers.count();
  228 |     test.skip(count < 3, 'Need at least 3 matches for this test');
  229 |     // Markers are thin (15px) and can overlap, so a centred click may land on a
  230 |     // neighbour. Click the very top-left pixel of the target marker, then read
  231 |     // back which match actually became current (1-based index in the counter).
  232 |     const target = markers.nth(2);
  233 |     await target.click({ force: true, position: { x: 2, y: 1 } });
  234 |     await page.waitForTimeout(200);
  235 | 
  236 |     // Whatever match got selected, its marker must carry the "current" class and
  237 |     // the counter's first number must match that marker's position (1-based).
  238 |     const currentIndex = await page.locator('.scrollbar-marker.current').evaluate(
  239 |       (el) => Array.from(el.parentElement.children).indexOf(el)
  240 |     );
  241 |     await expect(page.locator('#match-nav-counter')).toContainText(
  242 |       new RegExp(`^${currentIndex + 1} /`)
  243 |     );
  244 |     await expect(page.locator('.scrollbar-marker.current')).toHaveCount(1);
  245 |   });
  246 | 
  247 |   test('E8: closing the bar removes all markers', async ({ page }) => {
  248 |     await page.locator('#match-nav-close').click();
  249 |     await expect(page.locator('#scrollbar-markers')).toBeHidden();
  250 |     await expect(page.locator('.scrollbar-marker')).toHaveCount(0);
  251 |   });
  252 | });
  253 | 
  254 | test.describe('F. Navigation pane (sidebar)', () => {
  255 |   test.beforeEach(async ({ page }) => {
  256 |     await waitForIndex(page);
  257 |   });
  258 | 
  259 |   test('F1: clicking a sidebar link loads the page and marks it active', async ({ page }) => {
  260 |     const link = page.locator('#nav-sections ul a', { hasText: 'PMSM BEMF Observer' });
  261 |     await link.click();
  262 |     await expect(link).toHaveClass(/active/);
  263 |     // The link targets the iframe via target="contentFrame", which changes the
  264 |     // frame's location but NOT its src attribute. So assert on the frame URL.
  265 |     await expect.poll(
  266 |       async () => page.frame({ name: 'contentFrame' })?.url() || '',
  267 |       { timeout: 10000 }
  268 |     ).toMatch(/BEMFObserver/);
  269 |   });
  270 | 
  271 |   test('F2 (bug fix): after a search-result jump, clicking a sidebar link starts at top with no highlight', async ({ page }) => {
  272 |     // 1) Search and jump into a page (creates highlight + onload handler)
  273 |     await search(page, 'current');
  274 |     await page.locator('.search-result-item').first().click();
  275 |     await expect(page.locator('#match-nav-bar')).toBeVisible();
  276 | 
  277 |     // 2) Click a DIFFERENT sidebar link that also contains the keyword
  278 |     const link = page.locator('#nav-sections ul a', { hasText: 'PMSM Current Control' });
  279 |     await link.click();
  280 | 
  281 |     // 3) Wait for the new page to load (assert on frame URL, not src attribute)
  282 |     await expect.poll(
  283 |       async () => page.frame({ name: 'contentFrame' })?.url() || '',
  284 |       { timeout: 10000 }
```