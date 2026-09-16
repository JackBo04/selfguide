// This is a controlled fixture test. It does not log in to or test live ChatGPT.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import crypto from 'node:crypto';
import {spawn,spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const require=createRequire(process.env.SELFGUIDE_TEST_MODULE_ROOT || import.meta.url);
const {chromium}=require('playwright');
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const tmp=await fs.mkdtemp(path.join(os.tmpdir(),'selfguide-extension-test-'));
const project='https://chatgpt.com/g/g-p-fixture/project';
const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});
const variant=process.env.SELFGUIDE_TEST_VARIANT || 'local';
const env={...process.env,SELFGUIDE_BRIDGE_HOME:path.join(tmp,'bridge')};
const bridge=path.join(root,`skills/selfguide-${variant}/scripts/bridge.py`);
const watcher=path.join(root,`skills/selfguide-${variant}/scripts/wait_reply.py`);
const sessionScript=path.join(root,`skills/selfguide-${variant}/scripts/session.py`);
function session(...args){
 const result=spawnSync('python',[sessionScript,...args],{env,encoding:'utf8'});
 assert.equal(result.status,0,result.stderr);return JSON.parse(result.stdout);
}
const init=spawnSync('python',[bridge,'init','--project-url',project,'--port',String(port)],{env,encoding:'utf8'});
assert.equal(init.status,0,init.stderr);
const cfg=JSON.parse(await fs.readFile(path.join(tmp,'bridge/config.json'),'utf8'));
const proc=spawn('python',[bridge,'serve'],{env,stdio:'ignore'});
let context;
const wait=ms=>new Promise(r=>setTimeout(r,ms));
async function rpc(route,body,role='agent'){
 const response=await fetch(`http://127.0.0.1:${port}`+route,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+cfg[role+'_token']},body:JSON.stringify(body)});
 const data=await response.json();assert.equal(response.status,200,JSON.stringify(data));return data;
}
async function command(action,extra={},allowError=false){
 const id=crypto.randomBytes(16).toString('hex');
 await rpc('/command',{id,command:{action,expected_url:await page.url(),...extra}});
 for(let i=0;i<100;i++){
  const job=await rpc('/job',{id});
  if(job.state==='done'){if(!allowError)assert.equal(job.result.error,undefined,JSON.stringify(job.result));return job.result;}
  await wait(250);
 }
 throw Error('Fixture command timeout: '+action);
}
function runWatcher(args){
 return new Promise(resolve=>{
  const child=spawn('python',[watcher,...args],{env});let stdout='',stderr='';
  child.stdout.on('data',data=>stdout+=data);child.stderr.on('data',data=>stderr+=data);
  child.on('close',code=>resolve({code,stdout,stderr}));
 });
}
let screenshotCalls=0;
import {fixture} from './browser_fixture.mjs';
let page;
try{
 await wait(300);
 context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{channel:'chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage',`--disable-extensions-except=${path.join(root,'extension')}`,`--load-extension=${path.join(root,'extension')}`]});
 await context.route('https://chatgpt.com/**',route=>route.fulfill({contentType:'text/html',body:fixture}));
 const worker=context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
 page=await context.newPage();page.screenshot=()=>{screenshotCalls++;throw Error('Unexpected screenshot');};await page.goto(project);await page.waitForSelector('#prompt-textarea');
 await worker.evaluate(async cfg=>{
  const tabs=await chrome.tabs.query({url:'https://chatgpt.com/*'});
  await chrome.storage.local.set({endpoint:cfg.endpoint,token:cfg.token,tabId:tabs[0].id,enabled:true});
 },{endpoint:`http://127.0.0.1:${port}`,token:cfg.browser_token});
 const state=await command('status');assert.equal(state.composer,true);assert.ok(!JSON.stringify(state).includes('PRIVATE SIDEBAR'));
 const bytes=Buffer.from('random fixture marker '+crypto.randomBytes(8).toString('hex'));
 const attached=await command('attach',{file:{name:'probe.txt',mime:'text/plain',base64:bytes.toString('base64'),sha256:crypto.createHash('sha256').update(bytes).digest('hex')}});
 assert.equal(attached.file_paste_requested,true);assert.equal(attached.upload_confirmed,true);assert.ok(attached.attachments.some(a=>a.name==='probe.txt'));
 assert.equal(await page.locator('[data-testid=send-button]').getAttribute('aria-disabled'),'false','An attachment card alone is not upload readiness');
 // Match the live editor's paragraph structure and preserve intentional blank lines.
 const paragraphText='first\n\nsecond\nsoft break';
 await page.evaluate(()=>{
  window.fixtureTextarea=document.querySelector('#prompt-textarea');
  const e=document.createElement('div');e.id='prompt-textarea';e.contentEditable='true';
  window.fixtureTextarea.replaceWith(e);
  e.innerHTML='<p>first</p><p data-empty-paragraph="true"><br class="ProseMirror-trailingBreak"></p><p>second<br>soft break</p>';
 });
 assert.equal((await command('compose',{text:paragraphText})).draft_verified,true);
 assert.equal((await command('snapshot')).draft,paragraphText);
 const changedWhitespace=await command('send',{text:paragraphText.replace('\n\n','\n')},true);
 assert.equal(changedWhitespace.status,'blocked','Do not silently collapse meaningful blank lines');
 assert.equal((await command('status')).user_count,0);
 await page.evaluate(()=>document.querySelector('#prompt-textarea').replaceWith(window.fixtureTextarea));
 const taskFile=path.join(tmp,'task.txt');await fs.writeFile(taskFile,'Use only the attached fixture file. Literal delimiter: '+String.fromCharCode(96).repeat(3)+' goes here.');
 const run=session('new','--task-file',taskFile,'--workspace',path.join(tmp,'tasks'))['run'];
 const prepared=session('prepare','--run',run,'--file',taskFile);
 const text=await fs.readFile(prepared.file,'utf8');
 const composed=await command('compose',{text});assert.equal(composed.draft_verified,true);assert.equal(composed.draft,undefined);
 await page.evaluate(()=>document.querySelector('[data-testid=send-button]').setAttribute('aria-disabled','true'));
 const disabledSend=await command('send',{text},true);assert.equal(disabledSend.status,'blocked');
 assert.ok(disabledSend.error.includes('未就绪'),'Reject the disabled control before clicking, rather than timing out after an ignored click');
 assert.equal((await command('status')).user_count,0);
 assert.equal(await page.locator('#prompt-textarea').inputValue(),text);
 assert.equal(await page.evaluate(()=>window.sendClicks || 0),0,'No click while the control is disabled');
 await page.evaluate(()=>setTimeout(()=>document.querySelector('[data-testid=send-button]').setAttribute('aria-disabled','false'),1200));
 await page.evaluate(()=>window.replyDelay=6000);
 await page.evaluate(()=>{window.fencesAsBreaks=true;window.trimFenceSpace=true;});
 session('submitting','--run',run);
 const sent=await command('send',{text});assert.equal(sent.sent,true);assert.ok(sent.url.includes('/c/'));
 assert.equal(await page.evaluate(()=>window.sendClicks),1,'Click exactly once after readiness');
 session('sent','--run',run,'--url',sent.url);
 const promptFile=path.join(tmp,'prompt.txt'),stateFile=path.join(tmp,'wait.json'),replyFile=path.join(tmp,'reply.txt');
 await fs.writeFile(promptFile,text);
 const waitArgs=['--file',promptFile,'--expect-url',sent.url,'--out',stateFile,'--reply-out',replyFile,'--interval','0.15'];
 const interrupted=await runWatcher([...waitArgs,'--timeout','0.3']);
 assert.equal(interrupted.code,3,interrupted.stderr);
 const pending=JSON.parse(await fs.readFile(stateFile,'utf8'));
 assert.equal(pending.state,'timed_out');assert.equal(pending.screenshot_recommended,false);
 const waited=await runWatcher([...waitArgs,'--timeout','35','--resume']);
 assert.equal(waited.code,0,waited.stderr);
 const received=await fs.readFile(replyFile,'utf8');assert.ok(received.includes(bytes.toString()));
 assert.ok(!waited.stdout.includes(bytes.toString()));
 const watchState=JSON.parse(await fs.readFile(stateFile,'utf8'));
 assert.equal(watchState.state,'ready');assert.ok(watchState.probes>=1);assert.equal(watchState.screenshot_requests,0);
 session('reply','--run',run,'--file',replyFile,'--source','dom');
 assert.equal(session('status','--run',run).rounds[0].reply_format_valid,true);
 session('checkpoint','--run',run,'--phase','complete','--note-file',taskFile);
 const replay=await runWatcher([...waitArgs,'--timeout','1','--resume']);assert.equal(replay.code,0,replay.stderr);
 assert.equal((await command('status')).user_count,1,'Watcher must not resend a prompt');
 const reply=await command('reply',{text});assert.equal(reply.complete,true);assert.equal(reply.text,received);
 // Both observed fence renderings work, without loosening other whitespace.
 await page.evaluate(prompt=>{document.querySelector('[data-message-author-role="user"]').textContent=prompt.split(String.fromCharCode(96).repeat(3)).join('\n');},text);
 assert.equal((await command('reply-status',{text})).complete,true);
 // Rendering tolerance must not accept changed prose or another task/round.
 for (const changed of [text.replace('goes here','goes elsewhere'),text.replaceAll('round=1','round=2'),text.replaceAll(/task=[A-Za-z0-9_-]+/g,'task=other')]) {
  assert.equal((await command('reply-status',{text:changed},true)).code,'turn_mismatch');
 }
 const handoff='SELFGUIDE_REPLY_BEGIN task=fixture round=1\n路径 /tmp/a_b；原文 <value> & 中文\nSELFGUIDE_REPLY_END task=fixture round=1';
 await page.evaluate(value=>{
  const assistant=document.querySelector('[data-message-author-role="assistant"]');
  assistant.textContent='请同时核对这段块外说明。';
  const pre=document.createElement('pre'), toolbar=document.createElement('button'),code=document.createElement('code');
  toolbar.textContent='text Copy code';code.textContent=value;pre.append(toolbar,code);assistant.append(pre);
 },handoff);
 const structured=await command('reply',{text});
 assert.equal(structured.handoff_text,handoff);
 assert.ok(structured.text.includes('块外说明'));
 assert.ok(structured.text.includes('Copy code'));
 // A compact status does not return a previous large reply or the draft body.
 await page.evaluate(()=>document.querySelector('[data-message-author-role="assistant"]').append('Z'.repeat(50000)));
 const compact=await command('status'),full=await command('snapshot');
 assert.equal(compact.last_reply,undefined);assert.equal(compact.last_user,undefined);assert.equal(compact.draft,undefined);
 assert.ok(JSON.stringify(compact).length<1000);assert.ok(JSON.stringify(full).length>50000);
 // A code-block copy button alone does not mark a completed response.
 await page.evaluate(prompt=>{
  const messages=document.querySelector('#messages');
  const user=document.createElement('div');user.dataset.messageAuthorRole='user';user.textContent=prompt;messages.append(user);
 },text);
 assert.equal((await command('reply-status',{text})).reason,'no_current_reply');
 await page.evaluate(()=>{
  const article=document.createElement('article'),assistant=document.createElement('div');assistant.dataset.messageAuthorRole='assistant';assistant.textContent='partial';
  const pre=document.createElement('pre'),button=document.createElement('button');button.setAttribute('aria-label','Copy response');pre.append(button);assistant.append(pre);article.append(assistant);document.querySelector('#messages').append(article);
 });
 assert.equal((await command('reply-status',{text})).reason,'completion_marker_missing');
 await page.evaluate(()=>{
  const last=[...document.querySelectorAll('[data-message-author-role="assistant"]')].at(-1);
  const copy=document.createElement('button');copy.dataset.testid='copy-turn-action-button';last.parentElement.append(copy);
  window.changeReply=setInterval(()=>last.firstChild.textContent+='x',150);
 });
 const changing=await command('reply-status',{text});assert.equal(changing.reason,'reply_changing');assert.equal(changing.text,undefined);
 await page.evaluate(()=>clearInterval(window.changeReply));
 assert.equal((await command('reply-status',{text})).complete,true);
 const mismatch=await command('reply-status',{text:'wrong prompt'},true);
 assert.equal(mismatch.code,'turn_mismatch');assert.equal(mismatch.text,undefined);assert.equal(mismatch.screenshot_recommended,true);
 // Navigation to login/another project is reported as a diagnostic, without reading it.
 await page.goto('https://chatgpt.com/auth/login');
 const login=await command('status',{expected_url:undefined},true);
 assert.equal(login.code,'page_unavailable');assert.equal(login.screenshot_recommended,true);
 await page.goto(project);await page.waitForSelector('#prompt-textarea');
 await command('project');await page.waitForURL(project);
 assert.equal((await command('status')).user_count,0);
 await page.evaluate(()=>{const form=document.querySelector('form');form.remove();setTimeout(()=>document.body.append(form),3500);});
 assert.equal((await command('status')).composer,true,'Wait for a composer that mounts after document load');
 await page.evaluate(()=>{document.querySelector('form').remove();const button=document.createElement('button');button.textContent='Try again';document.body.append(button);});
 assert.equal((await command('status',{},true)).code,'page_render_failed');
 const diagnostic=await command('snapshot');assert.equal(diagnostic.composer,false);
 assert.equal(diagnostic.last_user,'');assert.equal(diagnostic.draft,'');
 assert.equal(screenshotCalls,0);
 console.log(JSON.stringify({variant,compact_status_bytes:Buffer.byteLength(JSON.stringify(compact)),diagnostic_snapshot_bytes:Buffer.byteLength(JSON.stringify(full)),watch_probes:watchState.probes,screenshots:screenshotCalls}));
 console.log('PASS: real extension service worker + content script + HTTP broker; compact status, upload completion, text compose/send, autonomous wait/resume, full reply, stale/partial reply rejection and login diagnostic; zero screenshots on controlled fixture.');
}finally{await context?.close();proc.kill('SIGTERM');await fs.rm(tmp,{recursive:true,force:true});}
