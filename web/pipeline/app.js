const $=id=>document.getElementById(id);
const icons={fog:'<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',image:'<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="m4 18 6-6 4 4 3-3 4 5"/>',vision:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M6 12h12"/>',routing:'<rect x="6" y="6" width="15" height="15" rx="2"/><path d="M3 16V3h13M10 3v18"/>',gemini:'<path d="m12 2 2.7 7.3L22 12l-7.3 2.7L12 22l-2.7-7.3L2 12l7.3-2.7Z"/>',sync:'<rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 18h4"/>'};
const stages=[['image','影像接收','手機上傳商品包裝'],['fog','Fog 決策','快取、雲端與離線佇列'],['vision','Google Vision','辨識文字與位置'],['routing','Region Routing','每張照片一個文字外接框'],['gemini','Gemini','結構化讀取包裝資訊'],['sync','結果同步','等待 App 顯示結果回報']];
let current=null, sampleMode=false, imageKey='', media=null, selectedStage='image';
function node(tag,cls,text){const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el;}
function render(job){current=job;const p=job?.payload||{};const states=job?.stages||{};const sample=job?.mode==='sample';$('mode').textContent=job?.mode==='simulation'?'SIMULATION · 合成演示':sample?'SAVED VISION SAMPLE':'LIVE PIPELINE';$('mode').classList.toggle('sample',sample);$('request-id').textContent=job?.request_id||'等待手機發起掃描';renderStageList(job);
 let title='準備接收影像',detail='在 FoodScan App 掃描商品，這裡會同步呈現實際處理進度。';const active=[...stages].reverse().find(([id])=>states[id]?.status==='active');if(job){title=active?active[1]+'處理中':'等待下一個進度事件';detail='此畫面依實際回報更新，不以動畫時間推估完成。';}if(states.gemini?.status==='done'){title='辨識完成';detail='等待後端結果備妥，再由 App 確認接收。';}if(states.sync?.status==='active'){title='結果已備妥';detail='等待 App 收到並顯示結果後回報。';}if(sample){title='已存分區範例';detail='這是先前的真實 Vision 紀錄；未執行 Gemini，也未連動手機。';}if(job?.phone_received){title='手機已確認接收';detail='同一筆掃描的結果已由 App 回報顯示完成。';}if(job?.failed){title='這次處理未完成';detail=job.error||'請在手機查看提示並重試。';}$('status-title').textContent=title;$('status-detail').textContent=detail;document.querySelector('.signal-line').classList.toggle('busy',!!active&&!sample&&!job?.failed);
 $('notice').hidden=!sample&&!job?.failed&&p.cached!==true;if(p.cached===true)$('notice').textContent='本次使用快取結果，未回報的辨識階段不會標示為已執行。';if(sample)$('notice').textContent='已存歷史分區範例，非本次單框主流程掃描；未執行 Gemini，也未連動手機。';if(job?.failed)$('notice').textContent=detail;
 $('image-name').textContent=p.source_name||p.result?.product_name||(p.source_image?'本次包裝影像':'尚未接收影像');$('image-stats').textContent=p.regions?`${p.regions.length} REGIONS`:(p.word_boxes?`${p.word_boxes} WORD BOXES`:'—');$('image-badge').hidden=!p.source_image;$('image-badge').textContent=p.regions?'VISION + SPATIAL RULES':'IMAGE RECEIVED';
 const key=(job?.request_id||'')+':'+(p.regions?.length||0)+':'+(p.source_image?.length||0);if(key!==imageKey){imageKey=key;if(p.source_image){const im=new Image();im.onload=()=>{if(key!==imageKey)return;media=im;drawPhoto(p);};im.src=p.source_image;}else{$('empty-image').hidden=false;$('photo').hidden=true;}}
 $('crops').replaceChildren();if(p.crops?.length){p.crops.forEach(c=>{const card=node('button','crop');card.type='button';card.setAttribute('aria-label','放大'+c.label);card.title='點擊放大';const im=node('img');im.src=c.image;im.alt=c.label;card.append(im,node('div','',c.label));card.addEventListener('click',()=>openImageZoom(c.image,c.label));$('crops').append(card);});$('crop-count').textContent=p.crops.length+' 張裁圖';}else{['等待文字外接大框'].forEach(label=>$('crops').append(node('div','crop-placeholder',label)));$('crop-count').textContent='等待區域分流';}
 $('phone').classList.toggle('received',!!job?.phone_received);$('sync-state').classList.toggle('received',!!job?.phone_received);$('sync-state').lastElementChild.textContent=job?.phone_received?'App 已確認顯示結果':states.sync?.status==='active'?'等待 App 確認接收':'等待 App 確認接收';
 const phone=$('phone-body');if(p.result){phone.replaceChildren();phone.append(node('div','result-label','本次商品'),node('h3','',p.result.product_name||'商品辨識結果'));if(p.result.health_score!=null)phone.append(node('div','result-score',String(p.result.health_score)),node('div','result-label','App 原有評分結果'));phone.append(node('p','',job.phone_received?'已由 App 回報顯示完成':'後端已備妥，等待 App 確認'));}else{phone.innerHTML='<div class="phone-orbit"><svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M10 4H5v5m17-5h5v5M5 23v5h5m17-5v5h-5M9 16h14"/></svg></div>';phone.append(node('h3','',sample?'等待真實掃描':states.gemini?.status==='done'?'等待結果同步':active?'正在分析':'等待掃描'),node('p','',sample?'分區範例不會傳送結果至 App':'實際結果將顯示於手機 App'));}
 renderInspector(job);renderFogSituation(job);if(job?.mode==='simulation'){$('notice').hidden=false;$('notice').textContent='本地合成事件預覽，未連接 Pi、未呼叫 Vision／Gemini，也未取得手機回報。';}
}
// Keep the opened image fixed while live polling updates the dashboard underneath.
function openImageZoom(src,label){
 const dialog=$('image-zoom');$('image-zoom-title').textContent=label;$('image-zoom-photo').src=src;$('image-zoom-photo').alt=label;
 if(!dialog.open){dialog.showModal();document.body.classList.add('image-zoom-open');}
}
const zoomDialog=$('image-zoom');
$('image-zoom-close').addEventListener('click',()=>zoomDialog.close());
zoomDialog.addEventListener('click',event=>{if(event.target===zoomDialog)zoomDialog.close();});
zoomDialog.addEventListener('close',()=>{document.body.classList.remove('image-zoom-open');$('image-zoom-photo').removeAttribute('src');});
const zoomPhoto=$('photo');zoomPhoto.tabIndex=0;zoomPhoto.setAttribute('role','button');zoomPhoto.title='點擊放大圖片與目前標註';
function openPhotoZoom(){if(!zoomPhoto.hidden&&current?.payload?.source_image)openImageZoom(zoomPhoto.toDataURL('image/png'),zoomPhoto.getAttribute('aria-label')||'包裝影像');}
zoomPhoto.addEventListener('click',openPhotoZoom);
zoomPhoto.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openPhotoZoom();}});

