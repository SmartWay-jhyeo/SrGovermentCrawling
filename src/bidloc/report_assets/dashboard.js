'use strict';
const DATA=JSON.parse(document.getElementById('dataset').textContent);
for(const row of DATA.records)row.regions=[...new Set(row.regions.map(r=>r.trim().replace(/\s+/g,' ')))].filter(Boolean);
const $=id=>document.getElementById(id), el=(tag,text)=>{const x=document.createElement(tag);if(text!==undefined)x.textContent=text;return x;};
const fmt=x=>x===null||x===undefined?'미수집':Number(x).toLocaleString('ko-KR',{maximumFractionDigits:1});
const pct=x=>x===null||x===undefined?'미수집':fmt(x*100)+'%';
const money=x=>x===null||x===undefined?'미수집':Math.abs(x)>=100000000?fmt(x/100000000)+'억 원':fmt(x/10000)+'만 원';
const median=xs=>{const a=xs.filter(x=>x!==null&&x!==undefined).sort((a,b)=>a-b);return a.length?(a[Math.floor((a.length-1)/2)]+a[Math.floor(a.length/2)])/2:null;};
const quantile=(xs,p)=>{const a=xs.filter(x=>x!==null&&x!==undefined).sort((a,b)=>a-b);if(!a.length)return null;const k=(a.length-1)*p,i=Math.floor(k);return a[i]+(a[Math.min(i+1,a.length-1)]-a[i])*(k-i);};
const words=x=>String(x||'').trim().split(/\s+/).filter(Boolean);
const match=(hq,r)=>{const h=words(hq),t=words(r);return t.length>0&&h.length>=t.length&&t.every((v,i)=>v===h[i]);};
const kstTime=x=>{if(!x)return NaN;const normalized=x.replace(' ','T');return Date.parse(/(?:Z|[+-]\d\d:\d\d)$/.test(normalized)?normalized:normalized+'+09:00');};
const state={filtered:[],target:[],location:[],unitprice:[],shortlist:[],limits:{location:40,unitprice:40,shortlist:40},sort:{},hq:DATA.hq};
const details=new Map(DATA.records.map(r=>[r.key,r]));
const byRegion=new Map();
const contractOptions=[...new Set(DATA.records.map(r=>r.contract).filter(Boolean))].sort();
for(const value of contractOptions){const option=el('option',value);option.value=value;$('contract').append(option);}
$('hq').value=DATA.hq;$('begin').value=DATA.meta.filters.begin||'';$('end').value=DATA.meta.filters.end||'';
for(const id of ['begin','end']){if(DATA.meta.filters.begin)$(id).min=DATA.meta.filters.begin;if(DATA.meta.filters.end)$(id).max=DATA.meta.filters.end;}
$('collection').textContent=DATA.meta.collection_status==='DONE'?'기간 수집 완료 · 상세 연결률 별도':'부분 결과 · 수집 미완료';
if(DATA.meta.data_mode==='synthetic')$('collection').textContent='합성 데이터 · 실제 공고 아님';
$('generated').textContent=DATA.meta.generated_at_kst.slice(0,19).replace('T',' ')+' KST';

function showDialog(title,content){$('detail-title').textContent=title;$('detail-content').replaceChildren(content);$('detail').showModal();}
function noticeDetails(row){
 const dl=el('dl');
 const items={'공고번호-차수':row.key,'공고명':row.title,'발주기관':row.issuer,'수요기관':row.agency,'공사현장지역':row.site,'참가가능지역':row.regions.join(' / ')||'지역 제한 미기재 · 공고문 확인','지역 자료 차수':row.regionOrd||'미수집','면허/허용업종 코드':row.codes.join(', ')||'미수집','계약방식':row.contract,'배정예산':money(row.budget),'추정가격':money(row.price),'입찰 마감':row.deadline,'개찰 예정':row.opening,'개찰단위 / 회차':row.openingKeys.map(k=>k.join(' / ')).join(', ')||'미수집','최초 개찰 명부 수':row.counts.map(fmt).join(', ')||'미수집','목록 응답 ID':row.source,'면허 응답 ID':row.licenseSources.join(', '),'지역 응답 ID':row.regionSources.join(', '),'개찰 응답 ID':row.openingSources.join(', '),'전체 참가자격':'UNKNOWN · 복수면허 AND/OR·주력분야·실적·적격심사 원문 확인','품질':row.regionMismatch?'지역 자료의 차수 차이 확인':row.licenseSuspect?'면허 필드 밀림 의심':row.quality||'표시된 품질 오류 없음'};
 for(const [key,value] of Object.entries(items)){dl.append(el('dt',key),el('dd',value===null||value===undefined||value===''?'미수집':value));}
 const box=el('div');box.append(dl);
 if(row.url){const a=el('a','나라장터 원공고 열기 ↗');a.href=row.url;a.target='_blank';a.rel='noopener noreferrer';box.append(a);}
 showDialog('공고 근거',box);
}
function showNotices(title,rows,extra){const box=el('div');box.append(el('p',`${fmt(rows.length)}건 · 복합키 기준 중복 제거. 처음 100건을 표시합니다. 전체 근거는 JSON/CSV에 있습니다.`));if(extra)box.append(el('p',extra));for(const row of rows.slice(0,100)){const card=el('div');card.className='notice-card';const b=el('button',row.title||row.key);b.className='link';b.onclick=()=>noticeDetails(row);card.append(b,el('div',row.key+' · '+(row.agency||'기관 미수집')+' · '+money(row.budget)));box.append(card);}showDialog(title,box);}
$('close-detail').onclick=()=>$('detail').close();

