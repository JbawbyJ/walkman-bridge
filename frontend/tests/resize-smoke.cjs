// Windows Electron acceptance for both products, including setZoomFactor and
// screenshot evidence. The headless Bridge Listening/Walkman/Transfer viewport
// checks live in walkman-smoke.test.mjs and run with `npm run test:frontend`.
// Run this file with the real Electron binary after building the frontend.
// This fixture owns its loopback API and never starts a backend, scanner,
// installer or device.
'use strict'
const { app, BrowserWindow } = require('electron')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')

const label = process.argv.find(value => value.startsWith('--label='))?.slice(8) || 'current'
const screenshotsOnly = process.argv.includes('--screenshots-only')
if (!/^[a-z0-9-]+$/.test(label)) throw new Error('Invalid output label')
const output = path.resolve(__dirname, '../test-output', `resize-${label}`)
fs.mkdirSync(output, { recursive: true })
app.setPath('userData', path.join(output, 'profile'))
app.on('window-all-closed', () => {})
const results = [], errors = [], failures = []
const progress = message => fs.appendFileSync(path.join(output, 'progress.log'), `${new Date().toISOString()} ${message}\n`)
const timeout = setTimeout(() => { progress('TIMEOUT'); app.exit(1) }, 180000)
const items = Array.from({ length: 36 }, (_, index) => ({
  id: `resize-${index}`, name: `Track ${index + 1} — A deliberately long filename to exercise the queue and staging layout.flac`,
  title: `Track ${index + 1} — A deliberately long title to exercise the queue`, artist: 'An artist with a long display name',
  album: 'Resize regression fixture', status: index === 2 ? 'blocked' : 'ready', duration_seconds: 240,
  mime: 'audio/flac', size_bytes: 12345678, scan: { ok: index !== 2, state: index === 2 ? 'blocked' : 'clean' },
}))
let product = 'player'
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1')
  const json = value => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(value)) }
  if (url.pathname.startsWith('/api/')) {
    if (url.pathname === '/api/health') return json({ ok: true, product, version: '0.4.1' })
    if (url.pathname === '/api/queue') return json({ items, quota: { used_bytes: 12345678 * items.length, limit_bytes: 10 * 1024 ** 3 } })
    if (url.pathname === '/api/playback-state') return json({ media_id: null, position_seconds: 0, volume: 0.4, repeat: 'off', shuffle: false })
    if (url.pathname === '/api/engine-busy') return json({ busy: false, draining: false, active: [] })
    if (url.pathname === '/api/jobs/latest') return json(null)
    if (url.pathname === '/api/device') return json({ connected: true, model: 'NW-S705F', track_count: items.length, total_bytes: 2 * 1024 ** 3, free_bytes: 1024 ** 3 })
    if (url.pathname === '/api/tracks') { res.setHeader('ETag', '"resize-fixture"'); return json(items) }
    // Music manager refreshes the same local metadata without changing this test's layout fixture.
    if (url.pathname === '/api/playlists') return json({ items: [] })
    if (url.pathname === '/api/device/playlists') { res.setHeader('ETag', '"resize-playlists"'); return json({ items: [] }) }
    res.statusCode = 404
    errors.push(`Unexpected fixture request: ${req.method} ${url.pathname}`)
    return json({ detail: 'Unexpected resize-fixture request' })
  }
  const dist = path.resolve(__dirname, '../dist')
  const file = path.resolve(dist, `.${url.pathname === '/' ? '/index.html' : url.pathname}`)
  if (!file.startsWith(dist + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.statusCode = 404; return res.end() }
  res.setHeader('Content-Type', { '.js': 'application/javascript', '.css': 'text/css', '.html': 'text/html', '.woff2': 'font/woff2', '.png': 'image/png' }[path.extname(file)] || 'application/octet-stream')
  fs.createReadStream(file).pipe(res)
})
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const evaluate = (win, script) => win.webContents.executeJavaScript(script, true)
async function until(win, expression) {
  for (let attempt = 0; attempt < 100; attempt++) { if (await evaluate(win, expression)) return; await delay(50) }
  throw new Error(`Renderer condition timed out: ${expression}`)
}

