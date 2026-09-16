'use strict';
(() => {
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const norm = value => value.replace(/\r\n/g,'\n').trim();
  const visible = el => el && (el.getClientRects().length > 0) && getComputedStyle(el).visibility !== 'hidden';
  const editor = () => [...document.querySelectorAll('#prompt-textarea,textarea[data-testid="prompt-textarea"],div[contenteditable="true"][role="textbox"]')].find(visible);
  const userMessages = () => [...document.querySelectorAll('[data-message-author-role="user"]')];
  const assistantMessages = () => [...document.querySelectorAll('[data-message-author-role="assistant"]')];
  const text = el => el ? (/^(TEXTAREA|INPUT)$/.test(el.tagName) ? el.value : el.innerText) : '';
  function draftText(el) {
    // ProseMirror paragraphs have CSS margins: innerText invents extra newlines.
    // Recover logical paragraph/BR boundaries without collapsing real blank lines.
    const nodes = [...(el?.childNodes || [])];
    if (!nodes.length || !nodes.every(node => node.nodeType === Node.ELEMENT_NODE && node.tagName === 'P')) return text(el);
    return nodes.map(node => {
      const copy = node.cloneNode(true);
      copy.querySelectorAll('br.ProseMirror-trailingBreak').forEach(br => br.remove());
      copy.querySelectorAll('br').forEach(br => br.replaceWith(document.createTextNode('\n')));
      return copy.textContent;
    }).join('\n');
  }
  const userContent = el => el?.querySelector('[data-testid="collapsible-user-message-content"]') || el;
  const userMatches = (el, expected) => {
    const content = userContent(el);
    // The sent bubble may collapse blank paragraphs. The composer still requires
    // the exact original draft; this fallback only compares the rendered message.
    const rendered = value => norm(value).replace(/\n{2,}/g,'\n');
    if (!content) return false;
    const values = [text(content), content.textContent || ''];
    if (values.some(value => rendered(value) === rendered(expected))) return true;
    // ChatGPT can render a literal triple-backtick run as a line break. Accept
    // only that observed transformation, with the same task/round markers and
    // all remaining text intact. Never relax the composer comparison.
    const markers = value => [...value.matchAll(/^SELFGUIDE_REPLY_(BEGIN|END) task=([A-Za-z0-9_-]+) round=(\d+)[ \t]*$/gm)];
    const pair = markers(expected).slice(-2);
    if (pair.length !== 2 || pair[0][1] !== 'BEGIN' || pair[1][1] !== 'END' ||
        pair[0][2] !== pair[1][2] || pair[0][3] !== pair[1][3]) return false;
    const fencesAsBreaks = [
      rendered(expected.replace(/(?<!`)```(?!`)/g,'\n')),
      // A fence rendered as a block boundary can also consume its following
      // horizontal whitespace. Do not strip indentation anywhere else.
      rendered(expected.replace(/(?<!`)```(?!`)[ \t]*/g,'\n'))
    ];
    return values.some(value => {
      const actual = markers(value).slice(-2);
      return actual.length === 2 && actual.every((mark,i)=>mark[0] === pair[i][0]) && fencesAsBreaks.includes(rendered(value));
    });
  };
  const button = selectors => [...document.querySelectorAll(selectors)].find(visible);
  const stop = () => button('[data-testid="stop-button"],button[aria-label="Stop generating"],button[aria-label="停止生成"]');
  const sendButton = () => button('[data-testid="send-button"],button[aria-label="Send prompt"],button[aria-label="Send message"],button[aria-label="发送提示"],button[aria-label="发送消息"]');
  const enabled = el => !!el && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
  function composer() { const e = editor(); return e?.closest('form') || e?.parentElement?.parentElement; }
  function attachments() {
    const scope = composer();
    if (!scope) return [];
    return [...scope.querySelectorAll('[data-testid*="attachment"],[data-testid*="file"],button[aria-label*="Remove"],button[aria-label*="移除"]')]
      .filter(visible).map(el => ({name:el.getAttribute('aria-label') || el.innerText || el.textContent || '', busy:!!el.querySelector('[role="progressbar"],.animate-spin')}));
  }
  function snapshot() {
    const users = userMessages(), assistants = assistantMessages(), e = editor();
    return {url:location.origin + location.pathname,composer:!!e,draft:draftText(e),generating:!!stop(),
      user_count:users.length,assistant_count:assistants.length,
      last_user:text(userContent(users.at(-1))),last_reply:text(assistants.at(-1)),attachments:attachments(),
      notices:[...document.querySelectorAll('[role="alert"]')].filter(visible).map(el=>el.innerText).slice(-3)};
  }
  function fault(code, message) {
    return Object.assign(new Error(message), {code});
  }
  const retryPage = () => [...document.querySelectorAll('button')].some(el=>visible(el) && /^(Try again|Retry|重试|再试一次)$/i.test(text(el).trim()));
  function compact() {
    const e = editor();
    const labels = [...document.querySelectorAll('button,[role="button"]')].filter(visible)
      .map(el => text(el).trim()).filter(value => /^(Extra High|xhigh|Pro)$/i.test(value));
    return {status:'ready',url:location.origin + location.pathname,composer:!!e,draft_present:!!norm(draftText(e)),
      generating:!!stop(),user_count:userMessages().length,assistant_count:assistantMessages().length,
      attachments:attachments(),thinking_label:labels.length === 1 ? labels[0] : null,
      notices:[...document.querySelectorAll('[role="alert"]')].filter(visible).map(el=>text(el).slice(0,300)).slice(-3)};
  }
  function requirePage(command, needsEditor = true) {
    if (location.origin !== 'https://chatgpt.com' || location.search || location.hash ||
        (command.expected_url && location.href !== command.expected_url)) throw fault('wrong_page','页面地址不符，未执行。');
    if (needsEditor && !editor()) {
      const retry = retryPage();
      throw fault(retry ? 'page_render_failed' : 'page_unavailable', retry
        ? '网页显示重试页面且没有输入框；核对本任务发送状态后仅恢复本窗口，不要改用其他任务窗口。'
        : '没有找到聊天输入框；可能需要手动登录、验证或适配网页。');
    }
  }
  function replyCandidate(command) {
    requirePage(command);
    const lastUser = userMessages().at(-1), last = assistantMessages().at(-1);
    if (!userMatches(lastUser, command.text || '')) throw fault('turn_mismatch','最近用户消息与本轮不同，未读取正文。');
    if (stop()) return {status:'waiting',reason:'generating'};
    if (!last || !(lastUser.compareDocumentPosition(last) & Node.DOCUMENT_POSITION_FOLLOWING)) return {status:'waiting',reason:'no_current_reply'};
    const turn = last.closest('article,[data-testid^="conversation-turn-"],.agent-turn') || last.parentElement;
    const copy = [...(turn?.querySelectorAll('[data-testid="copy-turn-action-button"],button[aria-label="Copy response"],button[aria-label="复制回复"]') || [])]
      .find(el => !el.closest('pre'));
    if (!copy) return {status:'waiting',reason:'completion_marker_missing'};
    const value = text(last);
    if (!value.trim()) return {status:'waiting',reason:'empty_reply'};
    return {status:'candidate',element:last,text:value};
  }
  async function readReply(command) {
    const first = replyCandidate(command);
    if (first.status === 'waiting') return first;
    await pause(1200);
    const second = replyCandidate(command);
    if (second.status === 'waiting') return second;
    if (first.element !== second.element || first.text !== second.text) return {status:'waiting',reason:'reply_changing'};
    const handoffs = [...second.element.querySelectorAll('pre code')].map(el => el.textContent)
      .filter(value => value.trim().startsWith('SELFGUIDE_REPLY_BEGIN '));
    return {url:location.origin + location.pathname,status:'ready',text:second.text,complete:true,source:'dom',
      ...(handoffs.length === 1 ? {handoff_text:handoffs[0]} : {})};
  }
  async function run(command) {
    // A completed document can still be mounting the chat composer. Wait in
    // this one operation, without screenshots or another agent-driven probe.
    requirePage(command, false);
    const readyDeadline = Date.now() + 10000;
    while (command.action !== 'snapshot' && !editor() && !retryPage() && Date.now() < readyDeadline) {
      await pause(250);
      requirePage(command, false);
    }
    requirePage(command, command.action !== 'snapshot');
    const e = editor();
    if (command.action === 'status') return compact();
    if (command.action === 'snapshot') return snapshot(); // Explicit diagnostic only.
    if (command.action === 'close') {
      const state = compact();
      if (state.generating || state.draft_present || state.attachments.length) throw fault('window_busy','任务窗口有生成、草稿或附件，保留窗口。');
      if (!userMatches(userMessages().at(-1),command.text || '')) throw fault('turn_mismatch','窗口中最新消息与已完成任务不同，保留窗口。');
      return {close_ready:true,url:state.url};
    }
    if (command.action === 'reply' || command.action === 'reply-status') return readReply(command);
    if (stop()) throw Error('当前回复仍在生成。');
    if (command.action === 'compose') {
      if (norm(draftText(e)) && norm(draftText(e)) !== norm(command.text)) throw Error('输入框已有不同草稿，未覆盖。');
      if (!norm(draftText(e))) {
        e.focus();
        if (e.tagName === 'TEXTAREA') {
          Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(e,command.text);
          e.dispatchEvent(new Event('input',{bubbles:true}));
        } else {
          const selection = getSelection(), range = document.createRange();
          range.selectNodeContents(e); selection.removeAllRanges(); selection.addRange(range);
          if (!document.execCommand('insertText',false,command.text)) throw Error('编辑器未接受文字；检查草稿后再处理。');
        }
      }
      await pause(300);
      if (norm(draftText(e)) !== norm(command.text)) throw Error('草稿文字核对失败，未发送。');
      return {...compact(),draft_verified:true};
    }
    if (command.action === 'send') {
      if (norm(draftText(e)) !== norm(command.text)) throw Error('草稿与本轮记录不一致，未发送。');
      // React may enable the button after the draft and attachment have rendered.
      // Wait before the single click; an unconfirmed click is never retried here.
      const readyDeadline = Date.now() + 20000;
      let send;
      while (true) {
        requirePage(command);
        if (norm(draftText(editor())) !== norm(command.text)) throw Error('草稿已改变，未发送。');
        send = sendButton();
        if (enabled(send) && !attachments().some(a=>a.busy) && !composer()?.querySelector('[role="progressbar"],.animate-spin')) break;
        if (Date.now() >= readyDeadline) throw Error('发送按钮未就绪或附件仍在上传。');
        await pause(300);
      }
      const baseline = userMessages().length;
      send.click();
      const deadline = Date.now() + 20000;
      while (Date.now() < deadline) {
        const state = snapshot();
        if (state.user_count > baseline && userMatches(userMessages().at(-1),command.text) && /\/c\//.test(state.url)) return {sent:true,url:state.url,user_count:state.user_count};
        await pause(300);
      }
      throw Error('发送结果未确认；检查网页和任务记录，不能直接重发。');
    }
    if (command.action === 'attach') {
      const f = command.file;
      const previousNames = new Set(attachments().map(a=>a.name));
      const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
      const dot = f.name.lastIndexOf('.');
      const stem = dot > 0 ? f.name.slice(0,dot) : f.name, suffix = dot > 0 ? f.name.slice(dot) : '';
      // ChatGPT may rename a repeated project filename to name(2).ext.
      const filename = new RegExp('(?:^|[\\s:/])'+escape(stem)+'(?:\\s*\\(\\d+\\))?'+escape(suffix)+'(?:$|\\s)');
      const bytes = Uint8Array.from(atob(f.base64), c => c.charCodeAt(0));
      const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');
      if (hash !== f.sha256) throw Error('附件传输校验失败。');
      const transfer = new DataTransfer();
      transfer.items.add(new File([bytes],f.name,{type:f.mime}));
      const input = [...document.querySelectorAll('input[type="file"]')].find(x=>!x.disabled);
      if (input) { input.files=transfer.files; input.dispatchEvent(new Event('change',{bubbles:true})); }
      else { e.focus(); e.dispatchEvent(new ClipboardEvent('paste',{clipboardData:transfer,bubbles:true,cancelable:true})); }
      const deadline = Date.now() + 20000;
      while (Date.now() < deadline) {
        requirePage(command);
        const state = compact();
        const added = state.attachments.find(a=>!previousNames.has(a.name) && filename.test(a.name));
        if (added && !state.attachments.some(a=>a.busy) &&
            !composer()?.querySelector('[role="progressbar"],.animate-spin')) {
          return {...state,file_paste_requested:true,original_name:f.name,observed_name:added.name,sha256:hash,upload_confirmed:true};
        }
        await pause(300);
      }
      throw fault('upload_unconfirmed','附件卡片或上传完成状态未确认，请检查一次页面，不要重复上传。');
    }
    throw Error('Unsupported action.');
  }
  let active = false;
  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message.type !== 'selfguide-command') return;
    if (active) { respond({error:'已有网页操作执行中。'}); return; }
    active = true;
    run(message.command).then(respond,error=>respond({status:'blocked',error:error.message,code:error.code || 'dom_operation_failed',screenshot_recommended:true})).finally(()=>{active=false;});
    return true;
  });
  setInterval(() => { chrome.runtime.sendMessage({type:'selfguide-heartbeat'}).catch(()=>{}); },2000);
})();
