const $=id=>document.getElementById(id);
const icons={fog:'<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',image:'<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="m4 18 6-6 4 4 3-3 4 5"/>',vision:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M6 12h12"/>',routing:'<rect x="6" y="6" width="15" height="15" rx="2"/><path d="M3 16V3h13M10 3v18"/>',gemini:'<path d="m12 2 2.7 7.3L22 12l-7.3 2.7L12 22l-2.7-7.3L2 12l7.3-2.7Z"/>',sync:'<rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 18h4"/>'};
const stages=[['image','影像接收','手機上傳商品包裝'],['fog','Fog 決策','快取、雲端與離線佇列'],['vision','Google Vision','辨識文字與位置'],['routing','Region Routing','營養標示與密集文字分流'],['gemini','Gemini','結構化讀取包裝資訊'],['sync','結果同步','等待 App 顯示結果回報']];
let current=null, sampleMode=false, imageKey='', media=null, selectedStage='image';
function node(tag,cls,text){const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el;}
function render(job){current=job;const p=job?.payload||{};const states=job?.stages||{};const sample=job?.mode==='sample';$('mode').textContent=job?.mode==='simulation'?'SIMULATION · 合成演示':sample?'SAVED VISION SAMPLE':'LIVE PIPELINE';$('mode').classList.toggle('sample',sample);$('request-id').textContent=job?.request_id||'等待手機發起掃描';renderStageList(job);
 let title='準備接收影像',detail='在 FoodScan App 掃描商品，這裡會同步呈現實際處理進度。';const active=[...stages].reverse().find(([id])=>states[id]?.status==='active');if(job){title=active?active[1]+'處理中':'等待下一個進度事件';detail='此畫面依實際回報更新，不以動畫時間推估完成。';}if(states.gemini?.status==='done'){title='辨識完成';detail='等待後端結果備妥，再由 App 確認接收。';}if(states.sync?.status==='active'){title='結果已備妥';detail='等待 App 收到並顯示結果後回報。';}if(sample){title='已存分區範例';detail='這是先前的真實 Vision 紀錄；未執行 Gemini，也未連動手機。';}if(job?.phone_received){title='手機已確認接收';detail='同一筆掃描的結果已由 App 回報顯示完成。';}if(job?.failed){title='這次處理未完成';detail=job.error||'請在手機查看提示並重試。';}$('status-title').textContent=title;$('status-detail').textContent=detail;document.querySelector('.signal-line').classList.toggle('busy',!!active&&!sample&&!job?.failed);
 $('notice').hidden=!sample&&!job?.failed&&p.cached!==true;if(p.cached===true)$('notice').textContent='本次使用快取結果，未回報的辨識階段不會標示為已執行。';if(sample)$('notice').textContent=p.note;if(job?.failed)$('notice').textContent=detail;
 $('image-name').textContent=p.source_name||p.result?.product_name||(p.source_image?'本次包裝影像':'尚未接收影像');$('image-stats').textContent=p.regions?`${p.regions.length} REGIONS`:(p.word_boxes?`${p.word_boxes} WORD BOXES`:'—');$('image-badge').hidden=!p.source_image;$('image-badge').textContent=p.regions?'VISION + SPATIAL RULES':'IMAGE RECEIVED';
 const key=(job?.request_id||'')+':'+(p.regions?.length||0)+':'+(p.source_image?.length||0);if(key!==imageKey){imageKey=key;if(p.source_image){const im=new Image();im.onload=()=>{if(key!==imageKey)return;media=im;drawPhoto(p);};im.src=p.source_image;}else{$('empty-image').hidden=false;$('photo').hidden=true;}}
 $('crops').replaceChildren();if(p.crops?.length){p.crops.forEach(c=>{const card=node('div','crop');const im=node('img');im.src=c.image;im.alt=c.label;card.append(im,node('div','',c.label));$('crops').append(card);});$('crop-count').textContent=p.crops.length+' 張裁圖';}else{['營養標示','其他文字密集區塊'].forEach(label=>$('crops').append(node('div','crop-placeholder',label)));$('crop-count').textContent='等待區域分流';}
 $('phone').classList.toggle('received',!!job?.phone_received);$('sync-state').classList.toggle('received',!!job?.phone_received);$('sync-state').lastElementChild.textContent=job?.phone_received?'App 已確認顯示結果':states.sync?.status==='active'?'等待 App 確認接收':'等待 App 確認接收';
 const phone=$('phone-body');if(p.result){phone.replaceChildren();phone.append(node('div','result-label','本次商品'),node('h3','',p.result.product_name||'商品辨識結果'));if(p.result.health_score!=null)phone.append(node('div','result-score',String(p.result.health_score)),node('div','result-label','App 原有評分結果'));phone.append(node('p','',job.phone_received?'已由 App 回報顯示完成':'後端已備妥，等待 App 確認'));}else{phone.innerHTML='<div class="phone-orbit"><svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M10 4H5v5m17-5h5v5M5 23v5h5m17-5v5h-5M9 16h14"/></svg></div>';phone.append(node('h3','',sample?'等待真實掃描':states.gemini?.status==='done'?'等待結果同步':active?'正在分析':'等待掃描'),node('p','',sample?'分區範例不會傳送結果至 App':'實際結果將顯示於手機 App'));}
 renderInspector(job);renderFogSituation(job);if(job?.mode==='simulation'){$('notice').hidden=false;$('notice').textContent='本地合成事件預覽，未連接 Pi、未呼叫 Vision／Gemini，也未取得手機回報。';}
}
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
 routing: {title:'Region Routing', description:'把文字與位置轉成兩類資訊區域，再從原圖裁出對應圖片。', input:'Vision 回傳的文字內容及座標。', method:'用多種營養關鍵字定位營養表，再依距離、文字量及密度合併其餘文字。', output:'營養標示與其他文字密集區塊的座標及裁圖。'},
 gemini: {title:'Gemini', description:'讀取原圖與局部裁圖，將包裝上可見的資訊整理成結構化欄位。', input:'本次商品原圖與規則產生的區域裁圖。', method:'擷取品名、成分、營養及廠商等欄位；無法讀取的欄位保留缺值。', output:'結構化商品辨識結果，接續交給原有後端分析。'},
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
 stages.forEach(([id,title,desc],index)=>{const state=job?.stages?.[id]||{status:'pending'};if(state.status==='done')completed++;if(state.status==='skipped')skipped++;const row=$('stages').children[index];row.className='stage '+state.status+(selectedStage===id?' selected':'');row.setAttribute('aria-pressed',String(selectedStage===id));row.querySelector('.stage-icon').innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true">'+(state.status==='done'?'<path d="m6 12 4 4 8-8"/>':icons[id])+'</svg>';row.querySelector('.stage-time').textContent=state.duration_ms==null?'':(state.duration_ms/1000).toFixed(2)+' s';row.querySelector('.stage-sub').textContent=state.status==='skipped'?(state.reason||'本次略過'):state.status==='pending'&&['queued','cloud_unavailable','replay_retry'].includes(job?.payload?.fog_latest?.event)?'等待補送，尚未執行':state.status==='done'?(id==='sync'?'App 已回報顯示結果':'已完成'):state.status==='active'?(id==='sync'?'等待手機確認':'正在處理…'):state.status==='failed'?'未完成':job?.mode==='sample'&&['gemini','sync'].includes(id)?'本範例尚未執行':job?.phone_received?'本次未回報此階段':desc;});
 $('count').textContent=completed+' / '+(stages.length-skipped)+(skipped?' · 略過 '+skipped:'');
}
function renderInspector(job){
 const d=stageDescriptions[selectedStage],p=job?.payload||{},state=job?.stages?.[selectedStage]||{status:'pending'};
 $('detail-step').textContent='STEP '+String(stages.findIndex(s=>s[0]===selectedStage)+1).padStart(2,'0')+' / 點選左側階段查看內容';
 $('visual-title').textContent='階段內容';$('detail-title').textContent=d.title;$('detail-description').textContent=d.description;
 $('detail-input').textContent=d.input;$('detail-method').textContent=d.method;$('detail-output').textContent=d.output;
 const labels={skipped:'本次略過',done:'已完成',active:'進行中',failed:'未完成',pending:job?.mode==='sample'?'本範例尚未執行':'尚未執行或未回報'};
 $('detail-status').textContent=labels[state.status]||labels.pending;$('detail-status').className='detail-status '+state.status;
 let evidence='等待這次掃描的階段資料。',output=null,summary='查看本次輸出';
 if(selectedStage==='fog'){const view=fogView(job);evidence=view.detail;output=p.fog_history||null;summary='查看 Fog 實際事件與佇列編號';}
 if(selectedStage==='image'&&p.source_image)evidence='已接收影像'+(p.image_count?' · 共 '+p.image_count+' 張':'')+(p.image_size?' · '+p.image_size.join(' × ')+' px':'');
 if(selectedStage==='vision'&&p.ocr_boxes){evidence='本次取得 '+p.ocr_boxes.length+' 個字詞框。文字保留 OCR 原始辨識結果，可能含錯字。';output=p.ocr_text||null;summary='查看本次 OCR 文字';}
 if(selectedStage==='routing'&&p.regions){evidence='本次產生 '+p.regions.filter(r=>r.class==='nutrition').length+' 個營養區、'+p.regions.filter(r=>r.class==='dense_text').length+' 個其他文字密集區。';output=p.regions.map(r=>({class:r.class,label:r.label,rect:r.rect||r.rect_xyxy}));summary='查看本次分區座標';}
 if(selectedStage==='gemini'&&p.gemini_output){evidence='以下是這次 Gemini 實際回傳的結構化結果，尚不等同於最終計分結果。';output=p.gemini_output;summary='查看本次結構化輸出';}
 if(selectedStage==='sync'){evidence=job?.phone_received?'App 已回報顯示這筆結果。':p.result?'後端結果已備妥，尚未收到 App 顯示回報。':'尚未取得後端最終結果與 App 顯示回報。';if(p.result){output=p.result;summary='查看結果摘要';}}
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
function renderFogSituation(job){
 const sample=job?.mode==='sample';$('fog-situation').hidden=!job||sample;if(!job||sample)return;
 const p=job.payload||{},view=fogView(job),intent={cloud:'完整雲端（略過讀快取）',miss:'首次掃描（預期 Cache Miss）',cache:'Fog 快取展示',queue:'離線暫存與補送'};
 $('demo-scenario').textContent=intent[p.demo_scenario]||'一般掃描（未指定 Demo）';
 $('fog-outcome').textContent=view.path;$('fog-explanation').textContent=view.detail;
 const list=$('fog-timeline');list.replaceChildren();(p.fog_history||[]).forEach(e=>{const text=new Date(e.at*1000).toLocaleTimeString('zh-TW')+' · '+(e.layer==='node'?'Fog Node':'Fog Python')+' · '+(fogLabels[e.event]||e.event)+(e.cache_status?' ['+e.cache_status+']':'')+(e.attempt?' · 第 '+e.attempt+' 次':'');list.append(node('li','',text));});
 const queued=['queued','cloud_unavailable','replay_started','replay_retry'].includes(view.latest?.event);
 if(!job.phone_received&&!job.failed&&queued){$('status-title').textContent=fogLabels[view.latest.event];$('status-detail').textContent=view.detail;
  if(!p.result){const heading=$('phone-body').querySelector('h3'),message=$('phone-body').querySelector('p');if(heading)heading.textContent={queued:'已暫存，等待補送',cloud_unavailable:'雲端暫時無法連線',replay_started:'正在補送',replay_retry:'等待再次補送'}[view.latest.event];if(message)message.textContent=view.detail;$('sync-state').lastElementChild.textContent=view.latest.event==='cloud_unavailable'?'等待 Fog 確認處理方式':'尚未取得分析結果';}
 }
 const skipped=job.stages?.[selectedStage]?.status==='skipped';if(skipped){$('image-view').hidden=true;document.querySelector('.image-meta').hidden=true;$('legend').hidden=true;$('crop-heading').hidden=true;$('crops').hidden=true;}
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
