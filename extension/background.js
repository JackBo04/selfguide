'use strict';
const active = new Set();
let pumping = false;
let layoutQueue = Promise.resolve();
let closeQueue = Promise.resolve();
let ticker;
function startPolling() { if (!ticker) ticker = setInterval(()=>void poll(),2000); }
const MAX_ACTIVE = 4;
const SESSION = 'selfguide.session.';
const PENDING = 'selfguide.pending.';
const cleanURL = value => { const u = new URL(value); return u.origin + u.pathname; };
const lane = command => command.session || 'legacy';
function inProject(value, key) {
  const u = new URL(value);
  return u.origin === 'https://chatgpt.com' && !u.search && !u.hash &&
    new RegExp('^/g/' + key + '(?:-[^/]+)?/(project|c/[A-Za-z0-9-]+)$').test(u.pathname);
}
const fault = (code, message) => Object.assign(new Error(message), {code});
async function rpc(cfg, route, body = {}) {
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(cfg.endpoint)) throw Error('地址必须是 http://127.0.0.1:端口');
  const response = await fetch(cfg.endpoint + route, {method:'POST',
    headers:{'Content-Type':'application/json','Authorization':'Bearer ' + cfg.token},
    body:JSON.stringify(body), signal:AbortSignal.timeout(12000), credentials:'omit', redirect:'error'});
  const data = await response.json();
  if (!response.ok) throw Error(data.error || '连接失败');
  return data;
}
async function binding(session, cfg) {
  if (session === 'legacy') return {tabId:cfg.tabId};
  return (await chrome.storage.local.get(SESSION + session))[SESSION + session];
}
async function pageCommand(tabId, message) {
  try { return await chrome.tabs.sendMessage(tabId, message); }
  catch (error) {
    // Reloading the extension removes its old page listeners. Reattach without
    // refreshing a user's draft or generation. Only "no receiver" proves that
    // no command ran; a closed response port must never retry a mutation.
    if (!String(error.message).includes('Receiving end does not exist')) throw error;
    await chrome.scripting.executeScript({target:{tabId},files:['content.js']});
    return chrome.tabs.sendMessage(tabId, message);
  }
}
async function taskTab(session, cfg, info) {
  const saved = await binding(session, cfg);
  if (!saved || (session !== 'legacy' && saved.state !== 'ready') || saved.tabId === undefined) throw fault('session_not_open','该任务尚无已确认窗口；先 open --run，不能借用其他任务窗口。');
  let tab;
  try { tab = await chrome.tabs.get(saved.tabId); }
  catch { throw fault('window_closed','该任务窗口已关闭；其他任务不受影响，请恢复本任务绑定。'); }
  if (saved.windowId !== undefined && saved.windowId !== tab.windowId) throw fault('window_changed','任务标签页已移入其他窗口，请核对本任务绑定。');
  if (!(tab.url && inProject(tab.url, info.project_key)) && !(tab.status === 'loading' && tab.pendingUrl && inProject(tab.pendingUrl, info.project_key))) {
    // The unified ChatGPT UI redirects project entry pages to / and project
    // conversations to /c/id. Keep the saved project URL as a canonical alias.
    const actual = new URL(tab.url || 'https://invalid.invalid');
    const entry = saved.entryUrl && new URL(saved.entryUrl);
    const conversation = entry?.pathname.match(/\/c\/([A-Za-z0-9-]+)$/)?.[1];
    const redirected = actual.origin === 'https://chatgpt.com' && !actual.search && !actual.hash &&
      entry && inProject(saved.entryUrl,info.project_key) &&
      (conversation ? actual.pathname === '/c/' + conversation : actual.pathname === '/');
    if (!redirected) throw fault('page_unavailable','任务窗口需要登录、验证或返回已配置项目。');
    if (!conversation) {
      const context = await pageCommand(tab.id,{type:'selfguide-command',command:{action:'project-context',
        project_key:info.project_key,project_name:saved.projectName || null}});
      if (!context.project_verified) throw fault(context.code || 'project_unverified',context.error || '无法确认项目选择。');
      if (!saved.projectName) await chrome.storage.local.set({[SESSION+session]:{...saved,projectName:context.project_name}});
      tab.selfguideProjectName = context.project_name;
    }
    tab.selfguideCanonical = saved.entryUrl;
  }
  return tab;
}
async function layoutTaskWindows(cfg) {
  // Only resize windows this extension created; leave the original browser alone.
  // Keeping their content visible avoids occlusion throttling of streaming replies.
  const layout = async () => {
    const saved = await chrome.storage.local.get(null);
    const all = await chrome.windows.getAll({windowTypes:['normal']});
    const ids = new Set(Object.entries(saved).filter(([key,value])=>key.startsWith(SESSION) && value.state==='ready').map(([,value])=>value.windowId));
    const owned = all.filter(window=>ids.has(window.id)).sort((a,b)=>a.id-b.id);
    if (!owned.length) return;
    let bounds = saved['selfguide.layout.bounds'];
    if (!bounds) {
      const original = all.filter(window=>!ids.has(window.id)).sort((a,b)=>b.width*b.height-a.width*a.height)[0] || all[0];
      bounds = {left:original.left || 0,top:original.top || 0,width:original.width,height:original.height};
      await chrome.storage.local.set({'selfguide.layout.bounds':bounds});
    }
    const columns = Math.min(owned.length,Math.max(1,Math.round(Math.sqrt(owned.length * bounds.width / bounds.height))));
    const rows = Math.ceil(owned.length / columns);
    const width = Math.floor(bounds.width / columns), height = Math.floor(bounds.height / rows);
    for (const [index,window] of owned.entries()) {
      await chrome.windows.update(window.id,{state:'normal',
        left:bounds.left+(index%columns)*width,top:bounds.top+Math.floor(index/columns)*height,width,height});
    }
  };
  layoutQueue = layoutQueue.catch(()=>{}).then(layout);
  return layoutQueue;
}
async function openWindow(command, cfg, info, jobId) {
  const session = lane(command), key = SESSION + session;
  if (session === 'legacy') throw Error('独立窗口需要任务 ID。');
  const saved = await binding(session, cfg);
  if (saved) {
    try {
      const tab = await taskTab(session, cfg, info);
      await layoutTaskWindows(cfg);
      return {window_opened:true,reused:true,session,tab_id:tab.id,window_id:tab.windowId,
        url:tab.selfguideCanonical || cleanURL(tab.url && inProject(tab.url,info.project_key) ? tab.url : tab.pendingUrl)};
    } catch (error) {
      if (!command.restore || !['window_closed','session_not_open'].includes(error.code)) throw error;
      if (!/\/c\//.test(command.expected_url)) throw Error('恢复需要已登记的会话地址；新会话发送情况未确认时先处理原操作。');
    }
  }
  if (!inProject(command.expected_url, info.project_key)) throw Error('只能打开已配置项目中的任务页面。');
  // Restores reuse a confirmed conversation; an ordinary open never retries an ambiguous creation.
  await chrome.storage.local.set({[key]:{state:'opening',jobId}});
  const window = await chrome.windows.create({url:command.expected_url,type:'normal',focused:false});
  const tabs = window.tabs || await chrome.tabs.query({windowId:window.id});
  if (tabs.length !== 1 || tabs[0].id === undefined) throw Error('新窗口的标签页未确认，请检查原 open 操作。');
  await chrome.storage.local.set({[key]:{state:'ready',tabId:tabs[0].id,windowId:window.id,entryUrl:command.expected_url}});
  await layoutTaskWindows(cfg);
  return {window_opened:true,reused:false,session,tab_id:tabs[0].id,window_id:window.id,url:command.expected_url};
}
async function execute(cfg, info, job) {
  const session = lane(job.command);
  let result;
  try {
    if (job.command.action === 'open') {
      result = await openWindow(job.command, cfg, info, job.id);
    } else if (job.command.action === 'close') {
      const close = async () => {
        const key = SESSION + session, saved = await binding(session,cfg);
        if (!saved || saved.state === 'closed') {
          result = {closed:true,already_closed:true,session};
        } else {
          let tab;
          try { tab = await taskTab(session,cfg,info); }
          catch (error) {
            if (error.code !== 'window_closed') throw error;
          }
          if (tab) {
            if (tab.status === 'loading') throw fault('window_busy','任务页面仍在加载，保留窗口。');
            const state = await pageCommand(tab.id,{type:'selfguide-command',id:job.id,command:{...job.command,...(tab.selfguideCanonical ? {project_alias:tab.selfguideCanonical} : {})}});
            if (!state.close_ready) throw fault(state.code || 'window_busy',state.error || '窗口尚未确认可关闭。');
            const current = await chrome.tabs.get(tab.id);
            if (current.url !== tab.url || (tab.selfguideCanonical || current.url) !== job.command.expected_url || current.windowId !== saved.windowId) throw fault('window_changed','窗口地址或归属已改变，保留窗口。');
            const windows = await chrome.windows.getAll({windowTypes:['normal']});
            if (windows.length === 1 && (await chrome.tabs.query({windowId:current.windowId})).length === 1) {
              throw fault('last_browser_window','保留最后一个浏览器窗口，让连接继续运行。');
            }
            // Remove only this task's tab. Other tabs manually added to its window survive.
            await chrome.tabs.remove(tab.id);
          }
          await chrome.storage.local.set({[key]:{state:'closed',url:job.command.expected_url,closedAt:Date.now()}});
          result = {closed:true,already_closed:!tab,session};
        }
      };
      // Serialize the final-window check and removal across task lanes.
      closeQueue = closeQueue.catch(()=>{}).then(close);
      await closeQueue;
    } else {
      const tab = await taskTab(session, cfg, info);
      if (job.command.action === 'focus') {
        if ((tab.selfguideCanonical || cleanURL(tab.url)) !== job.command.expected_url) throw fault('wrong_page','任务地址不同，未切换窗口。');
        await chrome.windows.update(tab.windowId,{focused:true});
        await chrome.tabs.update(tab.id,{active:true});
        result = {focused:true,session,url:cleanURL(tab.url)};
      } else if (tab.status === 'loading') {
        result = {status:'waiting',reason:'page_loading'};
      } else if (job.command.action === 'project') {
        if (session !== 'legacy') throw Error('任务窗口保留原会话；新任务请使用新的任务 ID。');
        const state = await pageCommand(tab.id,{type:'selfguide-command',id:job.id,
          command:{action:'status',expected_url:cleanURL(tab.url)}});
        if (state.error || state.generating || state.draft_present || state.attachments?.length) throw Error('当前页面有生成、草稿或附件，请先处理。');
        await chrome.tabs.update(tab.id,{url:job.project_url});
        result = {navigated:true,url:job.project_url,next:'Read compact status until the composer is ready.'};
      } else {
        result = await pageCommand(tab.id,{type:'selfguide-command',id:job.id,command:{...job.command,
          ...(tab.selfguideCanonical ? {project_alias:tab.selfguideCanonical,project_name:tab.selfguideProjectName} : {})}});
        if (tab.selfguideCanonical && result.url) {
          const physical = new URL(result.url);
          const id = physical.pathname.match(/^\/c\/([A-Za-z0-9-]+)$/)?.[1];
          const canonical = id ? 'https://chatgpt.com/g/' + info.project_key + '/c/' + id : tab.selfguideCanonical;
          result = {...result,page_url:result.url,url:canonical};
          if ((result.sent || result.submitted) && id) {
            const saved = await binding(session,cfg);
            await chrome.storage.local.set({[SESSION+session]:{...saved,entryUrl:canonical}});
          }
        }
      }
    }
  } catch (error) {
    result = {status:'blocked',error:String(error.message || error),code:error.code || 'extension_unavailable',uncertain:true,screenshot_recommended:true};
  }
  // Separate durable outboxes prevent simultaneous tasks from overwriting each other.
  const key = PENDING + job.id;
  const pending = {session,response:{id:job.id,result}};
  await chrome.storage.local.set({[key]:pending});
  await rpc(cfg,'/result',pending.response);
  await chrome.storage.local.remove(key);
}
async function flushPending(cfg) {
  const saved = await chrome.storage.local.get(null), blocked = new Set();
  if (saved.pendingResult) {
    try { await rpc(cfg,'/result',saved.pendingResult);await chrome.storage.local.remove('pendingResult'); }
    catch { blocked.add('legacy'); }
  }
  for (const [key,pending] of Object.entries(saved)) {
    if (!key.startsWith(PENDING) || active.has(pending.session)) continue;
    try { await rpc(cfg,'/result',pending.response);await chrome.storage.local.remove(key); }
    catch { blocked.add(pending.session); }
  }
  return blocked;
}
async function poll() {
  if (pumping) return;
  pumping = true;
  try {
    const cfg = await chrome.storage.local.get(['endpoint','token','tabId','enabled']);
    if (!cfg.enabled || !cfg.endpoint || !cfg.token) return;
    startPolling();
    const blocked = await flushPending(cfg), info = await rpc(cfg,'/info');
    while (active.size < MAX_ACTIVE) {
      if (!(await chrome.storage.local.get('enabled')).enabled) break;
      const job = await rpc(cfg,'/poll',{exclude_sessions:[...new Set([...active,...blocked])]});
      if (job.idle) break;
      const session = lane(job.command);
      active.add(session);
      void execute(cfg,info,job).catch(async error => {
        await chrome.storage.local.set({status:'连接暂停：'+String(error.message || error)});
      }).finally(() => { active.delete(session);void poll(); });
    }
  } catch (error) { await chrome.storage.local.set({status:'连接暂停：'+String(error.message || error)}); }
  finally { pumping = false; }
}
chrome.runtime.onMessage.addListener((message,sender,respond) => {
  (async () => {
    if (message.type === 'selfguide-heartbeat') {
      // An unbound page can wake the mailbox, but can never select its target.
      if (sender.url?.startsWith('https://chatgpt.com/')) void poll();
      return {ok:true};
    }
    if (sender.tab || sender.url !== chrome.runtime.getURL('popup.html')) throw Error('Invalid configuration sender.');
    if (message.type === 'selfguide-bind') {
      const cfg = {endpoint:message.endpoint,token:message.token}, info = await rpc(cfg,'/info');
      const [tab] = await chrome.tabs.query({active:true,currentWindow:true});
      if (!tab || !inProject(tab.url || '',info.project_key)) throw Error('先打开已配置的 selfguide 项目页面，再连接。');
      const old = await chrome.storage.local.get(null);
      if (Object.entries(old).some(([key,value])=>key.startsWith(SESSION) && value.tabId===tab.id)) throw Error('这是任务专用窗口，已连接；无需重新绑定。');
      const changing = old.endpoint !== cfg.endpoint || old.token !== cfg.token;
      if (active.size || (changing && (old.pendingResult || Object.keys(old).some(key=>key.startsWith(PENDING) || key.startsWith(SESSION))))) throw Error('已有任务或未确认结果；请保留原连接。');
      await chrome.storage.local.set({...cfg,tabId:tab.id,enabled:true,status:'已连接；新任务将自动使用独立窗口'});
      await chrome.alarms.create('selfguide-poll',{periodInMinutes:0.5});
      startPolling();
      void poll();return {ok:true};
    }
    if (message.type === 'selfguide-pause') {
      clearInterval(ticker);ticker=undefined;
      await chrome.storage.local.set({enabled:false,status:'已暂停领取新操作；已领取操作完成后保存结果，登录保持不变'});return {ok:true};
    }
    throw Error('Unknown message.');
  })().then(respond,error=>respond({error:error.message}));
  return true;
});
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name==='selfguide-poll')void poll();});
chrome.runtime.onInstalled.addListener(async()=>{
  await chrome.alarms.create('selfguide-poll',{periodInMinutes:0.5});
  void poll();
});
chrome.runtime.onStartup.addListener(async()=>{
  const all=await chrome.storage.local.get(null);
  const stale=Object.fromEntries(Object.entries(all).filter(([key])=>key.startsWith(SESSION)).map(([key,value])=>[key,{...value,state:'restart_unverified'}]));
  await chrome.storage.local.set(stale);
  void poll();
});

// Recreate the short poll timer after a service-worker wake; alarms are a fallback.
chrome.storage.local.get('enabled').then(cfg=>{if(cfg.enabled)startPolling();});
