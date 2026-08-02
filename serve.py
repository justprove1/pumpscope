#!/usr/bin/env python3
"""Interfaz web local para pumpscope.

    python3 serve.py [--puerto 8787]

Solo stdlib. Escucha en 127.0.0.1: no se expone a la red.
"""

import argparse
import json
import socketserver
import sys
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

from ps import analyze, explain, live, resolve, scan, sources

PAGE = r"""<!doctype html>
<html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>pumpscope</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#0b0d10;color:#e6e9ef;font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:20px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:#7d8694;font-size:13px;margin-bottom:24px}
form{display:flex;gap:8px;margin-bottom:8px}
input{flex:1;background:#151920;border:1px solid #252c37;color:#e6e9ef;padding:12px 14px;border-radius:8px;font-size:14px;font-family:inherit}
input:focus{outline:none;border-color:#3d7dff}
button{background:#3d7dff;color:#fff;border:0;padding:12px 20px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit}
button:disabled{opacity:.5;cursor:default}
.opts{display:flex;gap:16px;align-items:center;color:#7d8694;font-size:13px;margin-bottom:24px}
.opts #h{width:64px;flex:none;padding:5px 8px;font-size:13px;text-align:center}
.card{background:#111419;border:1px solid #1e242e;border-radius:12px;padding:20px;margin-bottom:16px}
.tok{font-size:18px;font-weight:600}
.mint{color:#5c6572;font-size:12px;font-family:ui-monospace,monospace;word-break:break-all;margin-top:2px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:14px;margin-top:16px}
.k{color:#7d8694;font-size:11px;text-transform:uppercase;letter-spacing:.05em}
.v{font-size:16px;font-weight:600;margin-top:2px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:#7d8694;margin:0 0 14px;font-weight:600}
.sc{margin-bottom:16px}
.sc:last-child{margin-bottom:0}
.scH{display:flex;align-items:baseline;gap:10px;margin-bottom:6px}
.scN{font-weight:700;font-size:14px;width:74px}
.scP{font-weight:700;font-size:15px;margin-left:auto}
.bar{height:7px;background:#1a1f27;border-radius:4px;overflow:hidden;margin-bottom:6px}
.bar div{height:100%;border-radius:4px}
.scD{color:#98a1b0;font-size:13px}
.mc{color:#7d8694;font-size:12.5px;margin-top:2px}
.mot{font-size:12.5px;margin-top:3px;padding-left:11px;position:relative}
.mot:before{content:'·';position:absolute;left:2px}
.xbtn{background:none;border:0;color:#5c8cff;font-size:12px;cursor:pointer;padding:4px 0 0;font-family:inherit;text-decoration:underline}
.xbox{background:#0d1015;border:1px solid #1e242e;border-radius:8px;padding:14px 16px;margin-top:8px}
.xbox p{margin:0 0 11px;font-size:13.5px;line-height:1.62;color:#c3cad6}
.xbox p:last-child{margin:0;color:#7d8694;font-size:12.5px}
.trend{margin-top:14px;padding:11px 13px;border-radius:9px;border-left:3px solid}
.trend b{font-size:13px;letter-spacing:.04em}
.trendW{font-size:12px;color:#7d8694;margin-top:3px;line-height:1.5}
.t-g{background:rgba(62,207,142,.07);border-color:#3ecf8e;color:#3ecf8e}
.t-y{background:rgba(232,179,57,.07);border-color:#e8b339;color:#e8b339}
.t-red{background:rgba(240,97,109,.07);border-color:#f0616d;color:#f0616d}
.win{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin-top:12px}
.win div{background:#0d1015;border:1px solid #1e242e;border-radius:7px;padding:7px 8px;text-align:center}
.win .wl{font-size:10px;color:#5c6572;text-transform:uppercase;letter-spacing:.05em}
.win .wv{font-size:12.5px;font-weight:700;font-family:ui-monospace,monospace;margin-top:2px}
.win .wc{font-size:10.5px;margin-top:1px;font-family:ui-monospace,monospace}
.stH{font-size:14px;font-weight:700;letter-spacing:.04em;margin-bottom:12px}
.stR{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid #161b22;font-size:13px}
.stR:last-of-type{border:0}
.stR b{font-family:ui-monospace,monospace}
.stF{font-size:12.5px;padding:3px 0}
.stNote{background:#1c1215;border:1px solid #3a1f24;border-radius:8px;padding:10px 12px;margin-top:10px;font-size:12.5px;color:#f0616d}
.wh{margin-top:10px}
.whr{display:grid;grid-template-columns:1.2fr 1fr 1fr 1.6fr;gap:8px;align-items:center;padding:8px 0;border-bottom:1px solid #161b22;font-size:12.5px}
.whr:last-child{border:0}
.whr .nm{font-weight:600}
.whr .um,.whr .pp{font-family:ui-monospace,monospace}
.whr .fi{font-size:11px;color:#5c6572;text-align:right}
.whH{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:#5c6572;border-bottom:1px solid #1e242e;padding-bottom:5px}
.whT{background:#0d1015;border:1px solid #1e242e;border-radius:8px;padding:11px 13px;margin-top:12px;font-size:13px}
.mc3{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:14px}
.mc3 div{background:#0d1015;border:1px solid #1e242e;border-radius:9px;padding:11px 12px}
.mc3 .t{font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;color:#7d8694;margin-bottom:3px}
.mc3 .v2{font-size:16px;font-weight:700;font-family:ui-monospace,monospace;line-height:1.25}
.mc3 .d{font-size:12px;font-family:ui-monospace,monospace;margin-top:2px;transition:color .35s}
.mc3 .p2{font-size:10.5px;color:#5c6572;margin-top:3px}
@media(max-width:560px){.mc3{grid-template-columns:1fr}}
.up{color:#3ecf8e}.rg{color:#e8b339}.dn{color:#f0616d}
.bup{background:#3ecf8e}.brg{background:#e8b339}.bdn{background:#f0616d}
.prog{height:9px;background:#1a1f27;border-radius:5px;overflow:hidden;margin:8px 0}
.prog div{height:100%;background:linear-gradient(90deg,#3d7dff,#3ecf8e)}
table{width:100%;border-collapse:collapse;font-size:12px;font-family:ui-monospace,monospace}
th{text-align:right;color:#5c6572;font-weight:500;padding:4px 6px;border-bottom:1px solid #1e242e}
th:first-child,td:first-child{text-align:left}
td{padding:4px 6px;border-bottom:1px solid #161b22}
.lv{display:flex;justify-content:space-between;padding:5px 0;font-family:ui-monospace,monospace;font-size:13px;border-bottom:1px solid #161b22}
.now{background:#151b26;margin:4px -8px;padding:6px 8px;border-radius:6px;font-weight:700;border:0}
.warn{color:#e8b339;font-size:13px;padding:4px 0}
.err{color:#f0616d;background:#1c1215;border:1px solid #3a1f24;padding:14px;border-radius:8px;font-size:14px}
.foot{color:#5c6572;font-size:12px;line-height:1.6;margin-top:28px;border-top:1px solid #1e242e;padding-top:16px}
.spin{color:#7d8694;font-size:14px;padding:20px 0;text-align:center}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#3ecf8e;margin-right:6px;vertical-align:middle;animation:pulse 1.8s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
.dot.off{background:#5c6572;animation:none}
.livev{transition:color .35s}
.flashU{color:#3ecf8e!important}.flashD{color:#f0616d!important}
.liveH{display:flex;align-items:center;gap:6px;font-size:11px;color:#7d8694;text-transform:uppercase;letter-spacing:.05em}
.bb{display:flex;height:20px;border-radius:5px;overflow:hidden;margin:5px 0 3px}
.bb .b{background:linear-gradient(90deg,#2fa876,#3ecf8e)}
.bb .s{background:linear-gradient(90deg,#f0616d,#c94651)}
.bbL{display:flex;justify-content:space-between;font-size:12px;color:#98a1b0;font-family:ui-monospace,monospace}
.bbR{font-size:11px;color:#7d8694;margin-bottom:12px}
.tabs{display:flex;gap:6px;margin-bottom:18px}
.tab{background:#151920;border:1px solid #252c37;color:#98a1b0;padding:8px 16px;border-radius:8px;font-size:13px;cursor:pointer;font-family:inherit}
.tab.on{background:#3d7dff;border-color:#3d7dff;color:#fff;font-weight:600}
.row{border-bottom:1px solid #1e242e;padding:14px 0}
.row:last-child{border:0}
.rowH{display:flex;align-items:baseline;gap:10px}
.rank{color:#5c6572;font-family:ui-monospace,monospace;font-size:13px;width:22px}
.rnm{font-weight:600;font-size:15px}
.sc2{margin-left:auto;font-weight:700;font-family:ui-monospace,monospace}
.rmint{color:#5c6572;font-size:11px;font-family:ui-monospace,monospace;word-break:break-all;margin:3px 0 6px 32px}
.rz{font-size:12.5px;margin-left:32px}
.rz.p{color:#3ecf8e}.rz.n{color:#f0616d}
.rbtn{background:#1a1f27;border:1px solid #252c37;color:#98a1b0;font-size:11px;padding:3px 10px;border-radius:5px;cursor:pointer;margin-left:32px;margin-top:6px;font-family:inherit}
</style></head><body><div class="wrap">
<h1>pumpscope</h1>
<div class="sub">3 escenarios con probabilidad, sobre datos reales de la curva</div>
<div class="tabs">
  <button class="tab on" id="tabA">Analizar token</button>
  <button class="tab" id="tabB">Buscar tendencias</button>
</div>
<form id="f">
  <input id="q" placeholder="https://pump.fun/coin/... o el mint" autocomplete="off" autofocus>
  <button id="go">Analizar</button>
</form>
<div class="opts">
  <label>horizonte <input type="text" inputmode="decimal" id="h" value="6"> h</label>
  <label><input type="checkbox" id="w" checked style="flex:none"> mostrar el porqué</label>
</div>
<div id="out"></div>
<div id="outB" style="display:none"></div>
<div class="foot">
Modelo estadístico sobre un mercado donde el 68,67% de los tokens muere el mismo día
y el 0,26% gradúa. Las probabilidades son condicionales y están mal calibradas en las
colas por definición. No es consejo financiero.
</div></div>
<script>
const $=s=>document.querySelector(s), out=$('#out');
let LAST_MINT='', MC3={}, WH=null;
// El input acepta "0,5" y "0.5": se normaliza aqui en vez de confiar en el
// locale del navegador, que con type=number devolvia cadena vacia.
function horizonte(){
 const raw=String($('#h').value||'').trim().replace(',','.');
 const v=parseFloat(raw);
 if(!isFinite(v)||v<=0)return 6;
 return Math.min(168,Math.max(0.25,v));
}
const money=x=>{if(x===null||x===undefined)return'n/d';const a=Math.abs(x);
 if(a===0)return'$0';
 if(a>=1e6)return'$'+(x/1e6).toFixed(2)+'M';if(a>=1e3)return'$'+(x/1e3).toFixed(1)+'k';
 if(a>=1)return'$'+x.toFixed(2);if(a>=1e-6)return'$'+x.toFixed(8);return'$'+x.toFixed(10)};
const pc=x=>(x>=0?'+':'')+x.toFixed(1)+'%';
// Capitalizacion con todos los digitos: '$7.23M' esconde 10.000 dolares de
// diferencia, que es justo lo que hay que ver al compararla con un objetivo.
const mcFull=x=>{if(x===null||x===undefined)return'n/d';const a=Math.abs(x);
 if(a===0)return'$0';
 if(a>=1000)return'$'+x.toLocaleString('es-ES',{maximumFractionDigits:0});
 if(a>=1)return'$'+x.toLocaleString('es-ES',{minimumFractionDigits:2,maximumFractionDigits:2});
 return money(x)};
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const CL={SUBIDA:['up','bup'],RANGO:['rg','brg'],BAJADA:['dn','bdn']};

$('#f').onsubmit=async e=>{
 e.preventDefault();
 const q=$('#q').value.trim(); if(!q)return;
 $('#go').disabled=true;
 out.innerHTML='<div class="card"><div class="spin">analizando… (las APIs gratuitas van despacio, ~10-20s)</div></div>';
 try{
  const r=await fetch('/api?q='+encodeURIComponent(q)+'&h='+horizonte());
  const d=await r.json();
  if(d.error){out.innerHTML='<div class="err">'+esc(d.error)+'</div>';}
  else render(d);
 }catch(err){
   out.innerHTML='<div class="err"><b>No se pudo contactar con el servidor.</b><br>'+
     '<span style="font-size:13px;color:#98a1b0">'+esc(err.message)+
     ' — comprueba que <code>serve.py</code> sigue corriendo en el puerto 8787.</span></div>';
 }
 $('#go').disabled=false;
};

function render(d){
 const c=d.curva; let h='';
 h+='<div class="card"><div class="tok">'+esc(d.nombre||'?')+' ('+esc(d.simbolo||'?')+')</div>'+
    '<div class="mint">'+esc(d.mint)+'</div><div class="grid">'+
    '<div><div class="liveH"><span class="dot" id="dot"></span>Precio</div>'+
    '<div class="v livev" id="lvPx">'+money(d.precio_usd)+'</div></div>'+
    '<div><div class="liveH"><span class="dot" id="dot2"></span>Market cap</div>'+
    '<div class="v livev" id="lvMc" style="font-size:15px">'+mcFull(d.mcap_usd)+'</div></div>'+
    kv('Liquidez',money(d.liquidez_usd))+kv('Vol 24h',money(d.vol24_usd))+
    kv('Edad',esc(d.edad))+'</div>'+
    '<div class="mc" id="lvDelta" style="margin-top:10px"></div>'+
    (d.tendencia?('<div class="trend t-'+esc(d.tendencia.color)+'">'+
      '<b>'+esc(d.tendencia.label)+'</b>'+
      '<div class="trendW">'+esc(d.tendencia.why)+'</div></div>'):'')+
    '</div>';

 h+='<div class="card"><h2>Estado de la curva</h2>';
 if(c.graduado){h+='<div class="up" style="font-weight:600">GRADUADO — cotiza en AMM</div>';}
 else{
  const p=(c.progreso||0);
  h+='<div style="display:flex;justify-content:space-between;font-size:13px"><span>Progreso</span><b id="lvProgT">'+(p*100).toFixed(1)+'%</b></div>';
  h+='<div class="prog"><div id="lvProgB" style="width:'+(p*100)+'%"></div></div>';
  if(c.mcap_graduacion_usd)h+='<div class="scD">Gradúa en '+mcFull(c.mcap_graduacion_usd)+' de mcap ('+(c.mcap_graduacion_sol||0).toFixed(1)+' SOL — umbral fijado en SOL, no en USD)</div>';
  if(c.x_para_graduar&&c.x_para_graduar>1)h+='<div class="scD">Precio de graduación '+money(c.precio_graduacion_usd)+' → <b>'+c.x_para_graduar.toFixed(1)+'x</b> desde aquí · faltan '+(c.sol_faltantes||0).toFixed(1)+' SOL</div>';
 }
 h+='</div>';

 h+='<div class="card"><h2>3 escenarios · horizonte '+esc(d.horizonte_label)+
    (d.horizonte_recortado?' (recortado de '+d.horizonte_pedido_h+'h)':'')+'</h2>';
 d.escenarios.slice().sort((a,b)=>b.prob-a.prob).forEach(s=>{
  const [tc,bc]=CL[s.name];
  h+='<div class="sc"><div class="scH"><span class="scN '+tc+'">'+s.name+'</span>'+
     '<span class="scD">'+esc(s.label)+'</span>'+
     '<span class="scP '+tc+'">'+(s.prob*100).toFixed(1)+'%</span></div>'+
     '<div class="bar"><div class="'+bc+'" style="width:'+(s.prob*100)+'%"></div></div>';
  if(s.name==='RANGO'){
   h+=s.lo?'<div class="scD">zona '+money(s.lo)+' — '+money(s.hi)+' ('+pc(s.lo_pct)+' / '+pc(s.hi_pct)+')</div>'
          :'<div class="scD">sin volatilidad medible para acotar la zona</div>';
   if(s.mcap_lo)h+='<div class="mc">mcap '+mcFull(s.mcap_lo)+' — '+mcFull(s.mcap_hi)+'</div>';
  }else{
   h+=s.target?'<div class="scD">objetivo <b>'+money(s.target)+'</b> ('+pc(s.pct)+') · origen: '+esc(s.source)+'</div>'
              :'<div class="scD">sin objetivo: faltan datos de precio</div>';
   if(s.mcap)h+='<div class="mc">'+(s.name==='SUBIDA'?'techo':'suelo')+' en capitalización <b class="'+tc+'">'+money(s.mcap)+'</b>'+
     (s.mcap_vs_grad?' · '+esc(s.mcap_vs_grad):'')+'</div>';
  }
  (s.motivos||[]).forEach(m=>h+='<div class="mot '+tc+'">'+esc(m)+'</div>');
  h+='<button class="xbtn" onclick="explicar(this,\''+s.name+'\')">¿por qué '+
     (s.name==='SUBIDA'?'la subida':(s.name==='BAJADA'?'la bajada':'el rango'))+'? →</button>'+
     '<div class="xdst"></div>';
  h+='</div>';
 });
 h+='<div class="scD" style="margin-top:14px;color:#5c6572">Movimiento esperado 1σ en '+
    esc(d.horizonte_label)+': ±'+d.movimiento_1sigma_pct.toFixed(1)+'% · escalado '+esc(d.escalado)+'</div>'+
    '<div class="scD" style="color:#5c6572;font-size:12px">volatilidad por '+esc(d.sigma_source||'n/d')+
    ' · objetivos por '+esc(d.dist_source||'n/d')+'</div>';

 // Los 3 objetivos en capitalizacion. Las cifras son niveles fijos del
 // analisis; lo que se actualiza en vivo es la distancia hasta cada una.
 const up=d.escenarios.find(x=>x.name==='SUBIDA')||{},
       rg=d.escenarios.find(x=>x.name==='RANGO')||{},
       dn=d.escenarios.find(x=>x.name==='BAJADA')||{};
 MC3={up:up.mcap||null, lo:rg.mcap_lo||null, hi:rg.mcap_hi||null, dn:dn.mcap||null};
 h+='<div class="mc3">'+
    '<div><div class="t">Techo · '+(up.prob*100||0).toFixed(0)+'%</div>'+
      '<div class="v2 up">'+mcFull(MC3.up)+'</div><div class="d" id="dUp">—</div>'+
      '<div class="p2">si rompe al alza</div></div>'+
    '<div><div class="t">Rango · '+(rg.prob*100||0).toFixed(0)+'%</div>'+
      '<div class="v2 rg" style="font-size:12.5px;line-height:1.45">'+mcFull(MC3.lo)+'<br>'+mcFull(MC3.hi)+'</div>'+
      '<div class="d" id="dRg">—</div><div class="p2">zona de consolidación</div></div>'+
    '<div><div class="t">Suelo · '+(dn.prob*100||0).toFixed(0)+'%</div>'+
      '<div class="v2 dn">'+mcFull(MC3.dn)+'</div><div class="d" id="dDn">—</div>'+
      '<div class="p2">si pierde soporte</div></div></div></div>';

 if(d.niveles&&d.niveles.length){
  h+='<div class="card"><h2>Niveles detectados</h2>';
  d.niveles.forEach(l=>{
   const mc=l.mcap?'<span style="color:#5c6572;margin-left:auto;padding-right:14px">'+mcFull(l.mcap)+'</span>':'';
   if(l.tipo==='ahora')h+='<div class="lv now"><span>→ precio actual</span>'+mc+'<span>'+money(l.precio)+'</span></div>';
   else h+='<div class="lv"><span class="'+(l.lado==='R'?'dn':'up')+'">'+l.lado+' '+esc(l.tipo)+'</span>'+mc+'<span>'+money(l.precio)+'</span></div>';
  });
  h+='</div>';
 }

 if(d.setup){
  const t=d.setup, cc=t.color==='g'?'up':(t.color==='red'?'dn':'rg');
  h+='<div class="card"><h2>Condiciones de entrada</h2>'+
     '<div class="stH '+cc+'">'+esc(t.condiciones)+'</div>';
  if(t.ve_pct!==null&&t.ve_pct!==undefined){
   h+='<div class="stR"><span>Valor esperado del movimiento</span><b>'+pc(t.ve_pct)+'</b></div>'+
      '<div class="stR"><span>Coste de entrar y salir <span style="color:#5c6572">('+money(t.tamano_ref)+')</span></span><b class="dn">-'+t.coste_pct.toFixed(2)+'%</b></div>'+
      '<div class="stR"><span><b>Valor esperado NETO</b></span><b class="'+(t.ve_neto_pct>0?'up':'dn')+'">'+pc(t.ve_neto_pct)+'</b></div>';
   if(t.ve_neto_pct<0)h+='<div class="stNote">Con estos números la operación pierde de media, aunque acierte a veces.</div>';
  }
  if(t.rr!==null&&t.rr!==undefined)h+='<div class="stR"><span>Riesgo / recompensa</span><b>'+t.rr.toFixed(2)+' a 1</b></div>';
  if(t.descartes.length){h+='<div style="margin-top:12px"><b class="dn" style="font-size:12px">DESCARTES</b>';
   t.descartes.forEach(x=>h+='<div class="stF dn">✕ '+esc(x)+'</div>');h+='</div>';}
  if(t.rojas.length){h+='<div style="margin-top:8px">';t.rojas.forEach(x=>h+='<div class="stF dn">▼ '+esc(x)+'</div>');h+='</div>';}
  if(t.verdes.length){h+='<div style="margin-top:8px">';t.verdes.forEach(x=>h+='<div class="stF up">▲ '+esc(x)+'</div>');h+='</div>';}
  h+='<div class="scD" style="color:#5c6572;font-size:12px;margin-top:12px">Esto describe lo medido, no es una recomendación de compra. La decisión de operar es tuya.</div></div>';
 }

 if(d.ballenas&&d.ballenas.niveles&&d.ballenas.niveles.length){
  h+='<div class="card"><h2>Entrada de ballena · horizonte '+esc(d.horizonte_label)+'</h2>'+
     '<div class="scD" style="font-size:12.5px;color:#7d8694">Umbral por impacto en el precio, no por dólares: '+
     'lo que es una ballena en un pool de $20k es ruido en uno de $2M.</div>'+
     '<div class="wh" id="whWrap"></div><div id="whT"></div></div>';
  WH=d.ballenas;
 }

 const f=d.flujo;
 h+='<div class="card"><h2>Flujo de órdenes ('+f.trades+' trades)</h2>';
 if(f.trades){
  const bb=(lbl,share,l,r2)=>{
   const p=(share*100);
   return '<div class="bbL"><span>'+lbl+'</span><span>'+p.toFixed(0)+'% / '+(100-p).toFixed(0)+'%</span></div>'+
    '<div class="bb"><div class="b" style="width:'+p+'%"></div><div class="s" style="width:'+(100-p)+'%"></div></div>'+
    '<div class="bbR">'+l+' &nbsp;·&nbsp; '+r2+'</div>';
  };
  const txs=f.buys/Math.max(1,f.buys+f.sells);
  h+='<div style="display:flex;justify-content:space-between;font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">'+
     '<b class="up">Compradores</b><b class="dn">Vendedores</b></div>';
  h+='<div id="bbWrap">'+
     bb('operaciones',txs,f.buys+' compras',f.sells+' ventas')+
     bb('wallets',f.buyer_share,f.buyers+' compradores',f.sellers+' vendedores')+
     bb('dinero',f.buy_usd_share,money(f.buy_usd),money(f.sell_usd))+'</div>';
  h+='<div class="scD" id="bbTicket">ticket medio · compra <b>'+money(f.avg_buy)+'</b> · venta <b>'+money(f.avg_sell)+'</b></div>';
  h+='<div id="bbWin" class="win"></div>';
  const div=txs-f.buyer_share;
  if(Math.abs(div)>0.12)h+='<div class="warn">⚠ '+(div>0
    ?'las compras se concentran en pocas wallets (compra repetida, no demanda nueva)'
    :'pocas wallets acumulan con tickets grandes mientras muchas venden pequeño')+'</div>';
  if(f.both)h+='<div class="scD" style="color:#5c6572">'+f.both+' wallets compran y venden en la misma ventana</div>';
  h+='<div class="scD" style="margin-top:6px">'+f.wallets+' wallets únicas · HHI '+f.hhi.toFixed(3)+'</div>';
  if(f.dev_vendio_usd>0)h+='<div class="scD dn" style="font-weight:700;margin-top:6px">⚠ EL DEV ESTÁ VENDIENDO: '+money(f.dev_vendio_usd)+'</div>';
 }else h+='<div class="scD">sin trades recuperados</div>';
 h+='</div>';

 if(d.porque){
  h+='<div class="card"><h2>Por qué esas cifras</h2>'+
     '<div class="scD" style="margin-bottom:10px">Régimen <b>'+esc(d.regimen)+'</b> · base rate '+
     d.porque.base.map(b=>b[0]+' '+(b[1]*100).toFixed(0)+'%').join(' / ')+
     ' · confianza '+(d.confianza*100).toFixed(0)+'%</div>';
  h+='<table><tr><th>señal</th><th>grupo</th><th>z</th><th>→SUB</th><th>→RAN</th><th>→BAJ</th></tr>';
  d.porque.filas.forEach(r=>{h+='<tr><td>'+esc(r.signal)+'</td><td style="color:#5c6572">'+esc(r.group)+
    '</td><td>'+r.z.toFixed(2)+'</td><td>'+r.up.toFixed(3)+'</td><td>'+r.rg.toFixed(3)+'</td><td>'+r.dn.toFixed(3)+'</td></tr>';});
  h+='</table>';
  if(d.porque.topados.length)h+='<div class="warn" style="margin-top:8px">Grupos limitados por correlación: '+esc(d.porque.topados.join(', '))+'</div>';
  h+='</div>';
 }

 if(d.avisos&&d.avisos.length){
  h+='<div class="card"><h2>Avisos de calidad de datos</h2>';
  d.avisos.forEach(w=>h+='<div class="warn">· '+esc(w)+'</div>');
  h+='</div>';
 }
 LAST_MINT=d.mint;
 out.innerHTML=h;
 if(WH)pintaBallenas(WH);
 startLive(d);
}

let ES=null;
function startLive(d){
 if(ES){ES.close();ES=null;}
 const base={px:d.precio_usd, mc:d.mcap_usd, t:Date.now()};
 let prev=base.px;
 ES=new EventSource('/stream?q='+encodeURIComponent(d.mint)+'&h='+horizonte());

 ES.onmessage=e=>{
  let v; try{v=JSON.parse(e.data);}catch(_){return;}
  const dot=$('#dot'), dot2=$('#dot2');
  if(v.error){ if(dot)dot.className='dot off'; if(dot2)dot2.className='dot off'; return; }
  if(dot)dot.className='dot'; if(dot2)dot2.className='dot';

  const px=$('#lvPx'), mc=$('#lvMc');
  if(px&&v.price){
   px.textContent=money(v.price);
   if(prev&&v.price!==prev){
    const cls=v.price>prev?'flashU':'flashD';
    px.classList.add(cls); setTimeout(()=>px.classList.remove(cls),450);
   }
   prev=v.price;
  }
  if(mc&&v.mcap)mc.textContent=mcFull(v.mcap);

  // Distancia viva desde la capitalizacion actual hasta cada objetivo.
  if(v.mcap){
   const set=(id,target,label)=>{
    const el=$(id); if(!el||!target)return;
    const ch=(target/v.mcap-1)*100;
    el.textContent=(ch>=0?'+':'')+ch.toFixed(1)+'% '+(label||'');
    el.style.color=ch>0?'#3ecf8e':(ch<0?'#f0616d':'#7d8694');
   };
   set('#dUp',MC3.up,'desde aquí');
   set('#dDn',MC3.dn,'desde aquí');
   const rg=$('#dRg');
   if(rg&&MC3.lo&&MC3.hi){
    if(v.mcap>=MC3.lo&&v.mcap<=MC3.hi){
     rg.textContent='dentro de la zona'; rg.style.color='#e8b339';
    }else if(v.mcap<MC3.lo){
     rg.textContent='+'+((MC3.lo/v.mcap-1)*100).toFixed(1)+'% para entrar';
     rg.style.color='#7d8694';
    }else{
     rg.textContent=((MC3.hi/v.mcap-1)*100).toFixed(1)+'% para volver';
     rg.style.color='#7d8694';
    }
   }
  }

  // Cuanto se ha movido desde que se calcularon los escenarios.
  const dl=$('#lvDelta');
  if(dl&&v.price&&base.px){
   const ch=(v.price/base.px-1)*100, secs=Math.round((Date.now()-base.t)/1000);
   const c=ch>0.05?'up':(ch<-0.05?'dn':'');
   dl.innerHTML='desde el análisis (hace '+(secs<60?secs+'s':Math.round(secs/60)+' min')+'): '+
     '<b class="'+c+'">'+pc(ch)+'</b>'+
     (Math.abs(ch)>15?' · <span class="rg">movimiento grande, conviene recalcular</span>':'');
  }

  // Barrera de flujo: se repinta cuando llega el carril lento (~24s).
  if(v.flujo)pintaBarrera(v.flujo);
  if(v.ballenas){WH=v.ballenas;pintaBallenas(v.ballenas);}
  // Ventanas de compras/ventas: carril medio (~8s).
  if(v.ventanas)pintaVentanas(v.ventanas);

  // La curva tambien avanza en directo.
  const pb=$('#lvProgB'), pt=$('#lvProgT');
  if(pb&&v.progress!==null&&v.progress!==undefined){
   pb.style.width=(v.progress*100)+'%';
   if(pt)pt.textContent=(v.progress*100).toFixed(1)+'%';
  }
 };
 ES.onerror=()=>{const d1=$('#dot'),d2=$('#dot2');if(d1)d1.className='dot off';if(d2)d2.className='dot off';};
}


function setTab(b){
 $('#tabA').classList.toggle('on',!b); $('#tabB').classList.toggle('on',b);
 $('#f').style.display=b?'none':'flex';
 document.querySelector('.opts').style.display=b?'none':'flex';
 $('#out').style.display=b?'none':'block';
 $('#outB').style.display=b?'block':'none';
 if(b&&!$('#outB').dataset.loaded)buscar();
}
$('#tabA').onclick=()=>setTab(false);
$('#tabB').onclick=()=>{setTab(true);};

async function buscar(){
 const o=$('#outB');
 o.innerHTML='<div class="card"><div class="spin">rastreando tendencias…</div></div>';
 try{
  const r=await fetch('/buscar?limite=10');
  const d=await r.json();
  if(d.error){o.innerHTML='<div class="err">'+esc(d.error)+'</div>';return;}
  let h='<div class="card"><h2>Memecoins en posible tendencia alcista</h2>';
  if(!d.filas.length)h+='<div class="scD">Ningún candidato supera los mínimos ahora mismo.</div>';
  d.filas.forEach((r2,i)=>{
   const c=r2.score>=3.5?'up':(r2.score>=2?'rg':'');
   h+='<div class="row"><div class="rowH"><span class="rank">'+(i+1)+'.</span>'+
      '<span class="rnm">'+esc(r2.name)+'</span>'+
      '<span class="scD">h1 '+pc(r2.h1)+' · liq '+money(r2.liq)+'</span>'+
      '<span class="sc2 '+c+'">'+(r2.score>=0?'+':'')+r2.score.toFixed(2)+'</span></div>'+
      '<div class="rmint">'+esc(r2.mint)+'</div>';
   r2.reasons.forEach(x=>h+='<div class="rz p">+ '+esc(x)+'</div>');
   r2.penalties.forEach(x=>h+='<div class="rz n">− '+esc(x)+'</div>');
   h+='<button class="rbtn" onclick="analizar(\''+r2.mint+'\')">analizar este</button></div>';
  });
  h+='</div><div class="card"><div class="scD" style="color:#5c6572">El score <b>no</b> es una probabilidad: '+
     'ordena candidatos para mirarlos de cerca. Analiza el que te interese antes de hacer nada.</div>'+
     '<button class="rbtn" style="margin-left:0" onclick="buscar()">volver a rastrear</button></div>';
  o.innerHTML=h; o.dataset.loaded='1';
 }catch(e){o.innerHTML='<div class="err">'+esc(e.message)+'</div>';}
}
function analizar(m){ setTab(false); $('#q').value=m; $('#f').dispatchEvent(new Event('submit')); }


async function explicar(btn,clase){
 const box=btn.nextElementSibling;
 if(box.dataset.open==='1'){box.innerHTML='';box.dataset.open='0';btn.textContent=btn.textContent.replace('↓','→');return;}
 box.innerHTML='<div class="xbox"><p style="color:#7d8694">redactando…</p></div>';
 box.dataset.open='1';
 try{
  const r=await fetch('/explicar?q='+encodeURIComponent(LAST_MINT)+'&c='+clase+
                      '&h='+horizonte());
  const d=await r.json();
  if(d.error){box.innerHTML='<div class="err">'+esc(d.error)+'</div>';return;}
  box.innerHTML='<div class="xbox">'+d.parrafos.map(p=>'<p>'+esc(p)+'</p>').join('')+'</div>';
  btn.textContent=btn.textContent.replace('→','↓');
 }catch(e){box.innerHTML='<div class="err">'+esc(e.message)+'</div>';}
}


function pintaBallenas(w){
 const el=$('#whWrap'); if(!el||!w||!w.niveles)return;
 let h='<div class="whr whH"><span>nivel</span><span>umbral</span><span>P entra</span><span>fiabilidad</span></div>';
 w.niveles.forEach(n=>{
  const p=n.p_compra*100, c=p>=60?'up':(p>=25?'rg':'');
  let fi;
  if(n.muy_extrapolado)fi='<span class="dn">extrapolado '+(n.sobre_max||0).toFixed(0)+'x</span>';
  else if(n.extrapolado)fi='<span class="rg">ajuste de cola '+(n.sobre_max||0).toFixed(1)+'x</span>';
  else fi='<span class="up">'+n.observadas+' observadas</span>';
  h+='<div class="whr"><span class="nm">'+esc(n.nombre)+'</span>'+
     '<span class="um">'+money(n.umbral)+'</span>'+
     '<span class="pp '+c+'"><b>'+p.toFixed(0)+'%</b> <span style="color:#5c6572;font-size:11px">/ '+
     ((n.p_venta||0)*100).toFixed(0)+'% sale</span></span>'+
     '<span class="fi">'+fi+'</span></div>';
 });
 el.innerHTML=h;
 const t=$('#whT');
 if(t&&w.titular){
  let x='<div class="whT">Lo más informativo: <b>'+esc(w.titular.nombre)+'</b> de <b>'+
        money(w.titular.umbral)+'</b> → <b class="'+(w.titular.p_compra>=0.6?'up':'rg')+'">'+
        (w.titular.p_compra*100).toFixed(0)+'%</b>, una cada '+(w.titular.espera_min||0).toFixed(0)+' min.';
  if(w.max_observado)x+='<div style="color:#5c6572;font-size:11.5px;margin-top:5px">mayor compra observada '+
     money(w.max_observado)+' · cola α='+(w.alfa||0).toFixed(2)+'</div>';
  (w.motivos||[]).forEach(m=>x+='<div style="color:#5c8cff;font-size:12px;margin-top:3px">· '+esc(m)+'</div>');
  if(!w.fiable)x+='<div class="warn" style="font-size:12px">⚠ ventana corta: la tasa está poco determinada</div>';
  t.innerHTML=x+'</div>';
 }
}

function barraHTML(lbl,share,l,r2){
 const p=share*100;
 return '<div class="bbL"><span>'+lbl+'</span><span>'+p.toFixed(0)+'% / '+(100-p).toFixed(0)+'%</span></div>'+
  '<div class="bb"><div class="b" style="width:'+p+'%;transition:width .5s"></div>'+
  '<div class="s" style="width:'+(100-p)+'%;transition:width .5s"></div></div>'+
  '<div class="bbR">'+l+' &nbsp;·&nbsp; '+r2+'</div>';
}

function pintaBarrera(f){
 const w=$('#bbWrap'); if(!w)return;
 const txs=f.buys/Math.max(1,f.buys+f.sells);
 w.innerHTML=barraHTML('operaciones',txs,f.buys+' compras',f.sells+' ventas')+
   barraHTML('wallets',f.buyer_share,f.buyers+' compradores',f.sellers+' vendedores')+
   barraHTML('dinero',f.buy_usd_share,money(f.buy_usd),money(f.sell_usd));
 const t=$('#bbTicket');
 if(t){
  let x='ticket medio · compra <b>'+money(f.avg_buy)+'</b> · venta <b>'+money(f.avg_sell)+'</b>'+
        ' · '+f.wallets+' wallets · HHI '+f.hhi.toFixed(3);
  const div=txs-f.buyer_share;
  if(Math.abs(div)>0.12)x+='<div class="warn">⚠ '+(div>0
    ?'las compras se concentran en pocas wallets (compra repetida, no demanda nueva)'
    :'pocas wallets acumulan con tickets grandes mientras muchas venden pequeño')+'</div>';
  if(f.dev_sold_usd>0)x+='<div class="warn" style="color:#f0616d;font-weight:700">⚠ EL DEV ESTÁ VENDIENDO: '+money(f.dev_sold_usd)+'</div>';
  t.innerHTML=x;
 }
}

function pintaVentanas(w){
 const el=$('#bbWin'); if(!el)return;
 const ord=['m5','h1','h6','h24'], nom={m5:'5 min',h1:'1 h',h6:'6 h',h24:'24 h'};
 el.innerHTML=ord.map(k=>{
  const d=w[k]; if(!d)return '';
  const sh=d.share, col=sh===null?'':(sh>0.55?'up':(sh<0.45?'dn':'rg'));
  const chg=(d.chg===null||d.chg===undefined)?'':pc(d.chg);
  return '<div><div class="wl">'+nom[k]+'</div>'+
   '<div class="wv '+col+'">'+(sh===null?'—':(sh*100).toFixed(0)+'% compra')+'</div>'+
   '<div class="wc" style="color:#5c6572">'+d.buys+'/'+d.sells+
   (chg?' · <span class="'+(d.chg>0?'up':'dn')+'">'+chg+'</span>':'')+'</div></div>';
 }).join('');
}

function kv(k,v){return '<div><div class="k">'+k+'</div><div class="v">'+v+'</div></div>';}
</script></body></html>
"""