function drawPhoto(payload){const c=$('photo');const scale=Math.min(1,1100/media.naturalWidth,1100/media.naturalHeight);c.width=Math.round(media.naturalWidth*scale);c.height=Math.round(media.naturalHeight*scale);const ctx=c.getContext('2d');ctx.drawImage(media,0,0,c.width,c.height);const dims=payload.image_size||[media.naturalWidth,media.naturalHeight];for(const r of selectedStage==='routing'?(payload.regions||[]):selectedStage==='vision'?(payload.ocr_boxes||[]):[]){const b=r.rect||r.rect_xyxy;if(!b)continue;const x=b[0]/dims[0]*c.width,y=b[1]/dims[1]*c.height,w=(b[2]-b[0])/dims[0]*c.width,h=(b[3]-b[1])/dims[1]*c.height;const color=selectedStage==='vision'?'#70d7e6':r.class==='nutrition'?'#ff6c76':'#f6b669';ctx.strokeStyle=color;ctx.lineWidth=2;ctx.strokeRect(x,y,w,h);ctx.font='15px "Microsoft JhengHei",sans-serif';if(selectedStage==='vision')continue;const label=r.label;const tw=ctx.measureText(label).width;const ly=Math.max(0,y-23);ctx.fillStyle=color;ctx.fillRect(x,ly,tw+12,23);ctx.fillStyle='#152016';ctx.fillText(label,x+6,ly+17);}$('empty-image').hidden=true;c.hidden=false;}
async function poll(){try{const r=await fetch('/api/state',{cache:'no-store'});if(!r.ok)throw new Error();const d=await r.json();$('connection').classList.add('online');$('connection').lastChild.textContent='儀表板服務已連線';if(!sampleMode)render(d.job);}catch{$('connection').classList.remove('online');$('connection').lastChild.textContent='連線中斷，正在重試';}finally{setTimeout(poll,1000);}}
$('sample').addEventListener('click',async()=>{try{const r=await fetch('/api/sample');if(!r.ok)throw new Error();sampleMode=true;render(await r.json());$('live').hidden=false;$('sample').hidden=true;}catch{$('notice').hidden=false;$('notice').textContent='本機找不到先前的分區範例。即時監看仍可使用。';}});
$('live').addEventListener('click',()=>{sampleMode=false;imageKey='';render(null);$('live').hidden=true;$('sample').hidden=false;});
$('fullscreen').addEventListener('click',()=>{if(document.fullscreenElement)document.exitFullscreen();else document.documentElement.requestFullscreen?.();});


function repaint(){if(media && current?.payload?.source_image)drawPhoto(current.payload);}
window.addEventListener('resize',()=>requestAnimationFrame(repaint));
document.addEventListener('fullscreenchange',()=>requestAnimationFrame(repaint));

