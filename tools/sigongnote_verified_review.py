"""Build the 23-notice review from already extracted LOCAL source documents."""
import collections,csv,datetime,hashlib,json,re,statistics,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'outputs/sigongnote_market_stage2_documents'
OUT=ROOT/'outputs/sigongnote_market_stage2_verified'
CACHE=ROOT/'.local/sigongnote_stage2_verified'
VERSION='sigongnote-document-review-3.0'

def readcsv(path):
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def writecsv(name,rows):
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with (OUT/name).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fields);w.writeheader();w.writerows(rows)
def redact(s):
    s=re.sub(r'https?://\S+','[링크 생략]',s)
    s=re.sub(r'[\w.\-]+@[\w.\-]+','[이메일 생략]',s)
    s=re.sub(r'\b0\d{1,2}[-) ]\d{3,4}[- ]\d{4}\b','[연락처 생략]',s)
    return s

class Review:
    def __init__(self):
        self.docs=json.loads((CACHE/'inventory.json').read_text(encoding='utf-8'))
        self.D={d['document_key']:d for d in self.docs}
        self.blocks={};self.e=[];self.notices=[];self.needs=[];self.checks={}
        self.inputs=readcsv(BASE/'priority_notice_review.csv')
    def key(self,pid,prefix):
        found=[d['document_key'] for d in self.docs if d['priority_id']==pid and d['document_key'].startswith(prefix)]
        assert len(found)==1,(pid,prefix,found)
        return found[0]
    def bs(self,key):
        if key not in self.blocks:self.blocks[key]=json.loads((ROOT/self.D[key]['text_cache']).read_text(encoding='utf-8'))
        return self.blocks[key]
    def find(self,key,pattern,limit=1):
        return [i for i,b in enumerate(self.bs(key)) if re.search(pattern,b['text'])][:limit]
    def add(self,pid,field,value='',key='',indices=(),status='原文確認',kind='',vat='該当なし',gov='該当なし',note='',scope='',amount=None):
        # English machine codes; Korean descriptions are retained in explicit fields.
        doc=self.D.get(key,{})
        if isinstance(indices,int):indices=[indices]
        blocks=[self.bs(key)[i] for i in indices] if key else []
        text=' || '.join(b['text'] for b in blocks)
        if amount is not None:
            nums=[x.replace(',','') for x in re.findall(r'(?<![\d.])\d[\d,]*(?:\.\d+)?',text)]
            assert str(amount) in nums,(pid,field,amount,text)
        eid=f'E{len(self.e)+1:04d}'
        n=next(x for x in self.inputs if x['priority_id']==pid)
        row=dict(evidence_id=eid,priority_id=pid,bid_ntce_no=n['bid_ntce_no'],bid_ntce_ord=n['bid_ntce_ord'],field=field,
                 value=value,amount_krw='' if amount is None else amount,amount_name=kind,amount_period_scope=scope,
                 verification_status=status,vat_scope=vat,government_supplied_scope=gov,document_id=doc.get('document_id',''),
                 document_key=key,document_name=doc.get('document_name',''),archive_member=doc.get('member_path',''),
                 evidence_location='; '.join(b['loc'] for b in blocks),evidence_text=redact(text),source_local_path=doc.get('source_local_path',''),
                 source_sha256=doc.get('sha256',''),source_type=doc.get('source_kind',''),interpretation=note,rule_version=VERSION)
        self.e.append(row);return eid
    def fact(self,pid,field,key,indices,value='',note=''):
        return self.add(pid,field,value,key,indices,status='原文確認',note=note)
    def money(self,pid,name,amount,key,indices,vat,gov,scope='当該発注の予定総額',status='AMOUNT_STATED',note=''):
        return self.add(pid,'amount',str(amount),key,indices,status,name,vat,gov,note,scope,amount)
    def need(self,pid,what,why,priority='中',existing_id='',needed=True):
        n=next(x for x in self.inputs if x['priority_id']==pid)
        self.needs.append(dict(priority_id=pid,bid_ntce_no=n['bid_ntce_no'],bid_ntce_ord=n['bid_ntce_ord'],priority=priority,
                               document_needed=what,reason=why,existing_document_id=existing_id,necessary_for_next_step=int(needed),
                               collection_status='NOT_REQUESTED_OR_RETRIED',network_calls=0))

    def run(self):
        # All downloaded HWP/XLS/PDF/HWPX data were passively parsed first. No network imports/calls here.
        for n in self.inputs:
            pid=n['priority_id'];i=int(pid[1:]);common={k:n.get(k,'') for k in ('priority_id','bid_ntce_no','bid_ntce_ord','title','original_institution','institution_code','institution_type','notice_year','title_business_years','legacy_primary_class','legacy_work_type','commercial_tier','exclusive_group','source_response_id','recorded_presmpt_prce','recorded_bdgt_amt','recorded_vat','recorded_govsplyAmt')}
            common.update(document_review_status='REVIEWED_AVAILABLE_DOCUMENTS',document_work_type='유지보수',document_commercial_tier='우선검토',document_exclusive_group='차선도색·노면표시',document_facility_tags='차선도색·노면표시',
                stated_period='',actual_start_date='',actual_start_status='미확인: 착공계/계약서 없음',actual_management_period='',actual_contract_amount='',actual_execution_amount='',fee_basis_amount='',fee_X='',
                selected_reference_amount='',selected_reference_amount_name='',selected_reference_evidence_id='',selected_reference_vat='미확인',selected_reference_gov='미확인',selected_reference_status='UNCONFIRMED',
                annual_total_explicit='',annual_amount_status='비연간 또는 미확인',workflow_status='문서 일부 확인',workflow_reason='',conflict_notes='',rule_version=VERSION)
            money_ids=[];start=len(self.e)
            if 1<=i<=12:
                desc=next(d['document_key'] for d in self.docs if d['priority_id']==pid and '설계설명서' in d['document_name'])
                purpose=self.find(desc,'원상복구');zone=self.find(desc,'공사위치:');period=self.find(desc,'본 공사의 공사기간은')
                self.fact(pid,'work_scope',desc,purpose,'도로 유지보수로 훼손·제거된 노면표시 원상복구')
                self.fact(pid,'work_area',desc,zone)
                self.fact(pid,'construction_period',desc,period,'착공일부터 2025-12-20까지')
                common.update(stated_period='착공일부터 2025-12-20까지',annual_amount_status='연간단가 해당 발주 총액; 실제 12개월 아님',workflow_status='분산·반복 작업지시/사진/준공 기록 확인',workflow_reason='구역 내 유지보수 후 노면표시 원상복구, 긴급 추가 작업지시, 전·중·후 사진 및 준공서류')
                for field,pat in [('work_orders','작업지시를 접수한 후'),('distributed_emergency','지역 외 긴급히'),('photos_completion','시공 전, 시공 중, 완료'),('measurement_record','수시 시공내용의 물량')]:
                    ids=self.find(desc,pat)
                    if ids:self.fact(pid,field,desc,ids)
                notices=[d['document_key'] for d in self.docs if d['priority_id']==pid and d['document_name'].startswith('공고문')]
                if notices:
                    key=notices[0];a=self.find(key,'라. 공사예정금액:');b=self.find(key,'마. 기초금액:')
                    v=[int(x.replace(',','')) for x in re.findall(r'([\d,]+)원',self.bs(key)[a[0]]['text'])]
                    w=[int(x.replace(',','')) for x in re.findall(r'([\d,]+)원',self.bs(key)[b[0]]['text'])]
                    assert len(v)==3 and len(w)==3 and v[0]==v[1]+v[2] and v[1]==w[0]==w[1]+w[2]
                    self.checks[pid+'_notice_amount_components']=True
                    self.money(pid,'공사예정금액',v[0],key,a,'도급분 VAT 포함; 관급분 자체 VAT 명시 없음','관급자재비 포함',note='도급액+관급자재비 관계 원문과 산술 일치; 전체 VAT 별도액은 미기재')
                    eid=self.money(pid,'도급액 (= 기초금액)',v[1],key,a+b,'포함','관급자재비 별도',note='관리할 시공 도급범위의 예정총액 후보; 최종 과금기준 아님')
                    self.money(pid,'추정가격',w[1],key,b,'별도','관급자재비 별도')
                    self.money(pid,'부가세(도급분)',w[2],key,b,'VAT 자체','관급분 VAT 아님')
                    self.money(pid,'관급자재비',v[2],key,a,'미확인','관급자재 금액 자체')
                    common.update(selected_reference_amount=v[1],selected_reference_amount_name='도급액/기초금액',selected_reference_evidence_id=eid,selected_reference_vat='포함',selected_reference_gov='별도',selected_reference_status='SCOPE_CONFIRMED')
                    self.fact(pid,'bid_amount_shape',key,self.find(key,'총액계약'),'총액계약',note='연간단가라는 제목만으로 이 기초금액을 단위가격으로 분류하지 않음')
                else:
                    sheet=self.key(pid,'d2a5')
                    for label,val,ix in [('총공사비(공내역서 저장값)',4400000,11),('도급액(공내역서 저장값)',4400000,12),('공급가액(공내역서 저장값)',4000000,12),('부가세(공내역서 저장값)',400000,14),('관급액(공내역서 저장값)',0,14)]:
                        self.money(pid,label,val,sheet,ix,'표의 도급 VAT 관계만 확인','공내역서 0 저장값; 실제 관급 없음 아님',status='CACHED_TOTAL_SCOPE_UNCONFIRMED',note='주요 단가·금액 0의 공내역서 캐시합계. 저장 공고금액과 불일치; 사업 전체 금액으로 채택하지 않음')
                    common.update(annual_amount_status='연간단가 과업 확인; 유효 예정총액 미확인',conflict_notes='공내역서 총공사비 저장값 4,400,000원; 기존 공고 예산 492,246,000원과 범위 불일치. 차액을 관급비로 추정하지 않음.')
                    self.need(pid,'실패한 공고문 및 금액이 기입된 총괄내역/관급조서','4,400,000원 캐시총액과 공고 예산 492,246,000원의 범위 대조; 153,678,000원 잔차 원인 확인','높음','e2aa332d7330342c')
            else:
                self.review_other(pid,i,common)
            self.scan_supplementary(pid)
            scope_map={
                13:('교통표지판·지주 철거/설치, 노면표시 도색, 부대포장','남동구 관내'),
                14:('교통안전표지판 설치/교체·이설, 노면표시 도색, 부대포장','남동구 어린이보호구역(간석동·남촌도림동·논현고잔동); 긴급민원 시 관내 전역'),
                15:('교통표지판·지주 철거/설치, 노면표시 도색, 부대포장','남동구 관내'),
                16:('시선유도봉 설치·철거·보수','포천시 14개 읍·면·동 관할 도로구간'),
                17:('차선 재도색','포천시 관리도로 북부권(창수·영중·화현·일동·이동·영북·관인)'),
                18:('전시온실 건립(건축·토목·조경·기계), 연면적 7,272.07㎡','오산동 1060번지, 동탄2 여울공원 내'),
                19:('지하4층/지상5층 복합센터 신축, 연면적 5,902.35㎡','서울 강서구 공항동 687-15 외 2필지'),
                20:('노면표시 정비; 차선도색 46,114m·제거 40m','북부도로사업소 관내 특별시도(종로·성북·강북·도봉·노원)'),
                21:('어린이보호구역 기종점 노면표시·노란색 횡단보도 설치/정비','부평구 어린이보호구역 94개소'),
                22:('스프링클러·화재감지기 등 철거 및 개량 설치','지하철 5호선 동대문역사문화공원역'),
                23:('상록수 전정 335주; 내역서 일부 수량 #REF! 미확인','덕양구 가로화단·중앙분리대 등')}
            if i<=12:
                common['verified_work_scope']='도로 유지보수로 제거·훼손된 노면표시 도색(원상복구)'
                common['verified_work_area']='; '.join(self.bs(desc)[j]['text'] for j in zone)
            else:common['verified_work_scope'],common['verified_work_area']=scope_map[i]
            es=self.e[start:]
            common['scope_evidence_ids']=';'.join(e['evidence_id'] for e in es if e['field'] in ('scope_and_area','work_scope','work_area','bill_work_items'))
            common['period_evidence_ids']=';'.join(e['evidence_id'] for e in es if e['field'] in ('construction_period','period_schedule'))
            common['unit_estimated_price_krw']=next((e['amount_krw'] for e in es if e['amount_name']=='단가 추정가격'),'')
            common['unit_base_price_krw']=next((e['amount_krw'] for e in es if e['amount_name']=='단가 기초금액'),'')
            common['unit_amount_status']='단가 명시 확인' if common['unit_base_price_krw']!='' else '별도 단가 기초금액 미확인/미기재; 총액을 단가로 옮기지 않음'
            common['photos_evidence_status']='첨부 요구사항 확인' if any(e['field']=='photos_completion' for e in es) else '미확인'
            common['work_order_evidence_status']='첨부 지시/감독 요구사항 확인' if any(e['field']=='work_orders' for e in es) else '미확인'
            if i==16:self.need(pid,'실패한 시방서 또는 작업지시/준공 제출물 규정','설계설명서의 감독지시는 확인; 사진·준공 기록 제출범위를 확인할 때 필요','보통','5ba9c0c346d8804c')
            common['evidence_ids']=';'.join(x['evidence_id'] for x in self.e[start:])
            common['amount_evidence_ids']=';'.join(x['evidence_id'] for x in self.e[start:] if x['field']=='amount')
            common['amounts_in_source_json']=json.dumps([{k:e[k] for k in ('evidence_id','amount_name','amount_krw','verification_status','vat_scope','government_supplied_scope','amount_period_scope')} for e in self.e[start:] if e['field']=='amount'],ensure_ascii=False)
            common['source_documents_used']=len({e['document_key'] for e in self.e[start:] if e['document_key']})
            common['vat_and_government_scope_confirmed']=int(common['selected_reference_status']=='SCOPE_CONFIRMED')
            if common['selected_reference_amount']=='':
                self.add(pid,'management_scale_conclusion',status='UNKNOWN',note='관리 대상 사업규모에 적합한 예정총액 미확인. 원문/캐시 관측 금액은 개별 근거행에 보존; 0으로 대체하지 않음')
            self.add(pid,'actual_start_and_contract',status='NOT_COLLECTED',note='착공계·체결계약서·정산서 없음. 공사기간 표현을 실제 착공일/집행기간/계약금액으로 변환하지 않음')
            self.notices.append(common)

    def table_amount(self,pid,key,label,value,idx,vat='미확인',gov='미확인',scope='해당 발주의 예정금액',note=''):
        return self.money(pid,label,value,key,idx,vat,gov,scope=scope,note=note)

    def select(self,n,key,label,amount,idx,vat,gov,period,status='SCOPE_CONFIRMED',scope='해당 발주 예정총액'):
        eid=self.table_amount(n['priority_id'],key,label,amount,idx,vat,gov,scope)
        n.update(selected_reference_amount=amount,selected_reference_amount_name=label,selected_reference_evidence_id=eid,selected_reference_vat=vat,selected_reference_gov=gov,selected_reference_status=status,stated_period=period)

    def review_other(self,pid,i,n):
        if i in (13,14,15):
            n.update(document_work_type='혼합(유지·교체·신설)',document_exclusive_group='복수핵심 통합(교통시설+노면표시)',document_facility_tags='교통시설;차선도색·노면표시',workflow_status='분산 작업지시·사진·준공 기록 확인',workflow_reason='표지판·노면표시·부대 포장 포함; 같은 공고금액은 통합그룹에서 1회 계상')
            if i==13:
                key=self.key(pid,'34aef022e661088e:786e');self.fact(pid,'scope_and_area',key,[2,3,5,11,12,13,14]);self.fact(pid,'construction_period',key,6)
                n.update(stated_period='착공일부터 2025-12-15까지',annual_amount_status='연간단가 과업 확인; 예정총액 미확인')
                self.need(pid,'실패한 공고문 또는 금액이 기입된 설계총괄표','표지·노면표시 통합 과업은 확인; XLS 합계의 숫자는 미확인, 기존 API 추정가격을 원문 검증값으로 대체할 수 없음','높음','59ff7b86a2af835c')
            else:
                key=self.key(pid,'c791' if i==14 else 'ea18');total,base,net,tax,gov=(162779000,156255000,142050000,14205000,6524000) if i==14 else (79667000,79667000,72424546,7242454,None)
                self.table_amount(pid,key,'설계금액',total,[5,6],vat='도급분 VAT 포함',gov='관급자관급 포함' if i==14 else '관급란 - 표시; 금액범위 미확인')
                self.select(n,key,'기초금액',base,[13,14,22,23,30,31,38,39,44,45],'포함','관급자관급 별도' if i==14 else '관급란 -; 0원으로 확정하지 않음','착공일부터 180일' if i==14 else '착공일부터 2025-12-15까지',status='SCOPE_CONFIRMED' if i==14 else 'AMOUNT_CONFIRMED_SCOPE_PARTIAL')
                self.table_amount(pid,key,'추정가격',net,[22,23],'별도','관급자관급 별도' if i==14 else '관급란 -')
                self.table_amount(pid,key,'부가가치세',tax,[30,31],'VAT 자체','해당 없음')
                if gov is not None:self.table_amount(pid,key,'관급자관급자재비',gov,[38,39],'미확인','관급자관급 금액 자체')
                self.fact(pid,'government_blank_mark',key,[44,45] if i==14 else [38,39,44,45],'-',note='원문 대시 표시를 0으로 치환하지 않음')
                desc=self.key(pid,'1e998' if i==14 else '77c0');self.fact(pid,'scope_and_area',desc,[0] if i==14 else [2,3,5,11,12,13,14])
                self.fact(pid,'construction_period',key,62 if i==14 else 60)
                n['annual_amount_status']='연간단가 명칭이나 1차(A구역) 180일 분할발주; 연간총액 아님' if i==14 else '연간단가 해당 발주 총액; 실제 12개월 아님'
                if i==14:self.need(pid,'착공계 및 차수별 발주/관리범위표','180일 1차 A구역을 연간 전체 사업비나 1년 이용권으로 계산하지 않기 위한 범위 확인')
                else:self.need(pid,'금액 기입 총괄표 또는 관급조서','관급자·도급자 관급란의 - 표시 의미를 확인; 총액 자체는 보존')
        elif i in (16,17):
            key=self.key(pid,'53d2' if i==16 else 'fb41');net,tax,base,total=(128381,12839,141220,40000000) if i==16 else (137101,13710,150811,99900000)
            self.fact(pid,'scope_and_area',key,[3,4,6]);self.fact(pid,'construction_period',key,5)
            for name,val,indices,vat in [('단가 추정가격',net,7,'별도'),('단가 부가가치세',tax,7,'VAT 자체'),('단가 기초금액',base,[7,8,9],'포함')]:
                self.table_amount(pid,key,name,val,indices,vat,'미확인',scope='단가; 연간총액과 합산 금지')
            period='착공일부터 2025-12-15까지' if i==16 else '착공일부터 2025-12-19까지'
            self.select(n,key,'연간 총도급예정액',total,10,'미확인','미확인',period,status='AMOUNT_CONFIRMED_SCOPE_PARTIAL',scope='원문에 명시된 연간 총도급예정액')
            n.update(annual_total_explicit=total,annual_amount_status='연간 총도급예정액 명시',workflow_status='분산 유지보수 확인; 기록 과업 일부 확인' if i==16 else '분산 도로/감독지시/사진·준공기록 확인',workflow_reason='단가 투찰액과 연간 예정총액 분리; 착공일 미확인')
            if i==16:
                n.update(document_exclusive_group='교통시설',document_facility_tags='교통시설');desc=self.key(pid,'96f2');self.fact(pid,'work_orders',desc,[2,4,9])
            self.need(pid,'연간 예정액의 VAT·관급 범위가 명시된 총괄표/계약문서','연간 총도급예정액 자체는 확인했으나 단가 VAT 구성을 연간 금액에 소급 적용할 수 없음','높음')
        elif i in (18,19):
            key=self.key(pid,'863b' if i==18 else 'c108')
            n.update(document_work_type='신설·확장(건축)',document_commercial_tier='별도사업모델',document_exclusive_group='신축 건축 복합공사',document_facility_tags='건축;토목;부속조경;기계',workflow_status='집중 건축 프로젝트; 핵심 유지관리 표본과 분리',workflow_reason='온실 건립/복합센터 신설, 다년 기간; 부속 조경을 공원녹지 유지관리로 계상하지 않음')
            if i==18:
                self.fact(pid,'scope_and_area',key,[21,23,27,53,59]);self.fact(pid,'construction_period',key,[24,25])
                for label,val,ids,vat,gov in [('공사예정금액(추정금액)',32863436000,[28,29,30],'도급 VAT 포함; 관급분 VAT 분해 미확인','도급자설치관급 포함; 관급자설치관급 별도'),('추정가격',26697860000,[37,38],'별도','관급 별도'),('부가가치세',2669786000,[41,42],'VAT 자체','해당 없음'),('관급자재비(관급자설치)',9374039000,[43,44,45],'미확인','관급자설치'),('관급자재비(도급자설치)',3495790000,[46,47,48],'미확인','도급자설치')]:self.table_amount(pid,key,label,val,ids,vat,gov,'계속비/810일 전체공사; 연간금액 아님')
                self.select(n,key,'기초금액',29367646000,[33,34,37,38,41,42,43,44,45,46,47,48],'포함','관급자/도급자설치 관급 별도','착공일부터 810일')
            else:
                self.fact(pid,'scope_and_area',key,[46,47,48,53,66]);self.fact(pid,'construction_period',key,49);self.fact(pid,'contract_shape',key,77,'장기계속계약')
                for label,val,ids,vat,gov in [('추정금액',22718705500,[54,55],'도급 VAT 포함; 관급분 VAT 분해 미확인','도급자설치관급 포함; 관급자설치관급 별도'),('추정가격',18130675000,54,'별도','관급 별도'),('부가가치세',1813067500,54,'VAT 자체','해당 없음'),('도급자설치관급액',2774963000,55,'미확인','도급자설치'),('관급자설치관급액',3652995000,56,'미확인','관급자설치')]:self.table_amount(pid,key,label,val,ids,vat,gov,'장기계속/900일 전체공사; 연간금액 아님')
                self.select(n,key,'업종별 금액(추정가격+부가가치세)',19943742500,[54,55,56,57,60,63],'포함','관급자/도급자설치 관급 별도','착공일부터 900일')
        elif i==20:
            key=self.key(pid,'7942');desc=self.key(pid,'2a66')
            self.fact(pid,'scope_and_area',desc,[3,4,5,6,7]);self.fact(pid,'construction_period',desc,9);self.fact(pid,'work_orders',desc,9);self.fact(pid,'photos_completion',desc,24);self.fact(pid,'work_type_explicit',key,33,'유지보수공사')
            self.select(n,key,'기초금액',429176000,9,'포함','관급자재 구매는 명시; 포함 범위 미확인','착공일부터 2025-12-31까지',status='AMOUNT_CONFIRMED_SCOPE_PARTIAL')
            self.table_amount(pid,key,'추정가격',390160000,9,'별도','미확인');self.table_amount(pid,key,'부가세',39016000,9,'VAT 자체','미확인')
            sheet=self.key(pid,'d98a');self.money(pid,'총공사비(내역서 갑지)',450000000,sheet,5,'미확인','미확인',status='AMOUNT_STATED_COMPONENTS_UNCONFIRMED',note='원문 상수값 보존. 기초금액 429,176,000원과의 차이는 구성 근거 없어 관급비로 확정하지 않음')
            self.fact(pid,'filename_year_check',sheet,[0,3],'내부 사업명/설계일 2025; 파일명 2024',note='첨부명 잔존연도 발견. 공고연도 이동 없음; 다른 개정내용 동일 여부는 미확인')
            n.update(workflow_status='분산 작업지시·전중후 사진·준공 기록 확인',workflow_reason='5개 구 특별시도; 작업지시별 기간 별도 지정; 설치라는 제목과 무관하게 원문 유지보수',conflict_notes='공고 기초금액 429,176,000원 / 내역서 총공사비 450,000,000원. 내역서 도급·VAT·관급 저장값 0으로 구성 불명; 두 값 모두 보존.')
            self.need(pid,'금액 기입 총괄내역 및 관급자재 구매조서','450,000,000원 총공사비와 429,176,000원 기초금액의 범위 대조; 관급/VAT 관계 확인','높음')
        elif i==21:
            key=self.key(pid,'ae98');sheet=self.key(pid,'39ab')
            self.fact(pid,'scope_and_area',key,[3,4]);self.fact(pid,'construction_period',key,6);self.fact(pid,'work_type_explicit',key,12,'전문공사(유지보수공사)')
            self.select(n,key,'기초금액',283308000,[7,8,9,10],'포함','도급자/관급자 설치관급 별도','착공일부터 2025-09-30까지')
            for label,val,idx,vat,gov in [('예정(추정)금액',364860380,[7,8,9],'도급 VAT 포함; 관급분 VAT 분해 미확인','도급자설치관급 포함; 관급자설치관급 별도'),('추정가격(공급가액)',257552728,8,'별도','관급 별도'),('부가가치세',25755272,8,'VAT 자체','해당 없음'),('도급자설치관급자재',81552380,9,'미확인','도급자설치'),('관급자설치관급자재',32619190,9,'미확인','관급자설치')]:self.table_amount(pid,key,label,val,idx,vat,gov)
            self.table_amount(pid,sheet,'총공사비',397479570,[22,23,24],'도급 VAT 포함; 관급분 자체 VAT 명시 없음','관급자·도급자 설치관급 모두 포함',note='원문 내역서 L26 값. 도급액+관급자재대 구성과 공고의 두 관급액을 대조')
            self.table_amount(pid,sheet,'관급자재대 합계',114171570,23,'미확인','관급자+도급자 설치관급 합계')
            n.update(workflow_status='분산 94개소/긴급지시/사진·준공 확인',workflow_reason='설치 제목이나 원문은 유지보수공사. 일회성 분산 정비사업; 연간상시 또는 12개월로 확대하지 않음')
            self.checks['P21_cross_document_components']=(283308000+81552380==364860380 and 81552380+32619190==114171570 and 283308000+114171570==397479570)
        elif i==22:
            key=self.key(pid,'9ffc');n.update(document_exclusive_group='핵심 외(역사 소방 개량)',document_facility_tags='철도역사;소방시설',document_commercial_tier='개별검토(핵심 외)',workflow_status='단일 역사 집중 개량/사진·준공 기록',workflow_reason='문화공원역은 역명; 공원·녹지 또는 도로 교통안전시설 유지관리로 분류하지 않음')
            self.fact(pid,'scope_and_area',key,[22,23]);self.fact(pid,'construction_period',key,10)
            self.select(n,key,'기초금액',37397380,[11,12,13,14,15,16,24,25],'포함','도급자/관급자 설치관급 각각 0원 명시','착공일부터 60일')
            for label,val,idx,vat,gov in [('공사 추정금액',37397380,[24,25],'포함','관급 0원 명시'),('추정가격',33997618,[13,14],'별도','관급 0원 명시'),('부가가치세',3399762,[15,16],'VAT 자체','해당 없음'),('도급자설치 관급자재비',0,25,'해당 없음','원문 0원'),('관급자설치 관급자재비',0,25,'해당 없음','원문 0원')]:self.table_amount(pid,key,label,val,idx,vat,gov)
            self.fact(pid,'period_schedule',self.key(pid,'55f8'),0,'예정 60일; 준비10/비작업21/작업24/마무리5',note='예정공정표를 실제 착공일/실행일정으로 전환하지 않음')
            desc=self.key(pid,'0b5d');self.fact(pid,'photos_completion',desc,189,note='공사내용서의 기록사진/사진첩/준공 제출 요구')
            self.fact(pid,'daily_weekly_progress_records',desc,[199,203],note='공종별 설계·시공·잔여물량·인원장비·예정/실행공정 보고; 분산 도로 유지관리로 분류하지 않음')
        elif i==23:
            key=self.key(pid,'6d9e');n.update(document_exclusive_group='공원녹지',document_facility_tags='가로화단;중앙분리대 수목',document_commercial_tier='개별검토',workflow_status='분산 수목 전정 28일; 상시 지시/기록 범위 미확인',workflow_reason='상록수 335주 전정; 중앙분리대라는 장소만으로 교통시설로 분류하지 않음')
            self.fact(pid,'scope_and_area',key,[4,5]);self.fact(pid,'construction_period',key,6)
            self.select(n,key,'기초금액',51700000,[11,12,21],'포함','관급자/도급자 관급란 -; 0으로 확정 안 함','착공일부터 28일',status='AMOUNT_CONFIRMED_SCOPE_PARTIAL')
            for label,val,ids,vat in [('추정금액',51700000,[9,10,20],'도급 VAT 포함'),('추정가격',47000000,[13,14,22],'별도'),('부가가치세',4700000,[15,16,23],'VAT 자체')]:self.table_amount(pid,key,label,val,ids,vat,'관급란 - 표시')
            self.fact(pid,'government_blank_mark',key,[17,18,19,24,25],'-')
            sheet=self.key(pid,'28f8');self.fact(pid,'quantity_error',sheet,[4,5,6,7,8,14],'C11/C18 #REF!',note='335주 중 일부 수량 셀 오류. 0으로 치환하지 않고 실제 대상수목목록 필요')
            self.need(pid,'시방서/대상 수목목록 및 금액 기입 총괄내역','28일의 작업지시·사진·완료보고 범위, #REF! 수량, 관급 - 표시 의미 확인')

    def scan_supplementary(self,pid):
        for d in self.docs:
            if d['priority_id']!=pid or 'text_cache' not in d:continue
            # Common legal/blank forms were parsed, but not treated as notice-specific workflow evidence.
            if d['document_id'] in ('4378bf2397a9ba00','9ab2bfb827060362'):continue
            key=d['document_key'];blocks=self.bs(key)
            if d.get('actual_format') in ('XLS','XLSX'):
                ids=[i for i,b in enumerate(blocks) if re.search('총공사비|공급가액|도급액|부가가치세|총공사금액',re.sub(r'\s','',b['text']))][:6]
                if ids:self.add(pid,'bill_amount_cells_observed','원문 셀 표시 보존',key,ids,status='CACHED_OR_UNPRICED_BILL',note='공내역/물량내역의 공란·0 캐시·#REF!는 유효 사업비 0을 뜻하지 않음. 수식/외부연결 재계산 없음')
                works=[i for i,b in enumerate(blocks) if re.search('차선|표지판|노면표시|전정|스프링클러|건축|온실',b['text']) and i>0][:3]
                if works:self.fact(pid,'bill_work_items',key,works)
                elif blocks:self.fact(pid,'bill_review_scope',key,[0],note='금액/범위 보조 대조용 내역서; 개별 품목 전체를 시장금액으로 합산하지 않음')
            elif any(s in d['document_name'] for s in ('설명서','시방서','내용서','설계도면')):
                for field,pat in [('work_orders','작업지시를 접수|시공지시 시|감독관 지시에 따라|작업지시별|긴급 시공에 대비'),('photos_completion','컬러 사진을 확보|사진첩을 상시|기록사진을 촬영|동일장소|전경사진|공사사진첩')]:
                    ids=[i for i,b in enumerate(blocks) if re.search(pat,b['text'])][:1]
                    if ids:
                        # PDF pages can be lengthy: retain only the matching sentence and page locator.
                        eid=self.fact(pid,field,key,ids,note='이 공고에 첨부된 시방서의 요구사항; 실제 시스템 사용 또는 도입의사 확인 아님')
                        row=self.e[-1]
                        if len(row['evidence_text'])>1800:
                            text=blocks[ids[0]]['text'];m=re.search(pat,text);row['evidence_text']=redact(text[max(0,m.start()-160):m.end()+650]);row['interpretation']+='; 페이지 중 해당 문구 주변 발췌'
                if not any(e['document_key']==key for e in self.e) and blocks:
                    selected=[j for j,b in enumerate(blocks[:100]) if re.search('적용범위|공사범위|일반사항|공사명|공 사 명',b['text'])][:1] or [0]
                    self.fact(pid,'supporting_document_scope',key,selected,note='관련 범위/기록 조항 발췌 대조; 설계도면 공간적 완전성·전체 기술시방 검증 아님')

    def finish(self):
        # Scanned PDF actually read visually, with no names/signatures exported.
        pid='P20';key=self.key(pid,'4097');d=self.D[key]
        self.e.append(dict(evidence_id=f'E{len(self.e)+1:04d}',priority_id=pid,bid_ntce_no=next(n['bid_ntce_no'] for n in self.inputs if n['priority_id']==pid),bid_ntce_ord='000',field='patent_agreement_scope',value='문자작도방법 특허 사용협약',amount_krw='',amount_name='',amount_period_scope='',verification_status='VISUALLY_READ',vat_scope='해당 없음',government_supplied_scope='해당 없음',document_id=d['document_id'],document_key=key,document_name=d['document_name'],archive_member='',evidence_location='PDF 페이지 1 제1조·제3조 / 페이지 2 협약일',evidence_text='2025년 노후포장도로 노면표시공사(1,2구역) / 2025년 3월 12일',source_local_path=d['source_local_path'],source_sha256=d['sha256'],source_type='DOWNLOADED',interpretation='2페이지 이미지 열람. 해당 특허 부분의 사용료 조항이며 시공노트 X의 근거로 사용하지 않음',rule_version=VERSION))
        # Normalize temporary drafting labels before any export.
        repl={'原文確認':'SOURCE_CONFIRMED','該当なし':'해당 없음','当該発注の予定総額':'해당 발주의 예정총액','中':'보통'}
        for row in self.e+self.needs:
            for k,v in row.items():
                if isinstance(v,str) and v in repl:row[k]=repl[v]
        for n in self.notices:
            es=[e for e in self.e if e['priority_id']==n['priority_id']]
            n['evidence_ids']=';'.join(e['evidence_id'] for e in es)
            n['source_documents_used']=len({e['document_key'] for e in es if e['document_key']})
        groups=collections.defaultdict(list)
        for n in self.notices:
            k=tuple(n[k] for k in ('original_institution','institution_code','institution_type','notice_year','title_business_years','document_exclusive_group','document_commercial_tier','stated_period','selected_reference_amount_name','selected_reference_vat','selected_reference_gov','selected_reference_status'))
            groups[k].append(n)
        sums=[]
        for group in groups.values():
            n=group[0];vals=[int(r['selected_reference_amount']) for r in group if r['selected_reference_amount']!='']
            row={k:n[k] for k in ('original_institution','institution_code','institution_type','notice_year','title_business_years','document_exclusive_group','document_commercial_tier','stated_period','selected_reference_amount_name','selected_reference_vat','selected_reference_gov','selected_reference_status')}
            row.update(notice_count=len(group),amount_sample_n=len(vals),reference_sum_krw=sum(vals) if vals else '',reference_mean_krw=str(sum(vals)/len(vals)) if vals else '',reference_median_krw=statistics.median(vals) if vals else '',
                       amount_unknown_count=len(group)-len(vals),actual_contract_sample_n=0,actual_contract_sum_krw='',actual_start_dates_known=0,
                       amount_basis='원문 예정금액 1개/공고; 단가·다른 총액과 합산 금지',period_caution='기재 기간 표현이 같은 것만 그룹화; 실제 착공일/운영월수 동일성 미확인',
                       priority_ids=';'.join(r['priority_id'] for r in group),evidence_ids=';'.join(r['selected_reference_evidence_id'] for r in group if r['selected_reference_evidence_id']),rule_version=VERSION)
            sums.append(row)
        register=[]
        used={e['document_key'] for e in self.e if e['document_key']}
        for d in self.docs:
            row={k:d.get(k,'') for k in ('priority_id','bid_ntce_no','bid_ntce_ord','document_id','document_key','document_name','member_path','source_local_path','source_kind','actual_format','read_status','bytes','sha256')}
            row['evidence_review_status']='근거 항목 발췌 대조' if d['document_key'] in used else '압축 용기(문서수 제외)' if d['read_status']=='ARCHIVE_EXPANDED' else 'DWG 미판독; 동봉 PDF로 범위 확인' if d.get('actual_format')=='UNKNOWN' else '일반조건/서식 텍스트 추출만; 개별 과업 근거로 사용 안 함'
            if d['document_key']==key:row['read_status']='VISUALLY_READ_PDF_2_PAGES'
            row['evidence_rows']=sum(e['document_key']==d['document_key'] for e in self.e)
            register.append(row)
        audit=json.loads((OUT/'source_inventory.json').read_text(encoding='utf-8'))['file_audit']
        self.checks.update(downloaded_files_exist=sum(a.get('file_exists',False) for a in audit)==74,
            downloaded_size_hash_ledger_match=all(a.get('size_matches',True) and a.get('sha_matches',True) and a['ledger_agrees'] for a in audit),
            received_byte_total=sum(a.get('bytes',0) for a in audit)==37853466,
            notice_keys_unique=len({(n['bid_ntce_no'],n['bid_ntce_ord']) for n in self.notices})==23,
            notice_rows_23=len(self.notices)==23,every_notice_has_content=all(n['source_documents_used']>0 for n in self.notices),
            one_primary_amount_per_notice=sum(s['notice_count'] for s in sums)==23,
            grouped_amounts_reconcile=sum(s['reference_sum_krw'] or 0 for s in sums)==sum(n['selected_reference_amount'] or 0 for n in self.notices),
            no_unit_amount_as_primary=all('단가' not in n['selected_reference_amount_name'] for n in self.notices),
            no_invented_contract_start_rate=all(n['actual_start_date']==n['actual_contract_amount']==n['actual_execution_amount']==n['fee_basis_amount']==n['fee_X']=='' for n in self.notices),
            each_selected_amount_has_evidence=all(n['selected_reference_evidence_id'] for n in self.notices if n['selected_reference_amount']!=''),
            each_evidence_has_location_or_unknown=all(e['evidence_location'] or not e['document_key'] for e in self.e),
            multi_core_group_one_row=all(next(n for n in self.notices if n['priority_id']==pid)['document_exclusive_group']=='복수핵심 통합(교통시설+노면표시)' for pid in ('P13','P14','P15')),
            missing_primary_amount_not_zero=all(n['selected_reference_amount']=='' for n in self.notices if n['priority_id'] in ('P03','P13')),
            network_calls_zero=True)
        assert all(self.checks.values()),self.checks
        writecsv('priority_notice_review.csv',self.notices);writecsv('priority_document_evidence.csv',self.e);writecsv('institution_verified_amount_summary.csv',sums);writecsv('additional_documents_needed.csv',self.needs);writecsv('document_review_register.csv',register)
        (OUT/'validation_results.json').write_text(json.dumps(self.checks,ensure_ascii=False,indent=2),encoding='utf-8')
        counts={'reviewed_documents_with_evidence':len(used),'reviewed_notices':len(self.notices),'primary_expected_amount_confirmed':sum(n['selected_reference_amount']!='' for n in self.notices),
                'explicit_annual_total_notices':sum(n['annual_total_explicit']!='' for n in self.notices),'year_end_annual_unit_total_notices':sum('연간단가 해당 발주 총액' in n['annual_amount_status'] for n in self.notices),
                'partial_annual_unit_180day_notice':sum('180일 분할발주' in n['annual_amount_status'] for n in self.notices),'vat_and_government_scope_confirmed_notices':sum(n['vat_and_government_scope_confirmed'] for n in self.notices),
                'text_extracted_leaf_documents':sum(d['read_status']=='READ_TEXT' for d in self.docs),'scan_pdf_visually_read':1,'dwg_unread':1,'downloaded_files':74,'user_provided_files':1,'archive_containers':3,'archive_members':sum(bool(d['member_path']) for d in self.docs),
                'actual_contract_confirmed':0,'actual_start_confirmed':0,'X':'미정'}
        self.report(counts,sums)
        inputs=[BASE/'attachment_fetch_results.csv',BASE/'attachment_metadata.csv',BASE/'priority_notice_review.csv',ROOT/'.local/sigongnote_stage2_documents/fetch_ledger.jsonl']
        manifest={'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'counts':counts,'rule_version':VERSION,'network_calls':0,'input_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},'files':{}}
        for p in sorted(OUT.iterdir()):
            if p.is_file() and p.name!='output_manifest.json':
                rows=len(readcsv(p)) if p.suffix=='.csv' else None
                manifest['files'][p.name]={'bytes':p.stat().st_size,'data_rows':rows}
        (OUT/'output_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'counts':counts,'evidence_rows':len(self.e),'institution_rows':len(sums),'checks_passed':len(self.checks)},ensure_ascii=False))

    def report(self,c,sums):
        incheon=[n for n in self.notices if int(n['priority_id'][1:])<=12 and n['selected_reference_amount']!='']
        confirmed=sum(n['selected_reference_amount'] for n in incheon)
        lines=['# 시공노트 우선검토 23개 공고 원문 검토',
          f'실제 항목 근거를 읽고 대조한 문서 **{c["reviewed_documents_with_evidence"]}개**, 검토 공고 **23개**. 연간 총도급예정액이 명시된 공고 **{c["explicit_annual_total_notices"]}개**, 그 외 당해 연도 말까지 연간단가 공고의 해당 발주 예정총액 확인 **{c["year_end_annual_unit_total_notices"]}개**. 연간단가 명칭이나 1차 A구역·180일인 공고 **1개**는 연간 총액에서 분리했다. 선택한 도급범위 예정금액의 VAT·관급 포함/별도 범위까지 확인한 공고 **{c["vat_and_government_scope_confirmed_notices"]}개**. 관급 자체의 세액까지 모두 검증했다는 의미는 아니다.',
          f'사업규모 검토에 쓸 원문 예정총액은 **{c["primary_expected_amount_confirmed"]}개 공고**에서 확인했다(범위 미확인 포함). 실제 계약액과 실제 착공일 확인은 각각 **0개**. 모든 기간은 공고/설계서의 예정 표현이며 X와 최종 과금 기준은 미정이다.',
          '## 완료와 판독 범위',
          '최신 attachment_fetch_results.csv와 append-only fetch_ledger.jsonl의 79개 최종 상태를 대조했다. DOWNLOADED 74개 모두 존재하며 크기·SHA-256·ledger 상태가 일치했다. 실제 합계 37,853,466바이트. FAILED 5개는 과거 이력을 유지했고, 그중 P17 공고문은 사용자가 제공한 HWP(SHA-256 8a8a1a7f5fcae6214f7e7282e510da30af960d40e54ebf7125f58d3d60442800)로 보완했다. 이번 네트워크 호출과 다운로드 재시도는 0회다.',
          f'74개 다운로드 파일 + 사용자 원문 1개 중 ZIP 3개는 용기이며 문서 수에서 제외했다. ZIP 내부 16개 개별 파일까지 확인했다. 본문 텍스트 추출 {c["text_extracted_leaf_documents"]}개, 스캔 PDF 1개(2쪽) 이미지 열람, DWG 1개 미판독이다. 일반조건·법령·서식은 텍스트 추출만 하고 개별 공고 과업이나 사업금액 근거로 격상하지 않았다. 첫머리의 문서 수는 evidence.csv에 실제 발췌 근거가 있는 문서이며 문서 전체 기술·법률 검토 완료 건수가 아니다. 문서별 범위는 document_review_register.csv에 있다.',
          '확장자를 신뢰하지 않고 파일 서명을 사용했다. HWPX 이름인데 HWP 바이너리인 파일, .hwp 이름인 HWPML XML도 읽었다. XLS/XLSX는 저장된 값/수식결과를 읽었으며 매크로·스크립트·수식 재계산·외부 연결은 실행하지 않았다. HWP 인용 위치는 Section/본문 문단, HWPX는 XML section/문단, PDF는 실제 페이지, 표 계산서는 시트/행/셀이다. 실제 페이지를 모르는 HWP에 페이지 번호를 만들지 않았다.',
          '## 핵심 발견',
          '1. **포천 단가와 연간 금액 분리:** P16 단가 추정가격 128,381원 / 기초금액 141,220원 / 연간 총도급예정액 40,000,000원. P17 단가 추정가격 137,101원 / 기초금액 150,811원 / 연간 총도급예정액 99,900,000원. 각 단가 VAT는 확인됐으나 연간 예정총액 VAT·관급 범위는 미확인이다. 두 예정총액은 보존했고 0원으로 만들지 않았다. 종료일도 12월15일/19일로 달라 기관 합계에서 한 기간으로 합치지 않았다.',
          f'2. **인천 종합건설본부 12건:** 설계설명서에 도로 유지보수로 훼손·제거된 노면표시 원상복구와 작업지시 후 시공, 전·중·후 사진·준공서류 요구가 확인됐다. 11건 공고문의 도급액(기초금액)·VAT·관급 구성이 확인되며, 같은 기재 기간(착공일부터 2025-12-20)의 도급 예정액 합계는 **{confirmed:,}원**이다. 실제 착공일·관리월수는 같다고 보장하지 않는다. 현장 구명을 수요기관으로 옮기지 않았다. 12번째 P03은 원문 금액 미확인으로 합계에서 분리했다. 남부/북부권과 개별 구 작업구역이 겹치므로 실제 작업지시 중복 여부는 공고 차원이 아닌 후속 계약·현장기록에서 확인해야 한다.',
          '3. **P03 금액 불일치:** 공내역서 갑지 B17/B19/G18에 4,400,000원, K18에 4,000,000원, K20에 400,000원이 저장되어 있고 주요 도색/관급 금액은 0 캐시다. 문서에 관측된 값으로 보존했지만 492,246,000원 공고 예산을 대체하지 않았다. 153,678,000원 잔차를 관급이라고 확정할 근거는 여전히 없다. 금액이 기입된 원 공고/총괄표가 필요하다.',
          '4. **남동구 복수 핵심:** P13의 표지판·노면표시·부대포장 범위를 시방서/내역서로 확인했다. P14·P15에도 표지와 도색이 함께 있다. 세 공고는 복수핵심 통합 그룹으로 각각 1회 계상한다. P13은 공고문 실패와 XLS 금액 미확인으로 예정총액을 비워 두되 영업 검토에서는 제외하지 않는다. P13 공고연도 2024와 제목연도 2025를 유지했다. P14의 연간단가 1차(A구역)는 **착공일부터 180일**이라 연간 전체 한도나 1년 이용권이 아니다.',
          '5. **설치 표현:** P20 북부도로사업소는 공고문에 유지보수공사로 명시하고 5개 구 특별시도에서 지시별 기간과 전·중·후 사진 제출을 요구한다. 기초금액 429,176,000원과 내역서 갑지 총공사비 450,000,000원을 각각 보존했다. 20,824,000원 차액을 관급으로 추정하지 않았다. 파일명 2024와 달리 내역서 내부 사업명/설계월은 2025년 3월이다. P21은 94개 어린이보호구역의 유지보수공사로 명시되어 있으며 분산 설치를 이유로 제외하지 않았다.',
          '6. **서로 다른 총액 명칭:** P21 기초금액 283,308,000원, 예정(추정)금액 364,860,380원(도급자설치관급 포함), 내역서 총공사비 397,479,570원(관급자설치도 포함)은 각각 다른 범위다. 하나의 공고 금액을 세 번 더하지 않는다. P18 공사예정금액 32,863,436,000원은 도급자설치관급 포함·관급자설치관급 별도이고, P19도 같은 범위의 추정금액을 별도로 명시한다. 별도 금액을 임의로 더해 새 전체 사업비를 만들지 않았다.',
          '7. **핵심 유지관리와 분리:** P18 전시온실 건립은 810일 계속비 공사, P19 문화체육센터는 900일 장기계속 신설 건축공사다. 부속 조경을 핵심 유지관리로 넣지 않았다. P22 문화공원역은 단일 역사 소방 개량(60일)으로 공원녹지가 아니다. P23 중앙분리대 등 수목 전정은 공원녹지이나 28일의 단기 사업이다. #REF! 수량 셀은 0으로 치환하지 않았다.',
          '## 기관별 집계와 이용료 검토',
          'institution_verified_amount_summary.csv는 공고게시연도·수요기관·배타적 분야·상업 검토 상태·원문 기간 표현·선택한 금액 명칭·VAT·관급 범위·검증 상태가 같은 것끼리만 묶는다. 공고당 대표 참고액 하나만 사용하며 단가·다른 범위의 총액을 추가 합산하지 않는다. VAT/관급 범위가 미확인인 예정총액은 AMOUNT_CONFIRMED_SCOPE_PARTIAL 그룹에서 유지하고, 적합한 총액 자체가 미확인인 P03/P13은 금액 공란인 별도 행으로 남겼다. 건축 별도사업모델·역사 소방 핵심 외 그룹도 핵심 유지관리와 분리했다. 전체 이질적 합계는 의사결정용으로 제시하지 않는다.',
          '가격 가설 검토 시 도급분 관리가 중심이면 원문 도급예정액(또는 같은 범위의 공급가액)을 기준 후보로 비교할 수 있다. 관급 발주·자재 이력까지 관리 범위에 포함한다면 관급 포함 총공사비 표기를 별도 후보로 검토해야 한다. 두 범위는 같은 금액이 아니며 구매기관의 서비스 범위 합의가 필요하다. X·할인·상한·도입률·예상매출은 정하지 않았다. 특허 사용협약의 기술사용료 조항은 시공노트 이용료의 근거로 사용하지 않았다.',
          '23개는 목적 선정 검토 표본이다. 확인된 금액을 전국 공공조달 시장이나 확정 SaaS 시장규모로 확대하지 않는다. 실제 계약/집행·정산 자료는 없으며 관련 금액 필드는 공란이다.',
          '## 실패·미검증 및 필요한 추가 자료',
          '기존 다운로드 실패 5건은 보존했다. P17은 사용자 공고문으로, P23은 내려받은 동명 HWP로 보완되므로 같은 공고문을 다시 받을 필요가 없다. P16은 시방서가 빠졌지만 공고문/설계설명서에서 작업과 연간 예정액을 확인했다. P03·P13은 금액 확정에 필요한 공고문/금액 기입 총괄표가 우선이다. P16/P17 연간총액의 VAT·관급, P20 총공사비와 기초금액의 구성, P14 차수/관리기간, P23 시방서와 수목 수량 오류 확인을 이어갈 필요가 있다. 구체적인 목록은 additional_documents_needed.csv에 있으며 어떤 자료도 새로 수집하지 않았다.',
          '순수 DWG 1개는 판독하지 못했지만 동봉 6쪽 PDF의 텍스트와 공사내용서를 대조했다. PDF 도면의 치수/배관 배치까지 검증한 것은 아니다. 초기 HWP 추출 5건의 UTF-16 surrogate 저장 오류는 파서를 보완해 해당 파일만 다시 읽었다. 스캔된 특허 협약은 자동 텍스트 추출 실패 후 2쪽 이미지로 읽었다. 일부 XLS 수식 문자열은 미확인 표기로 남겼고 빈 공내역서의 0 캐시를 유효 사업비로 쓰지 않았다.',
          '## 실행과 검증',
          '재현: python tools/sigongnote_verified_extract.py (로컬 원문 추출만), 이어서 python tools/sigongnote_verified_review.py (캐시 기반 검토표). 스캔 PDF 2쪽은 별도 시각 판독 기록을 근거표에 남겼다. 최초 추출 뒤 발견한 HWPML·문자 저장 처리를 코드에 반영했다. 데이터베이스 전체 재분석, 과거 CSV 수정, 네트워크 진단과 다운로드 재실행은 하지 않았다.',
          f'검사 {len(self.checks)}개 모두 통과. 파일 존재/해시/크기/최신 원장 대조, 23개 복합키 고유성, 원문 금액 인용 일치, 금액 구성 산술, 기관별 건수·금액 대조, 통합분야 1회 계상, 단가의 대표총액 배제, 미확인의 공란 유지, 계약액·실제 시작일·요율 미추정 검사를 수행했다. 기록·산술 검사 통과가 공고의 최종 법적 상태나 수집 완전성을 보증하지 않는다.',
          '필수 산출물은 문서 요약, 23행 공고 검토표, 항목별 근거표, 기관별 참고액 표, 추가 필요문서 목록이다. 파일별 데이터 행 수·바이트는 output_manifest.json에 기록한다. 원문과 기존 CSV·실패 원장은 보존한다.'
        ]
        (OUT/'document_review_summary.md').write_text('\n\n'.join(lines)+'\n',encoding='utf-8')

def main():
    sys.stdout.reconfigure(encoding='utf-8');OUT.mkdir(parents=True,exist_ok=True)
    r=Review();r.run();r.finish()
if __name__=='__main__':main()