// Scroll each rendered control into view and intersect every clipping ancestor.
// A correct scrollWidth alone misses controls clipped by a fixed-height shell.
const geometry = `(() => {
  const main = document.querySelector('main'), root = document.documentElement;
  const bounds = element => { const r=element.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}; };
  const visible = element => element.checkVisibility({visibilityProperty:true});
  const fit = element => { const r=element.getBoundingClientRect(); return r.left>=-1 && r.right<=innerWidth+1 && r.top>=-1 && r.bottom<=innerHeight+1; };
  const footer=document.querySelector('footer');
  const summary={ viewport:[innerWidth,innerHeight], root:[root.scrollWidth,root.scrollHeight], shell:bounds(document.querySelector('.nightops-app')), main:bounds(main), footer:bounds(footer), footerVisible:fit(footer), title:document.title, legacyBranding:/night\\s+ops/i.test(document.body.innerText), retroControls:!document.querySelector('.now-playing')||!!document.querySelector('.retro-deck .volume-dial'), horizontal:[], unreachable:[] };
  for(const element of [root,document.body,document.querySelector('.nightops-app'),main,...document.querySelectorAll('.panel,.retro-deck,.app-titlebar,footer')]) {
    if(element.scrollWidth>element.clientWidth+2) summary.horizontal.push({element:element.className||element.tagName,client:element.clientWidth,scroll:element.scrollWidth});
  }
  for(const element of document.querySelectorAll('main button,main input,main select,main summary,footer button,footer input,.window-controls button,.player-wing-controls button,.workspace-tabs button,.backup-warning button')) {
    if(!visible(element)) continue;
    element.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
    const r=element.getBoundingClientRect();
    let left=Math.max(0,r.left),right=Math.min(innerWidth,r.right),top=Math.max(0,r.top),bottom=Math.min(innerHeight,r.bottom);
    for(let p=element.parentElement;p;p=p.parentElement){const s=getComputedStyle(p),b=p.getBoundingClientRect();if(/auto|scroll|hidden|clip/.test(s.overflowX)){left=Math.max(left,b.left);right=Math.min(right,b.right)}if(/auto|scroll|hidden|clip/.test(s.overflowY)){top=Math.max(top,b.top);bottom=Math.min(bottom,b.bottom)}}
    const hit=right>left&&bottom>top?document.elementFromPoint((left+right)/2,(top+bottom)/2):null;
    if(right-left<r.width-2||bottom-top<Math.min(r.height,24)-2||!hit||!(hit===element||element.contains(hit))) summary.unreachable.push({element:element.getAttribute('aria-label')||element.textContent.trim().slice(0,50),rect:bounds(element)});
  }
  for(const scroll of document.querySelectorAll('main,main *')) if(scroll.scrollHeight>scroll.clientHeight) scroll.scrollTop=0;
  window.scrollTo(0,0);
  summary.shellScroll=window.scrollY;
  return summary;
})()`

async function dialogCheck(win, type) {
  const opener = type === 'link' ? `[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Import link')` : type === 'manager' ? `document.querySelector('.manage-music')` : `document.querySelector('.device-track .icon-button')`
  await evaluate(win, `${opener}.click()`)
  await until(win, `!!document.querySelector('dialog[open]')`)
  const result = await evaluate(win, `(() => {
    const dialog=document.querySelector('dialog[open]'),r=dialog.getBoundingClientRect();
    const result={type:${JSON.stringify(type)},width:r.width,height:r.height,viewport:[innerWidth,innerHeight],bounded:r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1,legacyBranding:/night\\s+ops/i.test(dialog.innerText),horizontal:dialog.scrollWidth>dialog.clientWidth+2,unreachable:[]};
    for(const button of dialog.querySelectorAll('button,input,select,summary')) {
      if(!button.checkVisibility({visibilityProperty:true}))continue;
      button.scrollIntoView({block:'center',behavior:'instant'});
      const b=button.getBoundingClientRect();let left=Math.max(0,b.left),right=Math.min(innerWidth,b.right),top=Math.max(0,b.top),bottom=Math.min(innerHeight,b.bottom);
      for(let p=button.parentElement;p;p=p.parentElement){const s=getComputedStyle(p),a=p.getBoundingClientRect();if(/auto|scroll|hidden|clip/.test(s.overflowX)){left=Math.max(left,a.left);right=Math.min(right,a.right)}if(/auto|scroll|hidden|clip/.test(s.overflowY)){top=Math.max(top,a.top);bottom=Math.min(bottom,a.bottom)}}
      const hit=right>left&&bottom>top?document.elementFromPoint((left+right)/2,(top+bottom)/2):null;
      if(right-left<b.width-2||bottom-top<Math.min(b.height,24)-2||!hit||!(hit===button||button.contains(hit)))result.unreachable.push(button.textContent||button.getAttribute('aria-label')||button.id)
    }
    return result;
  })()`)
  await evaluate(win, `document.querySelector('dialog[open]').dispatchEvent(new Event('cancel',{cancelable:true}))`)
  await until(win, `!document.querySelector('dialog[open]')`)
  return result
}

