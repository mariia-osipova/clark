/**
 * Playwright end-to-end test: tiramisu ingredient discovery
 *
 * Goal: verify the agent builds a reasonable tiramisu cart from a natural-language
 * request without explicit ingredient lists or hard-coded rules.
 *
 * Expected tiramisu ingredients: mascarpone, huevos/eggs, azúcar/sugar,
 * café/coffee, vainillas/savoiardi, cacao/cocoa, crema.
 */

const { chromium } = require('playwright');

const BASE_URL = 'http://localhost:8000';
const SESSION  = 'tiramisu-e2e-' + Date.now();
const TIMEOUT  = 60_000;

// Rough keyword check: does the cart contain at least some tiramisu-related items?
const TIRAMISU_KEYWORDS = [
  'mascarpone', 'queso crema', 'cream cheese',
  'huevo', 'egg',
  'azucar', 'azúcar', 'sugar',
  'cafe', 'café', 'coffee', 'nescafe',
  'vainilla', 'vainillas', 'savoiardi', 'bizcocho',
  'cacao', 'cocoa', 'chocolate',
  'crema',
];

function cartMatchesTiramisu(cartItems) {
  const joined = cartItems.map(i => i.name.toLowerCase()).join(' ');
  const hits = TIRAMISU_KEYWORDS.filter(kw => joined.includes(kw));
  return { hits, ok: hits.length >= 2 };
}

async function resolveAllClarifications(page, maxRounds = 6) {
  for (let round = 0; round < maxRounds; round++) {
    await page.waitForTimeout(500);
    const modal = page.locator('#clarification-modal');
    const visible = await modal.isVisible().catch(() => false);
    if (!visible) break;

    console.log(`  [clarification round ${round + 1}]`);
    const question = await page.locator('#modal-question').textContent();
    console.log(`  question: ${question}`);

    // Pick the first option
    const firstRadio = page.locator('input[name="clarification-option"]').first();
    await firstRadio.click();
    await page.waitForTimeout(300);

    const label = await page.locator('.modal__choice--selected .modal__option-label')
      .textContent().catch(() => '(unknown)');
    console.log(`  chose: ${label}`);

    const confirmBtn = page.locator('#modal-confirm');
    await confirmBtn.click();

    // Wait for modal to close or another to open
    await page.waitForTimeout(2000);
  }
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: '/opt/google/chrome/chrome',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu'],
  });
  const context = await browser.newContext();
  const page = await context.newPage();

  page.on('console', msg => {
    if (msg.type() === 'error') console.error('[browser error]', msg.text());
  });

  console.log('=== Navigating to app ===');
  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: TIMEOUT });
  await page.screenshot({ path: '/tmp/tiramisu_01_loaded.png' });
  console.log('Page loaded.');

  // ── Switch to chat tab ───────────────────────────────────────────────────────
  console.log('=== Switching to chat tab ===');
  await page.click('button[data-tab="chat"]');
  await page.waitForTimeout(500);
  await page.screenshot({ path: '/tmp/tiramisu_01b_chat_tab.png' });
  console.log('Chat tab selected.');

  // ── Send tiramisu request ────────────────────────────────────────────────────
  const chatInput = page.locator('#chat-input');
  const sendBtn   = page.locator('#chat-send-btn, button[type="submit"], #send-btn')
    .first();

  await chatInput.fill('necesito los ingredientes para hacer tiramisu');
  await page.screenshot({ path: '/tmp/tiramisu_02_typed.png' });
  console.log('=== Sending message: "necesito los ingredientes para hacer tiramisu" ===');

  // Submit via Enter
  await chatInput.press('Enter');

  // Wait for first response (agent takes a few seconds)
  await page.waitForTimeout(8000);
  await page.screenshot({ path: '/tmp/tiramisu_03_after_first_response.png' });

  // ── Resolve clarifications ───────────────────────────────────────────────────
  console.log('=== Resolving clarifications ===');
  await resolveAllClarifications(page);
  await page.waitForTimeout(3000);
  await page.screenshot({ path: '/tmp/tiramisu_04_after_clarifications.png' });

  // ── Read cart ────────────────────────────────────────────────────────────────
  const cartItems = await page.evaluate(() => {
    // Try reading from app state
    if (typeof window !== 'undefined' && window.state && window.state.cart) {
      return window.state.cart;
    }
    // Fallback: scrape cart DOM
    const items = [];
    document.querySelectorAll('.cart-item, [data-product-id]').forEach(el => {
      const name = el.querySelector('.cart-item__name, .product-name')?.textContent?.trim() || '';
      if (name) items.push({ name });
    });
    return items;
  });

  // Also check the last agent message for ingredient mentions
  const agentMessages = await page.locator('.chat-message--assistant, .message--assistant')
    .allTextContents().catch(() => []);

  console.log('\n=== Cart contents ===');
  cartItems.forEach(i => console.log(' -', i.name || i.product_id));

  console.log('\n=== Last agent replies ===');
  agentMessages.slice(-3).forEach(m => console.log(' >', m.trim().slice(0, 120)));

  const { hits, ok } = cartMatchesTiramisu(cartItems);
  console.log(`\n=== Result ===`);
  console.log(`Tiramisu keyword hits (${hits.length}):`, hits);

  if (ok) {
    console.log('PASS — agent assembled tiramisu ingredients without explicit rules');
  } else {
    // Even if cart is sparse, check if agent replied mentioning ingredients
    const allText = agentMessages.join(' ').toLowerCase();
    const msgHits = TIRAMISU_KEYWORDS.filter(kw => allText.includes(kw));
    console.log(`Message hits (${msgHits.length}):`, msgHits);
    if (msgHits.length >= 2) {
      console.log('PARTIAL PASS — agent mentioned tiramisu ingredients but cart may need more clarifications resolved');
    } else {
      console.log('NEEDS ATTENTION — cart and messages do not clearly contain tiramisu ingredients');
    }
  }

  await browser.close();
})();
