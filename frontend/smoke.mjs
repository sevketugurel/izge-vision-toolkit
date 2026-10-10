// Frontend-only smoke with explicit fixtures. API integration is tested separately on PostgreSQL 18.
import { readFileSync, writeFileSync, mkdirSync, mkdtempSync } from "node:fs";
import { resolve, join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
const root = resolve(import.meta.dirname, ".."),
  dist = resolve(import.meta.dirname, "dist");
const index = readFileSync(resolve(dist, "index.html"), "utf8");
const script = index.match(/src="([^"]+\.js)"/)[1],
  css = index.match(/href="([^"]+\.css)"/)[1];
const fixture = `
const farm={id:'farm',name:'Tarayıcı testi — SENTETİK DEMO',is_demo:true,timezone:'Europe/Istanbul'};
const animal={id:'animal',number:'101',name:'Ada'};
const payloads={farms:[farm],barns:[{id:'barn',name:'Kuzey'}],animals:[animal],stalls:[{id:'stall',barn_id:'barn',name:'A-01'}],cameras:[{id:'camera',name:'Kamera 1',barn_id:'barn',geometry_version:1,geometry_valid:false}],occupancies:[],zones:[],neighbors:[],media:[],runs:[],jobs:[],events:[],annotations:[],reviews:[],'feeding-windows':[],'feed-presence':[]};
window.fetch=async (url,options)=>{let data;const key=url.split('?')[0].split('/').at(-1);
if(url==='/api/config')data={app_name:'İzge',max_upload_bytes:2147483648};
else if(key==='report')data={animals:[],timezone:'Europe/Istanbul',status:'insufficient_data'};
else data=payloads[key]||[];
return new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}})};
window.smokeErrors=[];window.addEventListener('error',e=>window.smokeErrors.push(e.message));
window.addEventListener('unhandledrejection',e=>window.smokeErrors.push(String(e.reason)));
`;
const tests = `
setTimeout(async()=>{try{
const wait=()=>new Promise(r=>setTimeout(r,80));
if(!document.body.innerText.includes('SENTETİK DEMO'))throw new Error('Demo badge missing');
const nav=[...document.querySelectorAll('nav button')];if(nav.length!==6)throw new Error('Six sections required');
for(let i=0;i<6;i++){nav[i].click();await wait();if(document.querySelector('h1')?.textContent!==nav[i].textContent.slice(1))throw new Error('Navigation '+i);}
if(!document.body.innerText.includes('Etiket geçmişi'))throw new Error('Review view missing');
nav[1].click();await wait();if(!document.body.innerText.includes('Kamera bölgeleri'))throw new Error('Zone editor missing');
nav[0].click();await wait();
if(window.smokeErrors.length)throw new Error(window.smokeErrors.join('; '));
document.body.dataset.smoke='passed';document.body.insertAdjacentHTML('beforeend','<output id="smoke-result">FRONTEND_SMOKE_PASSED</output>');
}catch(e){document.body.dataset.smoke='failed';document.body.insertAdjacentHTML('beforeend','<output id="smoke-result">FRONTEND_SMOKE_FAILED: '+e.message+'</output>');}},300);
`;
mkdirSync(resolve(root, "artifacts"), { recursive: true });
const file = resolve(root, "artifacts/frontend-smoke.html");
writeFileSync(
  file,
  `<!doctype html><html lang="tr"><meta charset="utf-8"><style>${readFileSync(resolve(dist, "." + css), "utf8")}</style><body><div id="root"></div><script>${fixture}</script><script type="module">${readFileSync(resolve(dist, "." + script), "utf8")}\n${tests}</script></body></html>`,
);
const chrome =
  process.env.CHROME_BIN ||
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const result = spawnSync(
  chrome,
  [
    "--headless",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--no-first-run",
    "--no-default-browser-check",
    `--user-data-dir=${mkdtempSync(join(tmpdir(), "izge-chrome-smoke-"))}`,
    "--virtual-time-budget=10000",
    "--dump-dom",
    "file://" + file,
  ],
  { encoding: "utf8", timeout: 45000, maxBuffer: 4 * 1024 * 1024 },
);
writeFileSync(
  resolve(root, "artifacts/browser-smoke.log"),
  JSON.stringify({
    status: result.status,
    signal: result.signal,
    error: String(result.error || ""),
  }) +
    "\n" +
    (result.stderr || ""),
);
writeFileSync(resolve(root, "artifacts/browser-dom.html"), result.stdout || "");
if (
  result.status === 0 &&
  /id="smoke-result">FRONTEND_SMOKE_PASSED/.test(result.stdout || "")
) {
  console.log(
    "Frontend smoke passed (fixture data, six sections, demo label, review, zone editor).",
  );
} else {
  console.error(
    "Browser smoke did not pass. Exit:",
    result.status,
    "signal:",
    result.signal,
    "See artifacts/browser-smoke.log",
  );
  process.exit(1);
}
