const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: '/opt/google/chrome/chrome',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto('http://localhost:8000', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: '/tmp/app_state.png', fullPage: true });

  // Dump all element IDs and visible elements
  const info = await page.evaluate(() => {
    const elements = document.querySelectorAll('[id]');
    return Array.from(elements).map(el => ({
      id: el.id,
      tag: el.tagName,
      visible: el.offsetParent !== null,
      text: el.textContent?.trim().slice(0, 50),
    }));
  });
  info.forEach(e => console.log(`[${e.visible ? 'V' : 'H'}] #${e.id} <${e.tag}> "${e.text}"`));

  await browser.close();
})();
