// docs/diagrams/*.html 을 같은 이름의 PNG 로 저장합니다 (README 그림).
// 실행: npm i -g playwright && npx playwright install chromium   (최초 1회)
//       node docs/diagrams/render.js
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1900, height: 1000 }, deviceScaleFactor: 2 });
  for (const name of fs.readdirSync(__dirname).filter((f) => f.endsWith(".html"))) {
    await page.goto(pathToFileURL(path.join(__dirname, name)).href, { waitUntil: "networkidle" });
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(() => window.drawLinks && window.drawLinks());
    const out = path.join(__dirname, name.replace(/\.html$/, ".png"));
    await page.locator("#capture").screenshot({ path: out });
    console.log("saved", out);
  }
  await browser.close();
})();
