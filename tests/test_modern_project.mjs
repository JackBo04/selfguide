// Exercise actual extension windows and the real broker on synthetic local pages.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import crypto from 'node:crypto';
import {spawn,spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {fixture} from './browser_fixture.mjs';
const require=createRequire(process.env.SELFGUIDE_TEST_MODULE_ROOT || import.meta.url);
const {chromium}=require('playwright');
const modernFixture=fixture.replace("'/g/g-p-fixture-selfguide/c/'","'/c/'")+`<button data-composer-navigation-target="workspace-project" aria-expanded="false">selfguide</button><div role="option" data-value="g-p-fixture" hidden>selfguide</div><script>const selector=document.querySelector('[data-composer-navigation-target]'),option=document.querySelector('[role=option]');selector.onclick=()=>{option.hidden=false;selector.setAttribute('aria-expanded','true')};option.onclick=()=>{selector.textContent=option.textContent;option.hidden=true;selector.setAttribute('aria-expanded','false')};history.replaceState({},'',location.pathname.includes('/c/')?'/c/'+location.pathname.split('/').pop():'/');</script>`;
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const tmp=await fs.mkdtemp(path.join(os.tmpdir(),'selfguide-multi-test-'));
const project='https://chatgpt.com/g/g-p-fixture/project';
const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});
const variant=process.env.SELFGUIDE_TEST_VARIANT || 'server';
const env={...process.env,SELFGUIDE_BRIDGE_HOME:path.join(tmp,'bridge')};
const scripts=path.join(root,`skills/selfguide-${variant}/scripts`);
function session(...args){const r=spawnSync('python',[path.join(scripts,'session.py'),...args],{env,encoding:'utf8'});assert.equal(r.status,0,r.stderr);return JSON.parse(r.stdout);}
const init=spawnSync('python',[path.join(scripts,'bridge.py'),'init','--project-url',project,'--port',String(port)],{env,encoding:'utf8'});assert.equal(init.status,0,init.stderr);
const cfg=JSON.parse(await fs.readFile(path.join(tmp,'bridge/config.json'),'utf8'));
const broker=spawn('python',[path.join(scripts,'bridge.py'),'serve'],{env,stdio:'ignore'});
const wait=ms=>new Promise(r=>setTimeout(r,ms));
async function rpc(route,body){
 const r=await fetch(`http://127.0.0.1:${port}`+route,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+cfg.agent_token},body:JSON.stringify(body)});
 const value=await r.json();assert.equal(r.status,200,JSON.stringify(value));return value;
}
async function command(task,action,extra={},allowError=false){
 const id=crypto.randomBytes(16).toString('hex');
 await rpc('/command',{id,command:{action,session:task.id,expected_url:task.url,...extra}});
 const end=Date.now()+25000;
 while(Date.now()<end){
  const job=await rpc('/job',{id});
  if(job.state==='done'){if(!allowError)assert.equal(job.result.error,undefined,JSON.stringify(job.result));return job.result;}
  await wait(150);
 }
 throw Error('Command timed out: '+task.id+' '+action);
}
let context;
try {
 await wait(300);
 context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{channel:'chromium',headless:true,viewport:null,args:['--no-sandbox','--disable-dev-shm-usage','--no-proxy-server','--host-resolver-rules=MAP chatgpt.com 127.0.0.1',`--disable-extensions-except=${path.join(root,'extension')}`,`--load-extension=${path.join(root,'extension')}`]});
 await context.route('https://chatgpt.com/**',route=>route.fulfill({contentType:'text/html',body:modernFixture}));
 const worker=context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
 const legacy=await context.newPage();await legacy.goto(project);await legacy.waitForSelector('#prompt-textarea');await legacy.locator('#prompt-textarea').fill('KEEP THE ORIGINAL DRAFT');
 await worker.evaluate(async cfg=>{
  const [tab]=await chrome.tabs.query({url:'https://chatgpt.com/*'});
  await chrome.storage.local.set({endpoint:cfg.endpoint,token:cfg.token,tabId:tab.id,enabled:true});
 },{endpoint:`http://127.0.0.1:${port}`,token:cfg.browser_token});
 async function newTask(label){
  const file=path.join(tmp,label+'.txt');await fs.writeFile(file,'Task '+label+': use only your own attachment.\n\n\nKeep this last line.');
  const run=session('new','--task-file',file,'--workspace',path.join(tmp,'runs')).run;
  const task={id:path.basename(run),run,url:project,marker:crypto.randomBytes(16).toString('hex')};
  const created=context.waitForEvent('page');
  const opened=await command(task,'open');assert.equal(opened.window_opened,true);assert.equal(opened.reused,false);
  task.page=await created;
  // The extension can start navigation before Playwright attaches its route.
  // DNS is blocked above; explicitly revisit after attaching to the new target.
  await task.page.goto(project);await task.page.waitForSelector('#prompt-textarea');
  task.window=opened.window_id;task.tab=opened.tab_id;
  await task.page.evaluate(label=>{window.conversationId=label;window.collapsedMessage=true;window.renameUploads=true;},label);
  const prepared=session('prepare','--run',run,'--file',file);task.prompt=prepared.file;task.text=await fs.readFile(task.prompt,'utf8');
  return task;
 }
 const a=await newTask('modern');
 const ready=await command(a,'status');assert.equal(ready.composer,true);assert.equal(ready.page_url,'https://chatgpt.com/');assert.equal(ready.url,project);
 await command(a,'compose',{text:a.text});
 // A changed project must not accept a send, even though the URL stays /.
 await a.page.locator('[data-composer-navigation-target]').evaluate(e=>e.textContent='wrong project');
 assert.equal((await command(a,'send',{text:a.text},true)).code,'project_unverified');
 assert.equal(await a.page.evaluate(()=>window.sendClicks || 0),0);
 await a.page.locator('[data-composer-navigation-target]').evaluate(e=>e.textContent='selfguide');
 const sent=await command(a,'send',{text:a.text});assert.equal(sent.sent,true);assert.equal(sent.page_url,'https://chatgpt.com/c/modern');
 a.url=sent.url;assert.equal(a.url,'https://chatgpt.com/g/g-p-fixture/c/modern');
 session('submitting','--run',a.run);session('sent','--run',a.run,'--url',a.url);
 await wait(600);const reply=await command(a,'reply',{text:a.text});assert.equal(reply.complete,true);assert.match(reply.text,/FIXTURE_ACCEPTED/);assert.equal(reply.url,a.url);
 // A different root conversation cannot be substituted for the saved one.
 await a.page.evaluate(()=>history.replaceState({},'','/c/another-task'));
 assert.equal((await command(a,'status',{},true)).code,'page_unavailable');
 await a.page.evaluate(()=>history.replaceState({},'','/c/modern'));
 assert.equal((await command(a,'close',{text:a.text})).closed,true);
 const created=context.waitForEvent('page');const restored=await command(a,'open',{restore:true});a.page=await created;await a.page.goto(a.url);await a.page.waitForSelector('#prompt-textarea');
 assert.equal(restored.window_opened,true);assert.equal((await command(a,'status')).composer,true);
 assert.equal(await a.page.evaluate(()=>location.pathname),'/c/modern');
 console.log('PASS: project ID selection, root URL alias, unchanged-project guard, DOM send/reply, exact conversation routing and restore.');
} finally {
 if(context)await context.close();broker.kill('SIGTERM');await fs.rm(tmp,{recursive:true,force:true});
}
