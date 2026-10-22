import {vinValid,parseProduct,classify} from './analysis';
export {vinValid,parseProduct,classify} from './analysis';
import { env } from 'cloudflare:workers';

export const defaults={maxPrice:36000,idealPrice:31000,radius:50,preferredMileage:50000,fsdMonthly:99,ownershipMonths:36,intervalMinutes:120};

export function db(){if(!env.DB)throw Error('Database unavailable');return env.DB}

export async function read(id:string,fallback:any=null){const r=await db().prepare('SELECT body FROM records WHERE id=?').bind(id).first<{body:string}>();return r?JSON.parse(r.body):fallback}

export async function write(id:string,body:any){await db().prepare('INSERT INTO records(id,body,updated) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,updated=excluded.updated').bind(id,JSON.stringify(body),Date.now()).run()}

export async function event(kind:string,message:string){await db().prepare('INSERT INTO events(at,kind,message) VALUES(?,?,?)').bind(Date.now(),kind,message).run()}

const base='https://www.autohubtexas.com';

export async function scan(){const token=crypto.randomUUID(),now=Date.now();const lock=await db().prepare('INSERT INTO locks(id,until,token) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET until=excluded.until,token=excluded.token WHERE locks.until<? RETURNING token').bind('scan',now+1800000,token,now).first<{token:string}>();if(lock?.token!==token)return {status:'already_running'};

 const prefs=await read('settings',defaults),previous=await read('source:autohub',{});if(previous.nextScan>now){await db().prepare('DELETE FROM locks WHERE id=? AND token=?').bind('scan',token).run();return {status:'not_due',nextScan:previous.nextScan};}

 let requests=0,observations=0;const start=Date.now();let source:any={...previous,name:'Autohub Texas',status:'SCANNING',started:start,requests:previous.requests||0,observations:previous.observations||0,scans:previous.scans||0,completed:0,queued:0};await write('source:autohub',source);await event('scan','Starting dealer inventory scan');

 const fetchPage=async(url:string)=>{requests++;source={...source,current:url,requests:(previous.requests||0)+requests};await write('source:autohub',source);const r=await fetch(url,{headers:{'User-Agent':'TeslaDealHunter/1.0 (personal inventory research)'},redirect:'manual',signal:AbortSignal.timeout(20000)});if(!r.ok)throw Error('Source HTTP '+r.status);const body=await r.text();if(body.length>6000000)throw Error('Source response too large');return body};

 try{const robots=await fetchPage(base+'/robots.txt');

 // Fail closed on changes to the audited wildcard group, not unrelated bot groups.

 const wildcard=robots.split(/User-agent:/i).find(s=>s.trimStart().startsWith('*'))||'';

 if(!/Allow:\s*\//i.test(wildcard)||/Disallow:\s*\/(?:\s|$)/i.test(wildcard))throw Error('Robots rules need review');

 const index=await fetchPage(base+'/inventory');const links=[...new Set([...index.matchAll(/href="([^"<>]*\/product-page\/[^"<>]*)"/g)].map(m=>m[1]))].filter(u=>u.startsWith(base+'/product-page/')&&/tesla-model-(3|y)-/i.test(u));if(!links.length||links.length>40)throw Error('Inventory structure changed');source.queued=links.length;await write('source:autohub',source);

 const found=[];for(const url of links){await new Promise(r=>setTimeout(r,2000));const v=parseProduct(await fetchPage(url),url);if(!v)throw Error('Unrecognized listing');if(v.availability.endsWith('/InStock'))found.push(v);source.completed++;source.queued--;await write('source:autohub',source);await event('progress','Read '+(v.year+' '+v.model)+' · '+v.vin);}

 for(const v of found){const old=await read('vin:'+v.vin);const before=old?classify(old,prefs).category:null;const car={...v,firstSeen:old?.firstSeen||Date.now(),watched:old?.watched||false,evidence:old?.evidence||{},status:'ACTIVE',priceHistory:old?.priceHistory||[],sources:[{name:v.source,url:v.url,observed:v.observed}],misses:0};if(!old||old.price!==v.price){car.priceHistory.push({price:v.price,at:v.observed,url:v.url});await event(old?'price':'new',old?`${v.vin}: price ${old.price} → ${v.price}`:`New VIN ${v.vin}`);}await write('vin:'+v.vin,car);observations++;const after=classify(car,prefs).category;if(before!==after)await event('category',`${v.vin}: ${before||'NEW'} → ${after}`);}

 const all=await db().prepare("SELECT id,body FROM records WHERE id LIKE 'vin:%'").all<{id:string,body:string}>();for(const row of all.results){const v=JSON.parse(row.body);if(v.source==='Autohub Texas'&&!found.some(x=>x.vin===v.vin)){v.misses=(v.misses||0)+1;v.status=v.misses>=2?'REMOVED / AVAILABILITY UNKNOWN':'NOT SEEN';await write(row.id,v);}}

 source={...source,status:'HEALTHY',lastSuccess:Date.now(),nextScan:Date.now()+prefs.intervalMinutes*60000,observations:(previous.observations||0)+observations,scans:(previous.scans||0)+1,lastDurationMs:Date.now()-start,lastCount:observations,error:null};await event('scan',`Completed: ${observations} listing observations; ${requests} requests`);

 }catch(e){source={...source,status:'UNAVAILABLE',error:e instanceof Error?e.message:'Source failed',nextScan:Date.now()+prefs.intervalMinutes*60000};await event('error','Autohub Texas: '+source.error);}finally{await write('source:autohub',source);await db().prepare('DELETE FROM locks WHERE id=? AND token=?').bind('scan',token).run();await db().prepare('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 2000)').run();}return source;}

export async function state(){const prefs=await read('settings',defaults);const sources=await Promise.all([read('source:autohub',{name:'Autohub Texas',status:'WAITING'}),read('source:dallas',{name:'Autos of Dallas',status:'WAITING'})]);const rows=await db().prepare("SELECT body FROM records WHERE id LIKE 'vin:%'").all<{body:string}>();const vehicles=rows.results.map(r=>{const v=JSON.parse(r.body);return {...v,...classify(v,prefs)}});const events=await db().prepare('SELECT * FROM events ORDER BY id DESC LIMIT 100').all();const sum=(key:string)=>sources.reduce((n,s)=>n+(s[key]||0),0);return {settings:prefs,vehicles:vehicles.sort((a,b)=>b.preferenceScore-a.preferenceScore),sources,events:events.results,updated:Date.now(),tracker:{requests:sum('requests'),observations:sum('observations'),uniqueVins:vehicles.length,queue:sum('queued'),projectedObservationsPerHour:sources.some(s=>s.lastCount!=null)?Math.round(sum('lastCount')*60/prefs.intervalMinutes*10)/10:null,activeProcessingPerHour:sum('lastDurationMs')?Math.round(sum('lastCount')*3600000/sum('lastDurationMs')):null},coverage:'Two independent dealer collectors, including Autos of Dallas public API. Broader discovery and independent history are not yet connected. No paid services enabled.'};}