function summaryRow(region,local,province,group,basis){
 const lc=local.flatMap(r=>r.counts),pc=province.flatMap(r=>r.counts),linked=group.filter(r=>r.linked).length;
 const years={};for(const r of group)years[r.year??'미수집']=(years[r.year??'미수집']||0)+1;
 return {region,basis,local:basis==='참가가능지역'?local.length:null,province:basis==='참가가능지역'?province.length:null,total:group.length,localMedian:median(lc),localN:lc.length,provinceMedian:median(pc),provinceN:pc.length,price:median(group.map(r=>r.price)),linked,rate:group.length?linked/group.length:null,years,keys:group.map(r=>r.key)};
}
function calculateLocation(){
 const data=state.target,covered=data.filter(r=>r.regions.length).length,coverage=data.length?covered/data.length:null;
 state.regionCoverage=coverage;
 byRegion.clear();
 if(coverage===null||coverage<.8){
  state.basis='공사현장지역 (참가 자격 지역이 아님)';
  for(const r of data){const site=r.site||'현장지역 미수집';if(!byRegion.has(site))byRegion.set(site,[]);byRegion.get(site).push(r);}
  state.location=[...byRegion].sort((a,b)=>a[0].localeCompare(b[0],'ko')).map(([name,rows])=>summaryRow(name,[],[],rows,state.basis));
 }else{
  state.basis='참가가능지역';
  const buckets=new Map();for(const r of data)for(const region of r.regions){if(!buckets.has(region))buckets.set(region,[]);buckets.get(region).push(r);}
  const selected=new Set(data.flatMap(r=>r.regions.filter(x=>words(x).length>=2)));if(words(state.hq).length>=2)selected.add(state.hq);
  state.location=[...selected].sort((a,b)=>a.localeCompare(b,'ko')).map(city=>{
   const w=words(city),local=new Map(),province=new Map();
   for(let i=2;i<=w.length;i++)for(const r of buckets.get(w.slice(0,i).join(' '))||[])local.set(r.key,r);
   for(const r of buckets.get(w[0])||[])if(!local.has(r.key))province.set(r.key,r);
   return summaryRow(city,[...local.values()],[...province.values()],[...local.values(),...province.values()],state.basis);
  }).filter(r=>r.total>0||r.region===state.hq);
 }
 $('location-basis').textContent=state.basis+' · 지역 연결률 '+pct(coverage);
 $('location-basis').classList.toggle('warning',state.basis!=='참가가능지역');
}
function calculateUnitprice(){
 const groups=new Map();for(const r of state.filtered){if(!r.category||!r.municipality)continue;const key=r.category+'|'+r.year;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r);}
 state.unitprice=[...groups.values()].map(rows=>{
  const by=new Map();for(const r of rows){if(!by.has(r.municipality))by.set(r.municipality,[]);by.get(r.municipality).push(r);}
  const annual=[],annualDetail=[];for(const [name,rs] of by){const known=rs.filter(r=>r.budget!==null);const sum=known.length?known.reduce((a,r)=>a+r.budget,0):null;annualDetail.push({municipality:name,knownBudgetSum:sum,missing:rs.length-known.length});if(known.length===rs.length)annual.push(sum);}
  const budgets=rows.map(r=>r.budget),year=rows[0].year,licenseCount=rows.filter(r=>r.codes.includes('4992')).length;
  const distribution={};for(const r of rows)for(const code of r.codes)distribution[code]=(distribution[code]||0)+1;
  const begin=$('begin').value,end=$('end').value;
  const partial=year===null||Boolean((begin&&begin>year+'-01-01')||(end&&end<year+'-12-31'));
  const known=budgets.filter(v=>v!==null);
  return {category:rows[0].category,year,partial,total:rows.length,municipalities:by.size,budget:median(budgets),budgetP25:quantile(budgets,.25),budgetP75:quantile(budgets,.75),budgetMean:known.length?known.reduce((a,b)=>a+b,0)/known.length:null,annual:median(annual),annualP25:quantile(annual,.25),annualP75:quantile(annual,.75),annualMean:annual.length?annual.reduce((a,b)=>a+b,0)/annual.length:null,annualN:annual.length,price:median(rows.map(r=>r.price)),missing:rows.filter(r=>r.budget===null).length,licenseRatio:licenseCount/rows.length,licenseMissing:rows.filter(r=>!r.codes.length).length,distribution,annualDetail,keys:rows.map(r=>r.key)};
 }).sort((a,b)=>a.category.localeCompare(b.category,'ko')||(a.year??0)-(b.year??0));
}
function calculateShortlist(){
 const now=Date.now();state.shortlist=[];
 for(const r of state.target){if(!(kstTime(r.deadline)>now))continue;let status=null;if(!r.regions.length)status='unspecified';else if(r.regions.some(region=>match(state.hq,region)))status=r.regionMismatch||r.licenseSuspect?'review':'allowed';if(status)state.shortlist.push({...r,status});}
 state.shortlist.sort((a,b)=>kstTime(a.deadline)-kstTime(b.deadline));
 const a=state.shortlist.filter(r=>r.status==='allowed').length,u=state.shortlist.filter(r=>r.status==='unspecified').length,v=state.shortlist.filter(r=>r.status==='review').length;
 $('shortlist-note').textContent=`본점 ${state.hq||'미입력'} · 지역 일치 ${fmt(a)}건 / 지역 미기재 ${fmt(u)}건 / 차수·품질 확인 ${fmt(v)}건. 스냅샷 수집 범위는 ${DATA.meta.filters.end||'미수집'}까지이며, 이후 정정·취소는 원공고에서 확인하세요. 마감 여부는 화면을 갱신한 현재 시각으로 계산합니다.`;
}
function metric(title,value,note){const box=el('div');box.className='metric';box.append(el('h3',title),el('strong',value),el('small',note));return box;}
function updateMetrics(){const linked=state.target.filter(r=>r.linked).length;$('metrics').replaceChildren(metric('분석 대상 공고',fmt(state.target.length)+'건','최신 차수 · 취소·연결 재공고 제외'),metric('참가가능지역 연결',pct(state.regionCoverage),'지역 미기재 '+fmt(state.target.filter(r=>!r.regions.length).length)+'건'),metric('최초 개찰 연결',pct(state.target.length?linked/state.target.length:null),fmt(linked)+' / '+fmt(state.target.length)+' 공고'),metric('본점 지역 일치 · 마감 전',fmt(state.shortlist.filter(r=>r.status==='allowed').length)+'건','전체 참가자격은 공고문 확인'));}

