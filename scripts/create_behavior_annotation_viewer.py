#!/usr/bin/env python3
"""Create a local, raw-image bbox/posture annotation page. No automatic labels."""
import argparse
import json
import os
from pathlib import Path

HTML = r'''<!doctype html><html lang="tr"><meta charset="utf-8"><title>İzge insan etiketleme pilotu</title>
<style>body{font:16px system-ui;background:#151b22;color:#ecf1f5;margin:20px}button,input,select{font:inherit;margin:4px;padding:6px}canvas{max-width:100%;background:#000;touch-action:none}#objects{display:flex;gap:12px;flex-wrap:wrap}.object{padding:12px;border:1px solid #567}a{color:#9fdcff}.note{color:#ffdb86}#status{font-weight:bold}</style>
<h1 id="brand"></h1><p class="note">Ham kareler. Model önerisi, ROI onayı ve otomatik gerçek etiket yok. Kutuyu görünür hayvan için çiz; örtülü parçayı tahmin etme. Maske altkümesini ayrı instance mask aracıyla hazırlayın.</p>
<label>Etiketleyen kişi <input id="annotator" placeholder="Ad / ekip kimliği"></label>
<button id="previous">Önceki</button><button id="next">Sonraki</button>
<label>Kare <select id="frames"></select></label><button id="review">Bu kareyi insan incelemesi tamamlandı işaretle</button>
<label><input id="negative" type="checkbox">Bu karede etiketlenebilir hayvan yok (yalnız gerçekten boşsa)</label>
<button id="download">Etiket JSON dosyasını indir</button><label>Önceki JSON'u yükle <input id="load" type="file" accept="application/json"></label>
<p id="status"></p><p id="metadata"></p><canvas id="canvas"></canvas><div id="objects"></div>
<p>Yemlikte baş, gerçek yeme ve geviş ayrı hedeflerdir; bu sayfa bunları doldurmaz. ROI doğrulaması ayrı pakettedir. Sonraki oturum için JSON'u indirip saklayın.</p>
<script id="data" type="application/json">__DATA__</script>
<script>
const data=JSON.parse(document.getElementById('data').textContent), manifest=data.manifest;
let labels=data.labels,index=0,start=null;
const canvas=document.getElementById('canvas'),ctx=canvas.getContext('2d'),image=new Image();
const row=()=>labels.frames[index], item=()=>manifest.images[index];
document.getElementById('brand').textContent=data.brand+' — insan etiketleme pilotu';
const frames=document.getElementById('frames');
manifest.images.forEach((im,i)=>{const o=document.createElement('option');o.value=i;o.textContent=im.id+' | '+im.camera_id+' | '+im.light_observation;frames.append(o)});
function invalidate(){row().status='unlabeled';row().origin=null;row().annotator=null;row().annotated_at=null}
function draw(){ctx.drawImage(image,0,0,canvas.width,canvas.height);row().objects.forEach((o,i)=>{const b=o.bbox_xyxy;ctx.strokeStyle='#53ff96';ctx.lineWidth=4;ctx.strokeRect(b[0],b[1],b[2]-b[0],b[3]-b[1]);ctx.font='28px sans-serif';ctx.fillStyle='#53ff96';ctx.fillText(String(i+1),b[0]+5,b[1]+30)})}
function render(){frames.value=index;document.getElementById('negative').checked=false;const im=item();canvas.width=im.width;canvas.height=im.height;image.src=im.file;document.getElementById('metadata').textContent=im.id+' | PTS '+im.pts+' | '+im.time_base+' | '+im.offset_s+' sn | kayıt günü doğrulanmadı';document.getElementById('status').textContent='Durum: '+row().status;const parent=document.getElementById('objects');parent.replaceChildren();row().objects.forEach((o,n)=>{const card=document.createElement('div');card.className='object';const title=document.createElement('p');title.textContent='Kutu '+(n+1)+' (hayvan kimliği değil)';card.append(title);for(const [key,choices] of Object.entries({posture:['standing','lying','unknown'],body_visibility:['visible','partial','not_visible','uncertain'],head_visibility:['visible','partial','not_visible','uncertain']})){const label=document.createElement('label');label.textContent=key;const select=document.createElement('select');for(const v of ['',...choices]){const option=document.createElement('option');option.value=v;option.textContent=v||'Henüz etiketlenmedi';select.append(option)}select.value=o[key]||'';select.onchange=()=>{o[key]=select.value||null;invalidate();document.getElementById('status').textContent='Durum: unlabeled'};label.append(select);card.append(label)}const remove=document.createElement('button');remove.textContent='Kutuyu sil';remove.onclick=()=>{row().objects.splice(n,1);invalidate();render()};card.append(remove);parent.append(card)})}
image.onload=draw;
function position(e){const r=canvas.getBoundingClientRect();return [Math.max(0,Math.min(canvas.width,(e.clientX-r.left)*canvas.width/r.width)),Math.max(0,Math.min(canvas.height,(e.clientY-r.top)*canvas.height/r.height))]}
canvas.onpointerdown=e=>{start=position(e);canvas.setPointerCapture(e.pointerId)};
canvas.onpointerup=e=>{if(!start)return;const end=position(e),b=[Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.max(start[0],end[0]),Math.max(start[1],end[1])].map(Math.round);start=null;if(b[2]-b[0]<3||b[3]-b[1]<3)return;row().objects.push({class_name:'cow',bbox_xyxy:b,mask_file:null,segmentation:null,posture:null,body_visibility:null,head_visibility:null,head_at_feeder:null,eating:null,rumination:null});invalidate();render()};
document.getElementById('review').onclick=()=>{const person=document.getElementById('annotator').value.trim();if(!person){alert('Gerçek etiketleyen kişi gerekir');return}if(!row().objects.length&&!document.getElementById('negative').checked){alert('Boş kare gerçekten negatifse ilgili kutucuğu seçin');return}Object.assign(row(),{status:'reviewed',origin:'manual',annotator:person,annotated_at:new Date().toISOString()});render()};
document.getElementById('previous').onclick=()=>{index=Math.max(0,index-1);render()};document.getElementById('next').onclick=()=>{index=Math.min(manifest.images.length-1,index+1);render()};frames.onchange=()=>{index=Number(frames.value);render()};
document.getElementById('download').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(labels,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='annotations.human.json';a.click();URL.revokeObjectURL(url)};
document.getElementById('load').onchange=async e=>{try{const incoming=JSON.parse(await e.target.files[0].text());if(incoming.frames.length!==manifest.images.length||incoming.frames.some((r,i)=>r.image_id!==manifest.images[i].id))throw Error('wrong image set');labels=incoming;render()}catch{alert('JSON bu pilotun kare sırasıyla eşleşmiyor')}};
render();
</script></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    args = parser.parse_args()
    data = {'manifest': json.loads((args.package/'manifest.json').read_text()),
            'labels': json.loads((args.package/'annotations.template.json').read_text()),
            'brand': os.getenv('IZGE_APP_NAME', 'İzge')}
    with (args.package/'annotate.html').open('x') as target:
        target.write(HTML.replace('__DATA__', json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')))
    print('Yerel insan etiketleme sayfası hazır; varsayılan gerçek etiketler boş.')


if __name__ == '__main__':
    main()
