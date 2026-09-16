export const fixture=`<!doctype html><title>SelfGuide test fixture</title><aside>PRIVATE SIDEBAR SHOULD NOT BE READ</aside><button>Extra High</button><main id="messages"></main>
<form><textarea id="prompt-textarea"></textarea><input type="file" hidden><div id="attachments"></div><button type="button" data-testid="send-button">Send</button></form>
<script>
let uploaded='';
document.querySelector('input[type=file]').onchange=async e=>{const send=document.querySelector('[data-testid=send-button]');send.setAttribute('aria-disabled','true');uploaded=await e.target.files[0].text();document.querySelector('#attachments').innerHTML='<svg data-testid="file-icon" width="16" height="16"><path d="M0 0h16v16H0z"/></svg><div data-testid="attachment-card">'+(window.renameUploads ? e.target.files[0].name.replace('.txt','(2).txt') : e.target.files[0].name)+'<span role="progressbar"></span></div>';setTimeout(()=>{document.querySelector('[role=progressbar]').remove();send.setAttribute('aria-disabled','false');},600);};
document.querySelector('[data-testid=send-button]').onclick=()=>{
 window.sendClicks=(window.sendClicks || 0)+1;
 if(document.querySelector('[data-testid=send-button]').getAttribute('aria-disabled')==='true')return;
 const input=document.querySelector('textarea'),submitted=input.value;const article=document.createElement('article');const user=document.createElement('div');user.dataset.messageAuthorRole='user';user.textContent=input.value;article.append(user);document.querySelector('main').append(article);input.value='';document.querySelector('#attachments').innerHTML='';history.replaceState({},'', '/g/g-p-fixture-selfguide/c/'+(window.conversationId || 'fixture-1'));
 if(window.collapsedMessage){const content=document.createElement('div');content.dataset.testid='collapsible-user-message-content';content.textContent=user.textContent.replace(/\\n{3,}/g,'\\n\\n');user.replaceChildren(content);const toggle=document.createElement('button');toggle.dataset.testid='collapsible-user-message-toggle';toggle.textContent='Show more';user.append(toggle);}
 if(window.fencesAsBreaks)user.textContent=user.textContent.replace(new RegExp(String.fromCharCode(96).repeat(3)+(window.trimFenceSpace?'[ \\t]*':''),'g'),'\\n');
 const stop=document.createElement('button');stop.dataset.testid='stop-button';document.body.append(stop);
 setTimeout(()=>{const article=document.createElement('section');article.dataset.testid='conversation-turn-2';const assistant=document.createElement('div');assistant.dataset.messageAuthorRole='assistant';
 const lines=submitted.split('\\n'),begin=lines.find(line=>line.startsWith('SELFGUIDE_REPLY_BEGIN ')),end=lines.find(line=>line.startsWith('SELFGUIDE_REPLY_END '));
 const pre=document.createElement('pre'),code=document.createElement('code');code.textContent=begin+'\\nFIXTURE_ACCEPTED '+uploaded+'\\n'+end;pre.append(code);assistant.append(pre);
 article.append(assistant);const copy=document.createElement('button');copy.dataset.testid='copy-turn-action-button';copy.textContent='Copy';article.append(copy);document.querySelector('main').append(article);stop.remove();},window.replyDelay || 400);
};
</script>`;