const columns={
 location:[['region','지역',r=>{const b=el('button',r.region);b.className='link';b.onclick=()=>showNotices(r.region+' · 근거 공고',r.keys.map(k=>details.get(k)), '사업연도별 건수: '+JSON.stringify(r.years));return b;}],['local','관내 공고',r=>fmt(r.local)],['province','도 단위 공고',r=>fmt(r.province)],['total','합계',r=>fmt(r.total)],['localMedian','관내 명부 수 · 표본',r=>fmt(r.localMedian)+' / n='+fmt(r.localN)],['provinceMedian','도 단위 명부 수 · 표본',r=>fmt(r.provinceMedian)+' / n='+fmt(r.provinceN)],['price','추정가격 중앙값',r=>money(r.price)],['rate','최초 개찰 연결률',r=>pct(r.rate)+' ('+r.linked+'/'+r.total+')']],
 unitprice:[['category','분류',r=>{const b=el('button',r.category);b.className='link';b.onclick=()=>showNotices(r.category+' · '+r.year,r.keys.map(k=>details.get(k)),`공고 예산 P25 ${money(r.budgetP25)} / P75 ${money(r.budgetP75)} / 평균 ${money(r.budgetMean)}. 연간 합계 P25 ${money(r.annualP25)} / P75 ${money(r.annualP75)} / 평균 ${money(r.annualMean)}. 코드 관측 건수: ${JSON.stringify(r.distribution)}`);return b;}],['year','사업연도',r=>(r.year??'미수집')+(r.partial?' (부분)':'')],['total','공고 / 지자체',r=>fmt(r.total)+' / '+fmt(r.municipalities)],['budget','건당 예산 중앙값',r=>money(r.budget)],['annual','연간 합계 중앙값',r=>money(r.annual)+' (n='+r.annualN+')'],['price','추정가격 중앙값',r=>money(r.price)],['missing','예산 결측',r=>fmt(r.missing)],['licenseRatio','4992 코드 관측',r=>pct(r.licenseRatio)+' / 면허 미수집 '+r.licenseMissing]],
 shortlist:[['title','공고명 / 번호',r=>{const b=el('button',(r.title||'제목 미수집')+' · '+r.key);b.className='link';b.onclick=()=>noticeDetails(r);return b;}],['agency','발주·수요기관',r=>r.agency||r.issuer||'미수집'],['site','공사현장',r=>r.site||'미수집'],['regions','참가가능지역',r=>r.regions.join(' / ')||'지역 제한 미기재'],['price','추정가격',r=>money(r.price)],['budget','배정예산',r=>money(r.budget)],['contract','계약방식',r=>r.contract||'미수집'],['deadline','마감 / 개찰',r=>(r.deadline||'미수집')+' / '+(r.opening||'미수집')]]
};
function table(container,cols,rows,id,limit=40){
 const box=$(container);box.replaceChildren();if(!rows.length){const empty=el('p','조건에 해당하는 관측 자료가 없습니다. 미수집·미확인을 참가불가로 해석하지 마세요.');empty.className='empty';box.append(empty);return;}
 const t=el('table'),head=el('thead'),tr=el('tr'),body=el('tbody');
 for(const [key,label] of cols){const th=el('th'),button=el('button',label+(state.sort[id]?.key===key?(state.sort[id].dir===1?' ↑':' ↓'):''));th.scope='col';th.setAttribute('aria-sort',state.sort[id]?.key===key?(state.sort[id].dir===1?'ascending':'descending'):'none');button.onclick=()=>{state.sort[id]={key,dir:state.sort[id]?.key===key?-state.sort[id].dir:1};render(id);};th.append(button);tr.append(th);}head.append(tr);
 for(const row of rows.slice(0,limit)){const tr=el('tr');for(const [key,label,format] of cols){const td=el('td');td.dataset.label=label;const content=format?format(row):fmt(row[key]);td.append(content instanceof Node?content:document.createTextNode(String(content)));tr.append(td);}body.append(tr);}
 t.append(head,body);box.append(t);
}
function displayed(id){let rows=state[id];if(id==='location'){const q=$('region-search').value.trim().toLocaleLowerCase();rows=rows.filter(r=>r.region.toLocaleLowerCase().includes(q));}if(id==='shortlist'){const q=$('notice-search').value.trim().toLocaleLowerCase();rows=rows.filter(r=>r.status===$('candidate').value&&[r.title,r.key,r.agency,r.issuer].some(x=>String(x||'').toLocaleLowerCase().includes(q)));}const sort=state.sort[id];if(sort)rows=[...rows].sort((a,b)=>{const x=a[sort.key],y=b[sort.key];if(x===null||x===undefined)return y===null||y===undefined?0:1;if(y===null||y===undefined)return -1;return sort.dir*(typeof x==='number'&&typeof y==='number'?x-y:String(x).localeCompare(String(y),'ko'));});return rows;}
function render(id){const rows=displayed(id);table(id+'-table',columns[id],rows,id,state.limits[id]);document.querySelector(`[data-more="${id}"]`).hidden=rows.length<=state.limits[id];if(id==='location')$('location-count').textContent=fmt(rows.length)+'개 지역 · '+Math.min(rows.length,state.limits[id])+'개 표시';if(id==='shortlist')$('shortlist-count').textContent=fmt(rows.length)+'건 · 마감이 가까운 순 (열 제목으로 변경 가능)';}
function apply(){
 const begin=$('begin').value,end=$('end').value,min=$('min-price').value,max=$('max-price').value,contract=$('contract').value;
 if((begin&&end&&begin>end)||(min!==''&&max!==''&&Number(min)>Number(max))){$('filter-status').textContent='조건 오류: 시작일/최소 금액이 종료일/최대 금액보다 큽니다.';return;}
 state.hq=$('hq').value.trim();state.filtered=DATA.records.filter(r=>(!begin||(r.published||'').slice(0,10)>=begin)&&(!end||(r.published||'').slice(0,10)<=end)&&(!contract||r.contract===contract)&&(min===''||(r.price!==null&&r.price>=Number(min)))&&(max===''||(r.price!==null&&r.price<=Number(max))));
 state.target=state.filtered.filter(r=>r.relevant&&r.codes.includes('4992'));
 for(const id of ['location','unitprice','shortlist'])state.limits[id]=40;
 calculateLocation();calculateUnitprice();calculateShortlist();updateMetrics();for(const id of ['location','unitprice','shortlist'])render(id);
 $('filter-status').textContent=`${begin||'전체 시작'} ~ ${end||'전체 끝'} · 면허 4992 · ${contract||'전체 계약방식'} · 추정가격 ${min?fmt(min)+'원':'하한 없음'} ~ ${max?fmt(max)+'원':'상한 없음'}`;
 $('profile-note').textContent=$('profile').value+'의 전체 참가자격: UNKNOWN';
}
$('filters').onsubmit=e=>{e.preventDefault();apply();};
$('reset').onclick=()=>{HTMLFormElement.prototype.reset.call($('filters'));$('hq').value=DATA.hq;$('begin').value=DATA.meta.filters.begin||'';$('end').value=DATA.meta.filters.end||'';state.sort={};apply();};
$('region-search').oninput=()=>render('location');$('notice-search').oninput=()=>render('shortlist');$('candidate').onchange=()=>render('shortlist');
for(const b of document.querySelectorAll('[data-more]'))b.onclick=()=>{state.limits[b.dataset.more]+=40;render(b.dataset.more);};
const csvCell=v=>{let t=v===null||v===undefined?'':typeof v==='object'?JSON.stringify(v):String(v);if(/^[\s]*[=+@-]/.test(t))t="'"+t;return '"'+t.replaceAll('"','""')+'"';};
for(const b of document.querySelectorAll('[data-export]'))b.onclick=()=>{const id=b.dataset.export,rows=displayed(id);const fields=[...new Set(rows.flatMap(r=>Object.keys(r)))];const content='\uFEFF'+[fields,...rows.map(r=>fields.map(k=>r[k]))].map(row=>row.map(csvCell).join(',')).join('\r\n');const blob=new Blob([content],{type:'text/csv;charset=utf-8'});const a=el('a');a.href=URL.createObjectURL(blob);a.download=id+'-filtered.csv';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
$('theme').onclick=()=>{const current=document.documentElement.dataset.theme||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');document.documentElement.dataset.theme=current==='dark'?'light':'dark';$('theme').textContent=current==='dark'?'다크 모드':'라이트 모드';};

function staticSections(){
 const labels={LIST:'공사 목록',LICENSE:'면허제한',REGION:'참가가능지역',OPENING:'개찰결과'};
 const table=el('table'),thead=el('thead'),h=el('tr');for(const label of ['단계','완료 구간 / 전체','수신 행','미완료 / 실패','남은 호출 추정']){const th=el('th',label);th.scope='col';h.append(th);}thead.append(h);const tbody=el('tbody');
 for(const stage of Object.keys(labels)){const parts=DATA.meta.partitions.filter(r=>r.stage===stage),total=parts.reduce((s,p)=>s+p.partitions,0),done=parts.filter(p=>p.status==='DONE').reduce((s,p)=>s+p.partitions,0),rows=parts.reduce((s,p)=>s+(p.rows_received||0),0),failed=parts.filter(p=>p.status==='FAILED').reduce((s,p)=>s+p.partitions,0);const estimates=done?Math.ceil((total-done)*Math.max(1,rows/done/999)):null;const values=[labels[stage],fmt(done)+' / '+fmt(total),fmt(rows),fmt(total-done)+' / '+fmt(failed),total===0?'미수집':estimates===null?'표본 부족':fmt(estimates)+'회 (관측 평균)'];const tr=el('tr');values.forEach((value,i)=>{const td=el('td',value);td.dataset.label=['단계','완료 구간 / 전체','수신 행','미완료 / 실패','남은 호출 추정'][i];tr.append(td);});tbody.append(tr);}table.append(thead,tbody);$('progress-table').append(table);
 const labelsQuality={license_field_shift:'면허 필드 밀림 의심',amount_out_of_range:'금액 int64 범위 초과',conflicts:'동일 복합키 내용 충돌'};
 for(const [container,items] of [['relevance',DATA.meta.relevance],['quality',DATA.meta.quality]])for(const [key,value] of Object.entries(items)){const line=el('div');line.className='stat-line';line.append(el('span',labelsQuality[key]||key),el('strong',fmt(value)+'건'));$(container).append(line);}
 for(const note of DATA.meta.notes)$('notes').append(el('li',note));
}
staticSections();apply();
