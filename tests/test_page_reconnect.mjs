// Dispatch behavior: only absence of a receiver permits injection + dispatch.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
const source=await fs.readFile(new URL('../extension/background.js',import.meta.url),'utf8');
const helper=source.slice(source.indexOf('async function pageCommand('),source.indexOf('async function taskTab('));
async function scenario(error,injectError) {
 const calls=[];
 const chrome={tabs:{sendMessage:async(id,message)=>{
  calls.push(['dispatch',id,message]);
  if(calls.length===1 && error)throw Error(error);
  return {sent:true};
 }},scripting:{executeScript:async args=>{calls.push(['inject',args]);if(injectError)throw Error(injectError);}}};
 const context=vm.createContext({chrome});vm.runInContext(helper,context);
 let result,failure;try{result=await context.pageCommand(42,{command:{action:'send',text:'one message'}});}catch(e){failure=e.message;}
 return {calls,result,failure};
}
let s=await scenario();assert.equal(s.result.sent,true);assert.equal(s.calls.length,1);
s=await scenario('Could not establish connection. Receiving end does not exist.');
assert.equal(s.result.sent,true);assert.deepEqual(s.calls.map(x=>x[0]),['dispatch','inject','dispatch']);
assert.equal(s.calls[1][1].target.tabId,42);assert.equal(s.calls[1][1].files[0],'content.js');
assert.deepEqual(s.calls[0],s.calls[2]);
for(const error of ['The message port closed before a response was received.','Connection interrupted after send']) {
 s=await scenario(error);assert.equal(s.failure,error);assert.equal(s.calls.length,1);
}
s=await scenario('Receiving end does not exist','Cannot access tab');
assert.equal(s.failure,'Cannot access tab');assert.equal(s.calls.length,2);
console.log('PASS: reconnect only when no receiver exists; uncertain dispatch is never repeated.');
