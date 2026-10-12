import test from 'node:test';
import assert from 'node:assert/strict';
import net from 'node:net';
import http from 'node:http';
import {MODEL,REVISION,RUNTIME,validate,makeServer,generatorAdvice} from './advisor.mjs';

test('bounded fixed-model request prevents arbitrary model loading',()=>{
  for(const body of [null,[],{}, {model:MODEL,stream:true,prompt:'test'}, {model:'other',stream:false,prompt:'test'},
    {model:MODEL,stream:false,prompt:'x'.repeat(2049)}])assert.throws(()=>validate(body));
  assert.equal(validate({model:MODEL,stream:false,prompt:'test'}),'test');
});
test('generation tokens bounded; no authorization is emitted by adapter',async()=>{
  let received;
  const infer=generatorAdvice(async(messages,options)=>{received={messages,options};return [{generated_text:[{role:'assistant',content:'Check dataset license.'}]}];});
  assert.equal(await infer('dataset'),'Check dataset license.');
  assert.equal(received.options.max_new_tokens,40);
  assert.equal(received.options.do_sample,false);
  assert.match(received.messages[0].content,/Never authorize/);
});
test('loopback API labels provenance and refuses browsers, bad Host, oversized input',async()=>{
  let calls=0;const server=makeServer(async()=>{calls++;return 'Check dataset license.';});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const url=`http://127.0.0.1:${server.address().port}/api/generate`;
  const body=JSON.stringify({model:MODEL,stream:false,prompt:'dataset'});
  try{
    const good=await fetch(url,{method:'POST',body});const result=await good.json();
    assert.equal(good.status,200);assert.equal(result.runtime,RUNTIME);assert.equal(result.revision,REVISION);
    assert.equal(result.advisory_only,true);assert.equal(result.approved,undefined);
    assert.equal((await fetch(url,{method:'POST',headers:{Origin:'http://attacker.invalid'},body})).status,403);
    const hostCode=await new Promise((resolve,reject)=>{
      const req=http.request(url,{method:'POST',headers:{Host:'attacker.invalid'}},res=>{res.resume();resolve(res.statusCode);});
      req.on('error',reject);req.end(body);
    });assert.equal(hostCode,403);
    assert.equal((await fetch(url,{method:'POST',body:'x'.repeat(4097)})).status,413);
    assert.equal(calls,1);
    const reply=await new Promise((resolve,reject)=>{
      const socket=net.connect(server.address().port,'127.0.0.1');let data='';
      socket.on('error',reject);socket.on('data',chunk=>data+=chunk.toString());socket.on('end',()=>resolve(data));
      socket.on('connect',()=>socket.end(`POST /api/generate HTTP/1.1\r\nHost:127.0.0.1:${server.address().port}\r\nHost:evil.invalid\r\nContent-Length:0\r\nConnection:close\r\n\r\n`));
    });assert.match(reply,/403/);
  }finally{await new Promise(resolve=>server.close(resolve));}
});
test('one active inference; concurrent request fails without a second model call',async()=>{
  let release,enter;const entered=new Promise(resolve=>enter=resolve);
  const pending=new Promise(resolve=>release=resolve);let calls=0;
  const server=makeServer(async()=>{calls++;enter();await pending;return 'Check seller license.';});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const url=`http://127.0.0.1:${server.address().port}/api/generate`;
  const options={method:'POST',body:JSON.stringify({model:MODEL,stream:false,prompt:'dataset'})};
  try{
    const first=fetch(url,options);await entered;
    assert.equal((await fetch(url,options)).status,503);release();assert.equal((await first).status,200);assert.equal(calls,1);
  }finally{release();await new Promise(resolve=>server.close(resolve));}
});
