/** Private FreeLLMAPI backend with an authenticated public edge for Render. */
import http from 'node:http';
import {timingSafeEqual,createHash,createHmac} from 'node:crypto';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';

function sameSecret(value, expected) {
  if(typeof value!=='string')return false;
  const left=Buffer.from(value),right=Buffer.from(expected);
  return left.length===right.length&&timingSafeEqual(left,right);
}
function reply(res,status,body,headers={}) {
  res.writeHead(status,{'content-type':'application/json','cache-control':'no-store',...headers});
  res.end(JSON.stringify(body));
}
export function createGateway({accessKey,adminPassword,backendPort=3001,requestTimeout=120000}) {
  if(!accessKey||accessKey.length<24)throw new Error('GATEWAY_ACCESS_KEY must contain at least 24 characters');
  if(!adminPassword||adminPassword.length<16)throw new Error('GATEWAY_ADMIN_PASSWORD must contain at least 16 characters');
  const cookieName='shadow_gateway_edge';
  const sign=expires=>createHmac('sha256',accessKey).update('dashboard:'+expires+':'+adminPassword).digest('hex');
  function cookieValid(raw) {
    const value=(raw||'').split(';').map(v=>v.trim()).find(v=>v.startsWith(cookieName+'='))?.slice(cookieName.length+1);
    if(!value)return false;
    const [expires,signature]=value.split('.'),deadline=Number(expires),now=Math.floor(Date.now()/1000);
    return /^\d{10}$/.test(expires)&&deadline>now&&deadline<=now+28860&&sameSecret(signature,sign(expires));
  }
  return http.createServer(async(req,res)=>{
    res.setHeader('x-content-type-options','nosniff');
    res.setHeader('strict-transport-security','max-age=31536000');
    if(!req.url?.startsWith('/')||req.url.startsWith('//'))return reply(res,400,{error:'invalid_path'});
    const pathname=new URL(req.url,'http://gateway.local').pathname;
    if(pathname==='/healthz') {
      try {
        const response=await fetch(`http://127.0.0.1:${backendPort}/api/ping`,{signal:AbortSignal.timeout(2000)});
        return reply(res,response.ok?200:503,{ok:response.ok,gateway:'freellmapi'});
      }catch{return reply(res,503,{ok:false,gateway:'freellmapi'});}
    }
    const inference=pathname==='/v1'||pathname.startsWith('/v1/');
    if(inference) {
      // The built-in Playground sends the unified key and the signed browser
      // cookie, not Shadow's private edge header. Upstream still validates the
      // actual unified key; a dashboard session alone cannot invoke inference.
      const playground=cookieValid(req.headers.cookie)&&/^Bearer\s+freellmapi-\S+$/i.test(req.headers.authorization||'');
      if(!sameSecret(req.headers['x-shadow-gateway-key'],accessKey)&&!playground)return reply(res,401,{error:'gateway_access_required'});
      if(!/^Bearer\s+\S+$/i.test(req.headers.authorization||''))return reply(res,401,{error:'unified_key_required'});
    } else {
      const header=req.headers.authorization||'';
      const value=header.startsWith('Basic ')?Buffer.from(header.slice(6),'base64').toString('utf8'):'';
      const colon=value.indexOf(':');
      const basicValid=value.slice(0,colon)==='shadow'&&sameSecret(value.slice(colon+1),adminPassword);
      if(!basicValid&&!cookieValid(req.headers.cookie))
        return reply(res,401,{error:'dashboard_access_required'},{'www-authenticate':'Basic realm="Shadow FreeLLMAPI", charset="UTF-8"'});
      if(basicValid) {
        const expires=String(Math.floor(Date.now()/1000)+28800);
        const secure=req.headers['x-forwarded-proto']==='https'||req.socket.encrypted?'; Secure':'';
        res.setHeader('set-cookie',`${cookieName}=${expires}.${sign(expires)}; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800${secure}`);
      }
    }
    const headers={...req.headers};
    for(const name of ['connection','upgrade','proxy-authorization','proxy-authenticate','x-shadow-gateway-key'])delete headers[name];
    if(!inference&&headers.authorization?.startsWith('Basic '))delete headers.authorization;
    if(headers.cookie)headers.cookie=headers.cookie.split(';').filter(v=>!v.trim().startsWith(cookieName+'=')).join(';');
    // Only this edge can reach the loopback backend; do not accept spoofed
    // forwarding chains from clients. Render supplies the final client hop.
    const forwarded=req.headers['x-forwarded-for'];
    headers['x-forwarded-for']=typeof forwarded==='string'?forwarded.split(',').at(-1).trim():req.socket.remoteAddress;
    headers['x-forwarded-proto']=req.headers['x-forwarded-proto']==='https'?'https':'http';
    const upstream=http.request({hostname:'127.0.0.1',port:backendPort,path:req.url,method:req.method,headers},response=>{
      const responseHeaders={...response.headers};delete responseHeaders.connection;
      const edgeCookie=res.getHeader('set-cookie');
      if(edgeCookie&&responseHeaders['set-cookie'])responseHeaders['set-cookie']=[edgeCookie,...responseHeaders['set-cookie']];
      res.writeHead(response.statusCode||502,responseHeaders);response.pipe(res);
      response.on('error',()=>res.destroy());
    });
    upstream.setTimeout(requestTimeout,()=>upstream.destroy(new Error('gateway_timeout')));
    upstream.on('error',()=>{if(!res.headersSent)reply(res,502,{error:'gateway_upstream_unavailable'});else res.destroy();});
    req.on('aborted',()=>upstream.destroy());
    res.on('close',()=>{if(!res.writableFinished)upstream.destroy();});
    req.pipe(upstream);
  });
}