async function main() {
  await app.whenReady()
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const sizes = screenshotsOnly ? [[640,560],[1140,860]] : [[640,560],[720,560],[900,700],[1140,860],[1380,860],[1920,640],[800,1200]]
  for (const mode of ['player','bridge']) {
    product = mode
    const win = new BrowserWindow({ width:1140,height:860,minWidth:640,minHeight:560,frame:false,show:false,
      webPreferences:{preload:path.resolve(__dirname,'../../electron/preload.cjs'),additionalArguments:[`--nightops-product=${mode}`],backgroundThrottling:false,contextIsolation:true,sandbox:true,nodeIntegration:false} })
    win.webContents.setAudioMuted(true)
    win.webContents.on('console-message', details => { if(details.level==='error') errors.push(details.message) })
    await win.loadURL(`http://127.0.0.1:${server.address().port}/`)
    await until(win, `document.querySelector('.nightops-app') && document.querySelectorAll('.queue-row').length===36`)
    await evaluate(win, 'document.fonts.ready')
    const contextual = await evaluate(win, `!!document.querySelector('.manage-music')`)
    const views = !contextual ? ['default'] : screenshotsOnly ? [mode==='player'?'queue':'listening'] : mode === 'player' ? ['queue','equalizer','deck'] : ['listening','equalizer','device','transfer']
    for (const [width,height] of sizes) for(const scale of screenshotsOnly?[1,2]:[1,1.25,1.5,2]) for(const view of views) {
      win.setSize(width,height)
      win.webContents.setZoomFactor(scale)
      await delay(70)
      if(contextual) {
        await evaluate(win, `(() => {
          const mode=${JSON.stringify(mode)},view=${JSON.stringify(view)};
          if(mode==='bridge'){
            const name={listening:'Listening',equalizer:'Listening',device:'Walkman',transfer:'Transfer'}[view];
            const tab=[...document.querySelectorAll('.workspace-tabs button')].find(b=>b.textContent.startsWith(name));
            if(tab.getAttribute('aria-pressed')!=='true')tab.click();
          }else{
            const queue=[...document.querySelectorAll('.player-wing-controls button')].find(b=>b.textContent==='Playback queue');
            if((queue.getAttribute('aria-pressed')==='true')!==(view!=='deck'))queue.click();
            const eq=[...document.querySelectorAll('.player-wing-controls button')].find(b=>b.textContent==='Equalizer');
            if((eq.getAttribute('aria-pressed')==='true')!==(view==='equalizer'))eq.click();
          }
        })()`)
        await delay(20)
        if(mode==='bridge'&&['listening','equalizer'].includes(view))await evaluate(win,`(() => {const eq=document.querySelector('.bridge-eq-toggle button');if((eq.getAttribute('aria-expanded')==='true')!==${view==='equalizer'})eq.click()})()`)
        await delay(20)
        await evaluate(win,`document.querySelectorAll('.queue-row-menu').forEach((menu,index)=>{menu.open=index===2})`)
      }
      const result={product:mode,view,size:[width,height],scale,...await evaluate(win,geometry)}
      result.dialogs=[]
      if(await evaluate(win,`[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='Import link')`))result.dialogs.push(await dialogCheck(win,'link'))
      if(await evaluate(win,`!!document.querySelector('.device-track .icon-button')`))result.dialogs.push(await dialogCheck(win,'delete'))
      if(contextual&&view===views[0])result.dialogs.push(await dialogCheck(win,'manager'))
      result.ok=!result.legacyBranding&&result.title===(mode==='player'?'Red Lotus Player':'Walkman Bridge')&&result.retroControls&&!result.horizontal.length&&!result.unreachable.length&&result.footerVisible&&result.main.height>=30&&result.root[1]<=result.viewport[1]+2&&result.dialogs.every(d=>d.bounded&&!d.legacyBranding&&!d.horizontal&&!d.unreachable.length)
      results.push(result)
      if(!result.ok)failures.push(`${mode} ${view} ${width}x${height} @${scale}: ${JSON.stringify(result)}`)
      progress(`${result.ok?'PASS':'FAIL'} ${mode} ${view} ${width}x${height} @${scale}`)
      if(((view===views[0])&&((width===640&&(scale===1||scale===2))||(width===1140&&scale===1)||(width===1920&&scale===1)))||(view==='device'&&width===1140&&scale===1)) {
        await evaluate(win, `document.querySelectorAll('.queue-row-menu').forEach(menu=>menu.open=false);document.querySelector('main').scrollTop=0;window.scrollTo(0,0)`)
        // DOM geometry settles before Chromium's compositor. Wait for the
        // captured frame to reflect the restored view and the current zoom.
        win.webContents.invalidate()
        await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})
        await delay(160)
        fs.writeFileSync(path.join(output,`${mode}-${view==='device'?'device-':''}${width}x${height}-${scale}.png`),(await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG())
      }
    }
    win.destroy()
  }
  const report={ok:!failures.length&&!errors.length,electron:process.versions.electron,chromium:process.versions.chrome,scaleMethod:'Electron webContents.setZoomFactor: 100/125/150/200% effective CSS viewport scaling, not an OS DPI setting change',cases:results.length,failures:failures.length,errors,results}
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2))
  if(failures.length)fs.writeFileSync(path.join(output,'failures.txt'),failures.join('\n'))
  console.log(`Resize smoke ${report.ok?'PASS':'FAIL'}: ${results.length} actual Electron product/size/scale cases; ${failures.length} layout failures; ${errors.length} renderer/API errors. Report: ${output}`)
  process.exitCode=report.ok?0:1
}
main().then(()=>{clearTimeout(timeout);server.close();app.exit(process.exitCode||0)}).catch(error=>{progress(error.stack||String(error));console.error(error);clearTimeout(timeout);server.close();app.exit(1)})