const stageDescriptions = {
 fog: {title:'Fog 決策',description:'由 Fog 回報實際的快取查詢、雲端轉送與離線補送。',input:'本次條碼、圖片數量與選擇的 Demo 設定。',method:'查詢快取；必要時送雲端，雲端不可用則持久化排隊並在恢復後補送。',output:'Cache Hit／Miss／Bypass、佇列編號、補送次數與實際回傳狀態。'},
 image: {title:'影像接收', description:'接收這次掃描的食品包裝照片，作為後續辨識的共同輸入。', input:'FoodScan App 上傳的商品包裝照片。', method:'接收影像，保留本次掃描識別與照片內容。', output:'本次包裝影像與圖片尺寸。'},
 vision: {title:'Google Vision', description:'找出照片中的文字，並保留每個字詞的位置，提供後續分區使用。', input:'這次掃描的包裝影像。', method:'辨識文字、字詞座標與段落結構。', output:'OCR 文字與文字框；青色框表示實際回傳的字詞位置。'},
 routing: {title:'Region Routing', description:'將每張照片的所有字詞框合成一個文字外接大框，再裁切送給 Gemini。', input:'Vision 回傳的全部字詞座標及原圖尺寸。', method:'合併所有字詞框並加 4%／至少 16px 留白；範圍達九成圖片或無有效文字框時使用完整影像。', output:'每張照片一張裁圖，長邊最多 2048；原圖不另外加入 Gemini 輸入。'},
 gemini: {title:'Gemini', description:'讀取每張照片的單一文字大框裁圖，將可見資訊整理成結構化欄位。', input:'每張照片一張裁圖；回退時該張改用完整影像。', method:'擷取品名、成分、營養及廠商等欄位；無法讀取的欄位保留缺值。', output:'結構化商品辨識結果，接續交給原有後端分析。'},
 sync: {title:'結果同步', description:'辨識後接續原有添加物比對與計分，回傳結果至 App，再等待 App 顯示回報。', input:'後端整理完成的商品分析結果。', method:'回傳給 App；App 顯示結果後回報同一筆掃描識別。', output:'商品結果摘要與 App 顯示確認狀態。'}
};
function selectStage(id){
 selectedStage=id;renderStageList(current);renderInspector(current);renderFogSituation(current);
 if(media&&current?.payload?.source_image)drawPhoto(current.payload);
}
function renderStageList(job){
 if(!$('stages').children.length){
  stages.forEach(([id,title])=>{const row=node('button','stage');row.type='button';row.dataset.stage=id;row.setAttribute('aria-controls','stage-inspector');row.setAttribute('aria-label','查看'+title+'階段');const icon=node('span','stage-icon');const content=node('span','stage-text');const head=node('span','stage-title');head.append(node('span','',title),node('span','stage-time'));content.append(head,node('span','stage-sub'));row.append(icon,content);row.addEventListener('click',()=>selectStage(id));$('stages').append(row);});
 }
 let completed=0,skipped=0;
 stages.forEach(([id,title,desc],index)=>{const rawState=job?.stages?.[id]||{status:'pending'};const state=job?.payload?.gemini_skipped&&id==='gemini'?{status:'skipped',reason:'OCR 文字不足，未呼叫 Gemini'}:rawState;if(state.status==='done')completed++;if(state.status==='skipped')skipped++;const row=$('stages').children[index];row.className='stage '+state.status+(selectedStage===id?' selected':'');row.setAttribute('aria-pressed',String(selectedStage===id));row.querySelector('.stage-icon').innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true">'+(state.status==='done'?'<path d="m6 12 4 4 8-8"/>':icons[id])+'</svg>';row.querySelector('.stage-time').textContent=state.duration_ms==null?'':(state.duration_ms/1000).toFixed(2)+' s';row.querySelector('.stage-sub').textContent=state.status==='skipped'?(state.reason||'本次略過'):state.status==='pending'&&['queued','cloud_unavailable','replay_retry'].includes(job?.payload?.fog_latest?.event)?'等待補送，尚未執行':state.status==='done'?(id==='sync'?'App 已回報顯示結果':'已完成'):state.status==='active'?(id==='sync'?'等待手機確認':'正在處理…'):state.status==='failed'?'未完成':job?.mode==='sample'&&['gemini','sync'].includes(id)?'本範例尚未執行':job?.phone_received?'本次未回報此階段':desc;});
 $('count').textContent=completed+' / '+(stages.length-skipped)+(skipped?' · 略過 '+skipped:'');
}
function renderInspector(job){
 const d={...stageDescriptions[selectedStage]},p=job?.payload||{},state=job?.payload?.gemini_skipped&&selectedStage==='gemini'?{status:'skipped',reason:'OCR 文字不足，未呼叫 Gemini'}:job?.stages?.[selectedStage]||{status:'pending'};
 if(selectedStage==='routing'&&p.ocr_quality?.decision==='retake'){d.title='OCR 品質檢查';d.description='OCR 文字量不足，已在裁切與 Gemini 之前停止。';d.input='所有照片的 Vision OCR 文字。';d.method='合計可辨識的文字與數字字元；只有 0～2 字元時提示重拍。';d.output='重拍提示；未執行裁切，也未呼叫 Gemini。';}
 if(p.regions&&p.routing_policy!=='vision_text_envelope_v1'&&['routing','gemini'].includes(selectedStage)){d.description='此筆是歷史分區紀錄；保留當時實際回報，不代表目前單框主流程。';d.input='此筆紀錄當時使用的影像與文字座標。';d.method='依當時的 reader 策略處理，詳細資料見下方輸出。';d.output='此筆實際回報的區塊、裁圖或結構化結果。';}
 $('detail-step').textContent='STEP '+String(stages.findIndex(s=>s[0]===selectedStage)+1).padStart(2,'0')+' / 點選左側階段查看內容';
 $('visual-title').textContent='階段內容';$('detail-title').textContent=d.title;$('detail-description').textContent=d.description;
 $('detail-input').textContent=d.input;$('detail-method').textContent=d.method;$('detail-output').textContent=d.output;
 const labels={skipped:'本次略過',done:'已完成',active:'進行中',failed:'未完成',pending:job?.mode==='sample'?'本範例尚未執行':'尚未執行或未回報'};
 $('detail-status').textContent=labels[state.status]||labels.pending;$('detail-status').className='detail-status '+state.status;
 let evidence='等待這次掃描的階段資料。',output=null,summary='查看本次輸出';
 if(selectedStage==='fog'){const view=fogView(job);evidence=view.detail;output=p.fog_history||null;summary='查看 Fog 實際事件與佇列編號';}
 if(selectedStage==='image'&&p.source_image)evidence='已接收影像'+(p.image_count?' · 共 '+p.image_count+' 張':'')+(p.image_size?' · '+p.image_size.join(' × ')+' px':'');
 if(selectedStage==='vision'&&p.ocr_boxes){evidence='本次取得 '+p.ocr_boxes.length+' 個字詞框。文字保留 OCR 原始辨識結果，可能含錯字。';output=p.ocr_text||null;summary='查看本次 OCR 文字';}
 if(selectedStage==='routing'&&p.regions){evidence='本次產生 '+p.regions.length+' 個文字區塊；框線代表裁切範圍，不代表欄位分類。';if(p.routing_policy==='vision_text_envelope_v1')evidence='第一張照片使用一個文字外接大框；'+(p.regions[0]?.fallback?'本張回退為完整影像。':'只送裁圖給 Gemini。')+(p.gemini_input_count?' 本次合計 '+p.gemini_input_count+' 張 Gemini 輸入影像。':'');output=p.regions.map(r=>({class:r.class,label:r.label,rect:r.rect||r.rect_xyxy,fallback:r.fallback,retained_area:r.retained_area}));summary='查看本次分區座標';}
 if(selectedStage==='gemini'&&p.gemini_output){evidence='以下是這次 Gemini 實際回傳的結構化結果，尚不等同於最終計分結果。';output=p.gemini_output;summary='查看本次結構化輸出';}
 if(selectedStage==='sync'){evidence=job?.phone_received?'App 已回報顯示這筆結果。':p.result?'後端結果已備妥，尚未收到 App 顯示回報。':'尚未取得後端最終結果與 App 顯示回報。';if(p.result){output=p.result;summary='查看結果摘要';}}
 if(selectedStage==='vision'&&p.ocr_quality){evidence+=' 有效文字／數字共 '+p.ocr_quality.character_count+' 字元；'+(p.ocr_quality.decision==='retake'?'未達 3 字元，提示重拍。':'達到最低文字量，繼續解析（不代表內容正確或完整）。');output={quality:p.ocr_quality,text:p.ocr_text};}
 if(state.status==='done'&&evidence.startsWith('等待'))evidence='此筆紀錄有完成狀態，但未包含這個階段的詳細輸出。';
 if(state.status==='skipped')evidence=state.reason||'Fog 回報本次略過此階段。';
 if(state.status==='failed')evidence=job?.error||'此階段未完成，請查看 App 的提示。';
 if(state.status==='pending'&&job?.mode==='sample')evidence='這份已存範例尚未執行此階段。';
 $('detail-evidence').textContent=evidence;$('output-details').hidden=output===null;$('output-summary').textContent=summary;
 const text=typeof output==='string'?output:output===null?'':JSON.stringify(output,null,2);if($('output-text').textContent!==text)$('output-text').textContent=text;
 const showImage=['image','vision','routing'].includes(selectedStage);$('image-view').hidden=!showImage;document.querySelector('.image-meta').hidden=!showImage;
 $('legend').hidden=selectedStage!=='routing';$('crop-heading').hidden=selectedStage!=='routing';$('crops').hidden=selectedStage!=='routing';
 $('image-badge').textContent=selectedStage==='vision'?'VISION OCR WORD BOXES':selectedStage==='routing'?'VISION + SPATIAL RULES':'ORIGINAL IMAGE';
 $('image-stats').textContent=selectedStage==='vision'?(p.ocr_boxes?p.ocr_boxes.length+' WORD BOXES':'未回報文字框'):selectedStage==='routing'?(p.regions?p.regions.length+' REGIONS':'—'):(p.image_size?p.image_size.join(' × ')+' px':'—');
 $('photo').setAttribute('aria-label',selectedStage==='vision'?'包裝影像與 Vision 字詞框':selectedStage==='routing'?'包裝影像與規則分區框':'原始包裝影像');
}

const fogLabels={received:'Fog 已接收請求',cache_hit:'查到可用快取',cache_miss:'Cache Miss · 未找到可用快取',cache_bypass:'Bypass · 依設定略過讀快取',cache_excluded:'純照片 · 不共用條碼快取',cache_returned:'快取結果已回傳',manager_forwarded:'交由 Fog 管理層處理',manager_returned:'Fog 已回傳處理結果',cloud_forwarded:'Fog 正在送往雲端',cloud_returned:'雲端結果已回到 Fog',cloud_unavailable:'Fog 暫時無法取得雲端結果',queued:'已寫入 Fog 離線佇列',replay_started:'開始補送排隊任務',replay_retry:'補送未完成，等待重試',replay_completed:'補送完成，結果可供 App 取得',replay_failed:'補送失敗，需檢查請求',storage_failed:'暫存失敗，資料尚未保存',unavailable:'Fog 處理未完成'};
function fogView(job){
 const p=job?.payload||{},history=p.fog_history||[],latest=p.fog_latest;
 const decision=[...history].reverse().find(e=>['cache_hit','cache_returned','cache_miss','cache_bypass','cache_excluded'].includes(e.event));
 let path=decision?decision.event==='cache_hit'||decision.event==='cache_returned'?'Cache Hit · '+(decision.cache_status||'HIT'):fogLabels[decision.event]:'尚未收到 Fog 的快取決策';
 let detail=latest?fogLabels[latest.event]:'目前僅有 App 或 Cloud 事件，尚不能判定 Fog 是否命中快取。';
 if(decision?.event==='cache_hit'||decision?.event==='cache_returned')detail+='；Vision、分區與 Gemini 本次未重新執行。';
 if(latest?.queue_id)detail+='；任務 '+latest.queue_id.slice(0,12)+'…';
 if(latest?.attempt)detail+='；第 '+latest.attempt+' 次補送';
 if(latest?.http_status)detail+='；HTTP '+latest.http_status;
 return {path,detail,latest};
}
function pipelineView(job){
 const p=job?.payload||{},history=p.fog_history||[];
 const events=[...history];if(p.fog_latest&&events.at(-1)!==p.fog_latest)events.push(p.fog_latest);
 const routeEvents=['cache_hit','cache_returned','cache_miss','cache_bypass','cache_excluded','cloud_forwarded','cloud_returned','cloud_unavailable','queued','replay_started','replay_retry','replay_completed','replay_failed','storage_failed','unavailable'];
 const event=[...events].reverse().find(e=>routeEvents.includes(e.event))?.event;
 const decision=[...events].reverse().find(e=>['cache_hit','cache_returned','cache_miss','cache_bypass','cache_excluded'].includes(e.event));
 const cloudPath='App → Fog → Cloud（Vision → 單框裁切 → Gemini）→ Fog → App';
 if(!job)return {status:'等待掃描',path:'等待確認路徑',detail:'等待收到本次掃描的處理事件。'};
 if(p.ocr_quality?.decision==='retake')return {status:'文字不足，請重拍',path:'Vision → OCR 品質檢查 → 重拍（未呼叫 Gemini）',detail:p.ocr_quality.reason};
 if(job.mode==='sample')return {status:'歷史分區範例',path:'已存 Vision 紀錄 → 分區預覽',detail:'未執行本次雲端分析，也未連動手機。'};
 if(['storage_failed','replay_failed','unavailable'].includes(event)||job.failed)return {status:'處理未完成',path:event==='storage_failed'?'App → Fog → 暫存失敗':event==='replay_failed'?'Fog 佇列 → 補送失敗':'處理中斷',detail:job.error||fogLabels[event]||'請查看階段與事件紀錄。'};
 if(['replay_started','replay_retry','replay_completed'].includes(event))return {status:{replay_started:'恢復連線，正在補送',replay_retry:'補送未完成，等待重試',replay_completed:'補送完成'}[event],path:'Fog 佇列 → Cloud → Fog → App',detail:event==='replay_completed'?'分析結果已回到 Fog；手機收件狀態見結果同步。':'從 Fog 佇列補送先前保存的任務。'};
 if(event==='queued')return {status:'離線排隊，等待補送',path:'App → Fog → 離線佇列 → 等待雲端恢復',detail:'Fog 已確認保存任務；尚未取得分析結果。'};
 if(event==='cloud_unavailable')return {status:'雲端無法連線',path:'App → Fog → Cloud 連線失敗',detail:'尚待 Fog 回報是否成功寫入離線佇列。'};
 if(['cache_hit','cache_returned'].includes(decision?.event)&&!['cloud_forwarded','cloud_returned'].includes(event))return {status:'Fog 快取命中',path:'App → Fog 快取 → App',detail:'本次使用快取結果，不重新執行 Vision、裁切與 Gemini。'};
 const observedCloud=['vision','routing','gemini'].some(id=>['active','done','failed'].includes(job.stages?.[id]?.status));
 if(['cloud_forwarded','cloud_returned'].includes(event)||observedCloud)return {status:decision?.event==='cache_miss'?'快取未命中，雲端分析':decision?.event==='cache_bypass'?'略過快取，雲端分析':decision?.event==='cache_excluded'?'純照片，雲端分析':'雲端分析',path:decision?cloudPath:'Cloud（Vision → 單框裁切 → Gemini）',detail:decision?'依 Fog 實際回報顯示雲端處理路徑。':'已收到雲端處理事件；尚未收到 Fog 決策，無法確認快取狀態。'};
 if(decision)return {status:fogLabels[decision.event],path:'App → Fog → 等待轉送決策',detail:'尚未收到雲端轉送或離線排隊事件。'};
 return {status:'接收掃描，確認處理路徑',path:'App → 等待 Fog 回報',detail:'尚未收到足以確認快取或雲端路徑的事件。'};
}
function renderFogSituation(job){
 $('fog-situation').hidden=false;
 const p=job?.payload||{},view=fogView(job),pipeline=pipelineView(job);
 $('demo-scenario').textContent=pipeline.status;
 $('pipeline-explanation').textContent=pipeline.detail;
 $('fog-outcome').textContent=pipeline.path;$('fog-explanation').textContent=job&&job.mode!=='sample'?view.detail:'';
 const list=$('fog-timeline');list.replaceChildren();(p.fog_history||[]).forEach(e=>{const text=new Date(e.at*1000).toLocaleTimeString('zh-TW')+' · '+(e.layer==='node'?'Fog Node':'Fog Python')+' · '+(fogLabels[e.event]||e.event)+(e.cache_status?' ['+e.cache_status+']':'')+(e.attempt?' · 第 '+e.attempt+' 次':'');list.append(node('li','',text));});
 if(p.ocr_quality?.decision==='retake'){$('status-title').textContent='文字不足，請重拍';$('status-detail').textContent=p.ocr_quality.reason;const h=$('phone-body').querySelector('h3'),m=$('phone-body').querySelector('p');if(h)h.textContent='請重新拍攝';if(m)m.textContent=p.ocr_quality.reason;$('sync-state').lastElementChild.textContent='等待重新拍攝';}
 const queued=['queued','cloud_unavailable','replay_started','replay_retry'].includes(view.latest?.event);
 if(job&&!job.phone_received&&!job.failed&&queued){$('status-title').textContent=fogLabels[view.latest.event];$('status-detail').textContent=view.detail;
  if(!p.result){const heading=$('phone-body').querySelector('h3'),message=$('phone-body').querySelector('p');if(heading)heading.textContent={queued:'已暫存，等待補送',cloud_unavailable:'雲端暫時無法連線',replay_started:'正在補送',replay_retry:'等待再次補送'}[view.latest.event];if(message)message.textContent=view.detail;$('sync-state').lastElementChild.textContent=view.latest.event==='cloud_unavailable'?'等待 Fog 確認處理方式':'尚未取得分析結果';}
 }
 const skipped=job?.stages?.[selectedStage]?.status==='skipped';if(skipped){$('image-view').hidden=true;document.querySelector('.image-meta').hidden=true;$('legend').hidden=true;$('crop-heading').hidden=true;$('crops').hidden=true;}
}

render(null);poll();

async function loadGroundingConfig(){
 try{const response=await fetch('/api/grounding/config');if(!response.ok)throw new Error();const config=await response.json();$('grounding-config').textContent=config.model+' · '+(config.configured?'後端金鑰已設定':'尚未設定金鑰');}
 catch{$('grounding-config').textContent='無法讀取後端設定';}
}
function renderSearchSuggestions(html){
 const host=$('grounding-suggestions-frame');const root=host.shadowRoot||host.attachShadow({mode:'open'});root.replaceChildren();
 const source=new DOMParser().parseFromString(html,'text/html');
 const tags=new Set(['STYLE','DIV','A','SPAN','SVG','PATH','CIRCLE']);
 const attributes=new Set(['class','width','height','viewBox','fill','xmlns','d','fill-rule','clip-rule','cx','cy','r']);
 function copy(element){
  if(element.nodeType===Node.TEXT_NODE)return document.createTextNode(element.textContent);
  if(element.nodeType!==Node.ELEMENT_NODE||!tags.has(element.tagName.toUpperCase()))return null;
  if(element.tagName==='STYLE'&&/url\s*\(|@import/i.test(element.textContent))return null;
  const clean=element.namespaceURI==='http://www.w3.org/2000/svg'?document.createElementNS(element.namespaceURI,element.localName):document.createElement(element.localName);
  for(const attribute of element.attributes)if(attributes.has(attribute.name))clean.setAttribute(attribute.name,attribute.value);
  if(element.tagName==='A'){try{const url=new URL(element.getAttribute('href'));if(['https:','http:'].includes(url.protocol)){clean.href=url.href;clean.target='_blank';clean.rel='noopener noreferrer';}}catch{}}
  for(const child of element.childNodes){const safe=copy(child);if(safe)clean.append(safe);}return clean;
 }
 for(const element of [...source.head.childNodes,...source.body.childNodes]){const safe=copy(element);if(safe)root.append(safe);}
}
function renderGrounding(result){
 $('grounding-output').hidden=false;
 $('grounding-status').textContent=result.has_grounding?'已取得來源對應':'未取得完整引用';
 $('grounding-status').className='detail-status '+(result.has_grounding?'done':'pending');
 $('grounding-summary').textContent=(result.model_version||result.model)+' · '+result.elapsed_seconds+' 秒 · '+result.sources.length+' 個來源 / '+result.supports.length+' 段引用對應'+(result.queried_at?' · 查詢時間 '+new Date(result.queried_at).toLocaleString('zh-TW'):'');
 $('grounding-review').hidden=result.mode!=='vendor_events';
 $('grounding-answer').textContent=result.answer||'模型未回傳文字回答。';
 for(const id of ['grounding-queries','grounding-sources','grounding-supports'])$(id).replaceChildren();
 if(!result.queries.length)$('grounding-queries').append(node('p','muted','回應未提供搜尋字串。'));
 result.queries.forEach(query=>$('grounding-queries').append(node('p','search-query',query)));
 if(!result.sources.length)$('grounding-sources').append(node('p','muted','回應未提供可用的網頁來源。'));
 result.sources.forEach(source=>{const row=node('p','grounding-source');const link=node('a','', '['+source.id+'] '+source.title);const url=new URL(source.url);if(!['https:','http:'].includes(url.protocol))return;link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';row.append(link);$('grounding-sources').append(row);});
 if(!result.supports.length)$('grounding-supports').append(node('p','muted','回應未提供回答段落與來源的對應。'));
 result.supports.forEach(support=>{const row=node('p','grounding-support');row.append(node('span','',support.text),node('strong','', ' ['+support.source_ids.join(', ')+']'));$('grounding-supports').append(row);});
 const suggestions=result.search_suggestions_html||'';$('grounding-suggestions').hidden=!suggestions;renderSearchSuggestions(suggestions);
}
$('grounding-form').addEventListener('submit',async event=>{
 event.preventDefault();const query=$('grounding-query').value.trim();const mode=$('grounding-mode').value;const manufacturer=$('grounding-manufacturer').value.trim();const brand=$('grounding-brand').value.trim();const product=$('grounding-product').value.trim();
 if(mode==='general'&&query.length<3){$('grounding-query').focus();return;}
 if(mode==='vendor_events'&&!manufacturer&&!brand){$('grounding-status').textContent='請輸入查詢對象';$('grounding-summary').textContent='廠商或品牌至少填一項，再開始搜尋。';$('grounding-manufacturer').focus();return;}
 const button=$('grounding-run');if(button.disabled)return;button.disabled=true;button.textContent='搜尋與整理中…';document.querySelector('.grounding-results').setAttribute('aria-busy','true');
 $('grounding-output').hidden=true;renderSearchSuggestions('');$('grounding-status').className='detail-status active';$('grounding-status').textContent='正在呼叫 Gemini';$('grounding-summary').textContent='正在等待實際 API 回應，請稍候。';
 const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),65000);
 try{const response=await fetch('/api/grounding/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,mode,manufacturer,brand,product}),signal:controller.signal});const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'搜尋測試未完成。');renderGrounding(result);}
 catch(error){$('grounding-status').textContent='測試未完成';$('grounding-status').className='detail-status failed';$('grounding-summary').textContent=error.name==='AbortError'?'等待逾時，請稍後再試。':error.message;}
 finally{clearTimeout(timer);button.disabled=false;button.textContent='執行搜尋測試 ↗';document.querySelector('.grounding-results').setAttribute('aria-busy','false');}
});
loadGroundingConfig();
$('grounding-mode').addEventListener('change',()=>{
 const events=$('grounding-mode').value==='vendor_events';$('grounding-identity').hidden=!events;$('grounding-review').hidden=!events;document.querySelector('.event-scope').hidden=!events;
 $('grounding-query-label').textContent=events?'補充查詢方向（選填）':'測試問題';$('grounding-query').required=!events;$('grounding-query').minLength=events?0:3;
 $('grounding-output').hidden=true;$('grounding-status').textContent='等待測試';$('grounding-summary').textContent='切換模式後，請重新送出查詢。';
});

// Commands never mark a scan queued, replayed or received; only Fog/App events do.
let demoBusy=false,demoState=null;
function renderDemoControl(data){
 demoState=data;
 const ack=data.ack,fresh=ack&&data.ack_age_seconds<5;
 $('demo-offline').disabled=demoBusy||!data.configured||!fresh||!!data.offline;
 $('demo-restore').disabled=demoBusy||!data.configured;
 let message=!data.configured?'尚未設定控制金鑰；Fog 控制未啟用。':!fresh?'尚未收到 Fog 控制連線，請確認 Pi 已部署並啟用。':ack.blocked?'Fog 已套用：模擬雲端中斷。':'Fog 已回報：斷線模擬已解除。';
 if(data.offline&&(!fresh||ack.generation!==data.generation))message='已送出斷線設定，等待 Fog 確認；尚不能視為已斷線。';
 if(fresh&&ack.blocked){message+=ack.remaining_seconds!=null?' 已有任務入列，約 '+Math.ceil(ack.remaining_seconds)+' 秒後解除模擬。':data.auto_restore_seconds!=null?' 等待快取未命中的任務確實入列後開始倒數。':'請手動恢復，或等待安全逾時自動解除。';}
 $('demo-control-status').textContent=message;
}
async function pollDemoControl(){
 try{const response=await fetch('/api/demo/cloud',{cache:'no-store'});if(!response.ok)throw new Error();renderDemoControl(await response.json());}
 catch{$('demo-control-status').textContent='無法讀取控制狀態；可能需重啟新版儀表板 API。';$('demo-offline').disabled=true;$('demo-restore').disabled=true;}
 finally{setTimeout(pollDemoControl,1000);}
}
async function changeDemoCloud(offline){
 if(demoBusy)return;demoBusy=true;if(demoState)renderDemoControl(demoState);
 try{const response=await fetch('/api/demo/cloud',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({offline,auto_restore_seconds:$('demo-auto').checked?5:null})});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'控制設定未完成');renderDemoControl(data);}
 catch(error){$('demo-control-status').textContent=error.message;}
 finally{demoBusy=false;if(demoState){$('demo-restore').disabled=!demoState.configured;}}
}
$('demo-offline').addEventListener('click',()=>changeDemoCloud(true));
$('demo-restore').addEventListener('click',()=>changeDemoCloud(false));
pollDemoControl();

// Explicit rehearsal preparation: no synthetic progress or paid inference here.
let rehearsalBusy=false,rehearsalTasks=[];
const rehearsalNames={preparing:'準備中',ready:'已準備，等待／完成手機掃描',resetting:'重設中',archived:'已封存，可重新準備',restoring:'還原中',restored:'已還原',failed:'準備失敗'};
async function rehearsalAction(action,body){
 if(rehearsalBusy)return;rehearsalBusy=true;$('rehearsal-prepare').disabled=true;
 try{
  const response=await fetch('/api/rehearsal/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'操作未完成');
  $('rehearsal-status').textContent=result.message;await loadRehearsal(false);
 }catch(error){$('rehearsal-status').textContent=error.message;}
 finally{rehearsalBusy=false;$('rehearsal-prepare').disabled=false;await loadRehearsal(false);}
}
async function loadRehearsal(initial=false){
 try{
  const response=await fetch('/api/rehearsal',{cache:'no-store'});if(!response.ok)throw Error();
  const data=await response.json();rehearsalTasks=data.tasks;const next=data.next_miss||{};
  $('rehearsal-arm').disabled=rehearsalBusy||!!next.armed||!!next.busy;
  $('rehearsal-cancel').disabled=rehearsalBusy||(!next.armed&&!next.result)||!!next.busy;
  $('rehearsal-reset-new').disabled=rehearsalBusy||!data.tasks.some(t=>t.state==='ready'&&!t.existed&&!t.active);
  $('rehearsal-next-status').textContent=next.busy?'Fog 正在領取任務…':next.armed?'已預備下一筆 Miss，等待新版 Fog 領取（剩餘 '+Math.ceil(next.remaining_seconds)+' 秒）':next.result?.applied?'Fog 已確認清除條碼 '+next.result.barcode+' 快取；實際 Miss 路徑請看下方事件。':next.result?.pending?'已交給 Fog，等待實際清除確認；尚未宣稱套用成功。':next.result?.error||'尚未設定下一筆路徑；TEST 純照片不消耗設定。';
  const list=$('rehearsal-list');list.replaceChildren();
  for(const task of data.tasks){
   const row=document.createElement('div');row.className='rehearsal-row';
   const description=document.createElement('p');description.textContent=task.barcode+' · '+(rehearsalNames[task.state]||task.state)+(task.active?' · Cloud 處理中':'')+' · '+(task.existed?'原有商品保留，只清快取':'本輪新增商品可封存')+' · 新增圖片 '+task.new_images+' 張';row.append(description);
   if(task.state==='ready'||task.state==='archived'){
    const button=document.createElement('button');button.className='quiet';button.type='button';button.disabled=!!task.active||rehearsalBusy;
    const action=task.state==='ready'?'reset':'restore';button.textContent=action==='reset'?'重設此輪（可還原）':'還原商品與圖片';
    button.addEventListener('click',()=>rehearsalAction(action,{task_id:task.id}));row.append(button);
   }
   if(task.images.length){const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='查看本輪新增圖片清單';details.append(summary);const ul=document.createElement('ul');for(const image of task.images){const li=document.createElement('li');li.textContent=image;ul.append(li);}details.append(ul);row.append(details);}
   list.append(row);
  }
  if(initial)$('rehearsal-status').textContent=data.tasks.length?'可準備新條碼，或重設下方已完成的展演。':'尚未登記展演條碼。先準備，再由手機掃描。';
 }catch{if(initial)$('rehearsal-status').textContent='展演 API 尚未載入，請重啟新版儀表板服務。';}
}
$('rehearsal-form').addEventListener('submit',event=>{event.preventDefault();rehearsalAction('prepare',{barcode:$('rehearsal-barcode').value.trim()});});
loadRehearsal(true);setInterval(()=>{if(!rehearsalBusy)loadRehearsal(false);},3000);

$('rehearsal-arm').addEventListener('click',()=>rehearsalAction('next-miss',{enabled:true}));
$('rehearsal-cancel').addEventListener('click',()=>rehearsalAction('next-miss',{enabled:false}));
$('rehearsal-reset-new').addEventListener('click',()=>rehearsalAction('reset-new',{task_ids:rehearsalTasks.filter(t=>t.state==='ready'&&!t.existed&&!t.active).slice(0,20).map(t=>t.id)}));
