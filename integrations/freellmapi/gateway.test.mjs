import {test} from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {createGateway,normalizeEncryptionKey,startGateway} from './gateway.mjs';
const accessKey='test-access-key-at-least-24-characters';
const adminPassword='test-admin-password-only';
const basic='Basic '+Buffer.from('shadow:'+adminPassword).toString('base64');
const headers={'X-Shadow-Gateway-Key':accessKey,Authorization:'Bearer test-unified-key'};
async function fixture(run) {
 const backend=http.createServer((req,res)=>{
  if(req.url==='/api/ping'){res.writeHead(200);res.end('{"ok":true}');return;}
  let body='';req.on('data',chunk=>body+=chunk);req.on('end',()=>{
   res.setHeader('content-type','application/json');res.end(JSON.stringify({path:req.url,headers:req.headers,body}));
  });
 });
 await new Promise(resolve=>backend.listen(0,'127.0.0.1',resolve));
 const gateway=createGateway({accessKey,adminPassword,backendPort:backend.address().port});
 await new Promise(resolve=>gateway.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+gateway.address().port;
 try{await run(base,backend);}finally{
  gateway.closeAllConnections();backend.closeAllConnections();await Promise.all([new Promise(r=>gateway.close(r)),new Promise(r=>backend.close(r))]);
 }
}
test('anonymous health is minimal; dashboard and API are protected',()=>fixture(async base=>{
 const health=await fetch(base+'/healthz');assert.equal(health.status,200);assert.deepEqual(await health.json(),{ok:true,gateway:'freellmapi'});
 for(const path of ['/','/api/keys','/v1/models'])assert.equal((await fetch(base+path)).status,401);
 assert.equal((await fetch(base+'/api/keys',{headers})).status,401);
 assert.equal((await fetch(base+'/v1/models',{headers:{Authorization:'Bearer test-unified-key'}})).status,401);
 assert.equal((await fetch(base+'/v1/models',{headers:{'X-Shadow-Gateway-Key':accessKey}})).status,401);
 assert.equal((await fetch(base+'/',{headers:{Authorization:basic}})).status,200);
}));
test('inference streams its body and bearer key; edge key is never forwarded',()=>fixture(async base=>{
 const body=JSON.stringify({model:'auto:smart',messages:[{role:'user',content:'salom'}]});
 const response=await fetch(base+'/v1/chat/completions',{method:'POST',headers:{...headers,'content-type':'application/json'},body});
 assert.equal(response.status,200);const actual=await response.json();
 assert.equal(actual.body,body);assert.equal(actual.headers.authorization,'Bearer test-unified-key');
 assert.equal(actual.headers['x-shadow-gateway-key'],undefined);
}));
test('encoded traversal cannot turn an inference key into dashboard access',()=>fixture(async base=>{
 for(const path of ['/v1/../api/keys','/v1/%2e%2e/api/keys']){
  const response=await fetch(base+path,{headers});assert.equal(response.status,401);
 }
 const response=await fetch(base+'/',{headers:{Authorization:basic}});const actual=await response.json();
 assert.equal(actual.headers.authorization,undefined);
}));
test('gateway reports an unavailable backend as unhealthy',()=>fixture(async(base,backend)=>{
 await new Promise(resolve=>backend.close(resolve));
 assert.equal((await fetch(base+'/healthz')).status,503);
 assert.equal((await fetch(base+'/v1/models',{headers})).status,502);
}));
test('encryption stays stable for Render-generated secrets and existing hex keys',()=>{
 const key='a'.repeat(64);assert.equal(normalizeEncryptionKey(key),key);
 const generated='test-generated-render-secret-32chars';const normalized=normalizeEncryptionKey(generated);
 assert.match(normalized,/^[a-f0-9]{64}$/);assert.equal(normalizeEncryptionKey(generated),normalized);
 assert.notEqual(normalizeEncryptionKey(generated+'x'),normalized);
 assert.throws(()=>normalizeEncryptionKey('weak'),/ENCRYPTION_KEY/);
});
test('invalid public startup fails before a backend is spawned',async()=>{
 await assert.rejects(()=>startGateway({}),/ENCRYPTION_KEY/);
 assert.throws(()=>createGateway({accessKey:'weak',adminPassword}),/GATEWAY_ACCESS_KEY/);
 assert.throws(()=>createGateway({accessKey,adminPassword:'weak'}),/GATEWAY_ADMIN_PASSWORD/);
});

test('edge cookie preserves FreeLLMAPI dashboard bearer authentication',()=>fixture(async base=>{
 const first=await fetch(base+'/',{headers:{Authorization:basic,'X-Forwarded-Proto':'https'}});
 const setCookie=first.headers.get('set-cookie');assert(setCookie.includes('HttpOnly'));assert(setCookie.includes('Secure'));assert(setCookie.includes('SameSite=Strict'));
 const cookie=setCookie.split(';')[0];
 const response=await fetch(base+'/api/keys',{headers:{Cookie:cookie,Authorization:'Bearer dashboard-session'}});
 assert.equal(response.status,200);const actual=await response.json();assert.equal(actual.headers.authorization,'Bearer dashboard-session');
 assert(!actual.headers.cookie?.includes('shadow_gateway_edge'));
 assert.equal((await fetch(base+'/v1/models',{headers:{Cookie:cookie,Authorization:'Bearer dashboard-session'}})).status,401);
 assert.equal((await fetch(base+'/api/keys',{headers:{Cookie:'shadow_gateway_edge=0000000000.forged',Authorization:'Bearer dashboard-session'}})).status,401);
}));
