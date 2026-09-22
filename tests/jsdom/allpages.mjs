import { JSDOM } from 'jsdom';
import fs from 'fs';
const pages = ['dashboard','playlists','settings','system','photos','widgets'];
const js = fs.readFileSync('/rool-drive/rndrsbc_engine/assets/app.js','utf8');
const CONFIG = { active_playlist:'main', language:'en', refresh_mode:'scheduled', transition:'cut',
  device:{name:'rndrSBC Frame', timezone:'America/Chicago', language:'en'},
  display:{driver:'auto', model:'epd7in3f', orientation:0, rotation:0, rotate:false, width:800, height:480, color_mode:'7color', saturation:0.5},
  playlists:{ main:{ name:'Main', items:[{widget:'weather', settings:{}, duration_minutes:30},{widget:'clock', settings:{}, duration_minutes:15},{widget:'photo_frame', settings:{}, duration_minutes:10}] } } };
for (const page of pages) {
  const html = fs.readFileSync(`/rool-drive/rndrsbc_engine/server/templates/${page}.html`,'utf8');
  const dom = new JSDOM(html, { runScripts:'outside-only', url:`http://localhost:8080/${page==='dashboard'?'':page}` });
  const { window } = dom;
  window.tailwind = { config: ()=>{} };
  window.L = { map: ()=>({ setView(){}, addTo(){}, tileLayer: ()=>({addTo(){}}), marker: ()=>({addTo(){}}) }) };
  window.fetch = async (u) => ({ ok:true, json: async () => u==='/api/config' ? CONFIG : (u==='/api/auth/status' ? {authenticated:true} : {}) });
  try { window.eval(js); } catch(e) { console.log(`${page}: EVAL ERR ${e.message}`); continue; }
  try { await window.eval('loadStatus()'); } catch(e) { console.log(`${page}: loadStatus ERR ${e.message}`); continue; }
  // probe key containers
  const ids = [...window.document.querySelectorAll('[id]')].map(e=>e.id);
  const empty = ids.filter(id => { const el = window.document.getElementById(id); return el && el.children.length===0 && !el.textContent.trim() && /container|list|tabs|grid|cards/.test(id); });
  console.log(`${page}: OK — empty dynamic containers: [${empty.join(', ')}]`);
}
