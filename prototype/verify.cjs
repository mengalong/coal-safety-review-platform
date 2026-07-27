const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
  });
  const desktop = await browser.newPage({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 });
  const errors = [];
  desktop.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });
  desktop.on('pageerror', err => errors.push(err.message));
  await desktop.goto('http://127.0.0.1:65513/#workbench', { waitUntil: 'networkidle' });
  await desktop.fill('#login-name', 'admin');
  await desktop.fill('#login-password', 'coal123456');
  await desktop.click('#login-form button[type="submit"]');
  await desktop.waitForSelector('.global-header');
  await desktop.screenshot({ path: 'workbench-desktop.png', fullPage: true });
  await desktop.click('[data-nav="tasks"]');
  await desktop.click('tbody tr:first-child');
  await desktop.click('[data-act="review"]');
  await desktop.screenshot({ path: 'review-desktop.png', fullPage: true });
  const desktopMetrics = await desktop.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    reviewColumns: getComputedStyle(document.querySelector('.review-workspace')).gridTemplateColumns,
    issueCount: document.querySelectorAll('.issue-card').length
  }));

  const routeChecks = [];
  for (const route of ['workbench', 'tasks', 'create', 'detail', 'standards', 'standardDetail', 'rules', 'reports', 'report', 'settings']) {
    await desktop.goto(`http://127.0.0.1:65513/#${route}`, { waitUntil: 'networkidle' });
    routeChecks.push(await desktop.evaluate((name) => ({
      route: name,
      heading: document.querySelector('h1, .review-title strong')?.textContent.trim() || '',
      overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      bodyLength: document.body.innerText.length
    }), route));
  }

  await desktop.goto('http://127.0.0.1:65513/#create', { waitUntil: 'networkidle' });
  await desktop.click('[data-act="upload"]');
  await desktop.click('[data-act="next"]');
  await desktop.click('[data-act="next"]');
  const createStep = await desktop.locator('.step.current .step-label').textContent();
  await desktop.goto('http://127.0.0.1:65513/#detail', { waitUntil: 'networkidle' });
  await desktop.click('[data-act="review"]');
  await desktop.click('[data-act="confirm"]');
  const selectedIssue = await desktop.locator('.issue-card.active h3').textContent();

  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
  mobile.on('pageerror', err => errors.push(err.message));
  await mobile.goto('http://127.0.0.1:65513/#tasks', { waitUntil: 'networkidle' });
  await mobile.screenshot({ path: 'tasks-mobile.png', fullPage: true });
  const mobileMetrics = await mobile.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    navItems: document.querySelectorAll('.nav-item').length,
    bottomNavHeight: Math.round(document.querySelector('.sidebar').getBoundingClientRect().height)
  }));
  console.log(JSON.stringify({ desktopMetrics, mobileMetrics, routeChecks, createStep, selectedIssue, errors }, null, 2));
  await browser.close();
})();