export function normalizeEncryptionKey(value) {
  if(typeof value!=='string'||value.length<32)throw new Error('ENCRYPTION_KEY must contain at least 32 characters');
  return /^[a-fA-F0-9]{64}$/.test(value)?value:createHash('sha256').update(value).digest('hex');
}

export async function startGateway(env=process.env) {
  const encryptionKey=normalizeEncryptionKey(env.ENCRYPTION_KEY);
  if(!env.GATEWAY_ADMIN_EMAIL)throw new Error('GATEWAY_ADMIN_EMAIL is required');
  const port=Number(env.PORT||10000);
  if(!Number.isInteger(port)||port<1||port>65535||port===3001)throw new Error('PORT must be valid and different from the private backend port');
  const server=createGateway({accessKey:env.GATEWAY_ACCESS_KEY,adminPassword:env.GATEWAY_ADMIN_PASSWORD});
  // Only bootstrap the initial account; upstream ignores this once claimed.
  // Do not overwrite the owner's provider keys or routing settings on restart.
  const backend=spawn(process.execPath,['server/dist/index.js'],{
    cwd:'/app',stdio:'inherit',
    env:{...env,ENCRYPTION_KEY:encryptionKey,PORT:'3001',HOST:'127.0.0.1',TRUST_PROXY:'loopback',
      NODE_OPTIONS:'--max-old-space-size=256',
      FREEAPI_CONFIG_JSON:JSON.stringify({admin:{email:env.GATEWAY_ADMIN_EMAIL,password:env.GATEWAY_ADMIN_PASSWORD}})},
  });
  let closing=false;
  function shutdown(code) {
    if(closing)return;closing=true;
    server.close();server.closeIdleConnections();backend.kill('SIGTERM');
    const deadline=setTimeout(()=>{backend.kill('SIGKILL');process.exit(code);},8000);deadline.unref();
    backend.once('exit',()=>{clearTimeout(deadline);process.exit(code);});
  }
  backend.on('error',()=>shutdown(1));
  backend.on('exit',()=>{if(!closing){server.close();server.closeAllConnections();process.exit(1);}});
  server.on('error',()=>shutdown(1));
  process.once('SIGTERM',()=>shutdown(0));process.once('SIGINT',()=>shutdown(0));
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,'0.0.0.0',resolve);});
  console.log(`Protected FreeLLMAPI gateway listening on port ${port}`);
  return {server,backend};
}
if(process.argv[1]&&fileURLToPath(import.meta.url)===process.argv[1]) {
  startGateway().catch(error=>{console.error(error.message);process.exit(1);});
}
