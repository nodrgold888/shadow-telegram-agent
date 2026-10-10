/** Verify the official signed catalog before updating Shadow's bundled snapshot. */
import {createPublicKey,verify} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
const root=new URL('../shadow/data/',import.meta.url);
const key=createPublicKey('-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAq9yv4+3EeyMHKsfVYBhkcz1lYgIXSUeHNnN6tNgYX3k=\n-----END PUBLIC KEY-----\n');
const check=process.argv.includes('--check');
let bytes,signature;
if(check){bytes=await readFile(new URL('freellmapi-catalog.json',root));signature=(await readFile(new URL('freellmapi-catalog.sig',root),'utf8')).trim();}
else{
 const response=await fetch('https://api.freellmapi.co/v1/latest',{signal:AbortSignal.timeout(20000)});
 if(!response.ok)throw new Error('Catalog HTTP '+response.status);
 signature=response.headers.get('x-catalog-signature');
 const chunks=[];let length=0;
 for await(const chunk of response.body){length+=chunk.length;if(length>2000000)throw new Error('Catalog too large');chunks.push(chunk);}
 bytes=Buffer.concat(chunks);
}
if(!signature||!verify(null,bytes,key,Buffer.from(signature,'base64')))throw new Error('Catalog signature is invalid');
const data=JSON.parse(bytes);
if(typeof data.version!=='string'||!Array.isArray(data.models)||!Array.isArray(data.platforms)||data.models.length>5000)throw new Error('Invalid catalog shape');
if(!check){await writeFile(new URL('freellmapi-catalog.json',root),bytes);await writeFile(new URL('freellmapi-catalog.sig',root),signature+'\n');}
console.log(`Verified FreeLLMAPI catalog ${data.version}: ${data.models.length} model endpoints`);