def _fmt_age(h):
    if h is None:
        return "n/d"
    if h < 2:
        return "%.0f min" % (h * 60)
    if h < 48:
        return "%.1f h" % h
    return "%.1f dias" % (h / 24.0)


def payload(a, why=True):
    coin, curve, stats, pred = a["coin"], a["curve"], a["stats"], a["pred"]
    try:
        mcap = float(coin.get("usd_market_cap") or 0) or None
    except (TypeError, ValueError):
        mcap = None

    niveles = []
    for lv in a["resistances"][:4][::-1]:
        niveles.append({"lado": "R", "tipo": lv.get("kind", "pivote"),
                        "precio": lv["price"], "mcap": lv.get("mcap")})
    niveles.append({"tipo": "ahora", "precio": a["price"], "mcap": a.get("mcap_now")})
    for lv in a["supports"][:4]:
        niveles.append({"lado": "S", "tipo": lv.get("kind", "pivote"),
                        "precio": lv["price"], "mcap": lv.get("mcap")})

    hu = stats.get("hurst", 0.5)
    escalado = ("H=%.2f medido" % hu) if stats.get("hurst_fitted") else "H=0.50 por defecto"
    if stats.get("hurst_fitted"):
        escalado += ", revierte a la media" if hu < 0.45 else (
            ", tendencia persistente" if hu > 0.55 else "")

    out = {
        "mint": a["mint"],
        "nombre": coin.get("name"), "simbolo": coin.get("symbol"),
        "precio_usd": a["price"], "mcap_usd": mcap,
        "liquidez_usd": a["liq"]["liq_usd"], "vol24_usd": a["liq"]["vol24_usd"],
        "edad": _fmt_age(a["age"].get("age_h")),
        "regimen": pred["regime"], "confianza": pred["confidence"],
        "tendencia": a.get("trend"),
        "ballenas": a.get("whale"),
        "setup": a.get("setup"),
        "horizonte_label": a["horizon_label"],
        "horizonte_pedido_h": a["horizon_pedido_h"],
        "horizonte_recortado": a["horizon_recortado"],
        "movimiento_1sigma_pct": a["expected_move_pct"],
        "escalado": escalado,
        "sigma_source": a.get("sigma_source"),
        "dist_source": a.get("dist_source"),
        "curva": {
            "graduado": curve.get("complete"),
            "progreso": curve.get("progress"),
            "precio_graduacion_usd": curve.get("grad_price_sol_usd"),
            "mcap_graduacion_usd": curve.get("grad_mcap_usd"),
            "mcap_graduacion_sol": curve.get("grad_mcap_sol"),
            "sol_faltantes": curve.get("sol_to_grad"),
            "x_para_graduar": curve.get("x_to_grad"),
        },
        "escenarios": a["scenarios"],
        "niveles": niveles,
        "flujo": {
            "trades": a["flow"]["n"], "desbalance": a["flow"]["imbalance"],
            "wallets": a["flow"]["wallets"], "hhi": a["flow"]["hhi"],
            "dev_vendio_usd": a["flow"]["dev_sold_usd"],
            "buys": a["flow"]["buys"], "sells": a["flow"]["sells"],
            "buyers": a["flow"]["buyers"], "sellers": a["flow"]["sellers"],
            "both": a["flow"]["both"],
            "buyer_share": a["flow"]["buyer_share"],
            "buy_usd_share": a["flow"]["buy_usd_share"],
            "buy_usd": a["flow"]["buy_usd"], "sell_usd": a["flow"]["sell_usd"],
            "avg_buy": a["flow"]["avg_buy"], "avg_sell": a["flow"]["avg_sell"],
        },
        "avisos": a["warnings"],
    }
    if why:
        out["porque"] = {
            "base": [[c, pred["prior"][c]] for c in ("SUBIDA", "RANGO", "BAJADA")],
            "filas": [{"signal": r["signal"], "group": r.get("group", "-"), "z": r["z"],
                       "up": r["deltas"]["SUBIDA"], "rg": r["deltas"]["RANGO"],
                       "dn": r["deltas"]["BAJADA"]} for r in pred["contrib"][:12]],
            "topados": pred.get("capped_groups", []),
        }
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, mint, horizonte=6.0):
        """Server-Sent Events: empuja una lectura de precio cada ~1,5s.

        pump.fun aguanta ese ritmo sin throttling, asi que el precio y el
        progreso de la curva se mueven en directo sin tocar GeckoTerminal ni
        gastar el presupuesto de peticiones del analisis completo.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        fails = 0
        n = 0
        pool = creator = None
        liq_hint, hz_hint = None, horizonte
        while True:
            try:
                data = live.tick(mint)
                fails = 0
                pool = data.pop("_pool", None) or pool
                creator = data.pop("_creator", None) or creator

                # Carril medio (~8s): pulso de compras/ventas por ventana.
                # DexScreener aguanta este ritmo sin problema.
                if n % 5 == 0:
                    try:
                        d2 = live.ds_flow(mint)
                        data.update(d2)
                        if d2.get("liq"):
                            liq_hint = d2["liq"]
                    except sources.SourceError:
                        pass

                # Carril lento (~24s): barrera completa con wallets unicas y
                # deteccion del dev. Cuesta una peticion a GeckoTerminal, que
                # solo admite 30/min, asi que va espaciado a proposito.
                if n % 16 == 0:
                    try:
                        data.update(live.gt_flow(
                            pool, creator, liq_usd=liq_hint,
                            horizonte_h=hz_hint,
                            ctx={"progress": data.get("progress"),
                                 "complete": data.get("complete")}))
                    except sources.SourceError:
                        pass
            except sources.SourceError as e:
                fails += 1
                # Tres fallos seguidos: se corta y el navegador reconecta solo.
                if fails >= 3:
                    return
                data = {"error": str(e)}
            except Exception as e:
                # Un bug aqui cortaba el stream sin dejar rastro y la pagina
                # se quedaba con el punto gris. Se registra y se avisa al
                # navegador antes de cerrar.
                sys.stderr.write("  stream %s: %s: %s\n"
                                 % (mint[:12], type(e).__name__, e))
                try:
                    self.wfile.write(("data: %s\n\n" % json.dumps(
                        {"error": "%s: %s" % (type(e).__name__, e)})).encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    pass
                return
            n += 1

            try:
                self.wfile.write(("data: %s\n\n" % json.dumps(data)).encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return  # el navegador cerro la pestaña
            time.sleep(1.5)

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html; charset=utf-8")

        if u.path == "/stream":
            q = (parse_qs(u.query).get("q") or [""])[0]
            try:
                mint = resolve.extract_mint(q)
            except ValueError:
                return self._send(400, "mint invalido", "text/plain; charset=utf-8")
            try:
                hz = float((parse_qs(u.query).get("h") or ["6"])[0])
            except ValueError:
                hz = 6.0
            return self._stream(mint, hz)

        if u.path == "/explicar":
            qs2 = parse_qs(u.query)
            q = (qs2.get("q") or [""])[0]
            clase = (qs2.get("c") or ["SUBIDA"])[0].upper()
            if clase not in ("SUBIDA", "RANGO", "BAJADA"):
                clase = "SUBIDA"
            try:
                h = float((qs2.get("h") or ["6"])[0])
            except ValueError:
                h = 6.0
            try:
                a = analyze.analyze(resolve.extract_mint(q), horizon_h=h)
                body = json.dumps({"parrafos": explain.narrate(a, clase),
                                   "clase": clase}, ensure_ascii=False)
            except (ValueError, sources.SourceError) as e:
                body = json.dumps({"error": str(e)}, ensure_ascii=False)
            except Exception as e:
                body = json.dumps({"error": "%s: %s" % (type(e).__name__, e)},
                                  ensure_ascii=False)
            return self._send(200, body, "application/json; charset=utf-8")

        if u.path == "/buscar":
            try:
                lim = int((parse_qs(u.query).get("limite") or ["10"])[0])
            except ValueError:
                lim = 10
            try:
                filas, errs = scan.find(limit=max(1, min(25, lim)))
                body = json.dumps({"filas": filas, "avisos": errs}, ensure_ascii=False,
                                  default=str)
            except Exception as e:
                body = json.dumps({"error": "%s: %s" % (type(e).__name__, e)},
                                  ensure_ascii=False)
            return self._send(200, body, "application/json; charset=utf-8")

        if u.path != "/api":
            return self._send(404, "no encontrado", "text/plain; charset=utf-8")

        qs = parse_qs(u.query)
        q = (qs.get("q") or [""])[0]
        try:
            h = float((qs.get("h") or ["6"])[0])
        except ValueError:
            h = 6.0

        try:
            mint = resolve.extract_mint(q)
            a = analyze.analyze(mint, horizon_h=h)
            body = json.dumps(payload(a), ensure_ascii=False, default=str)
        except (ValueError, sources.SourceError) as e:
            body = json.dumps({"error": str(e)}, ensure_ascii=False)
        except Exception as e:  # que un token raro no tumbe el servidor
            body = json.dumps({"error": "%s: %s" % (type(e).__name__, e)}, ensure_ascii=False)
        return self._send(200, body, "application/json; charset=utf-8")

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--puerto", type=int, default=8787)
    args = ap.parse_args()

    # Solo loopback: esto no debe quedar expuesto a la red.
    srv = Server(("127.0.0.1", args.puerto), Handler)
    print("pumpscope escuchando en  http://127.0.0.1:%d" % args.puerto)
    print("Ctrl-C para parar.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nparado.")
        srv.shutdown()


if __name__ == "__main__":
    main()
