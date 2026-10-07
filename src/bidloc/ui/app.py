"""Search, drill-down and analysis screens using native Streamlit controls."""
from dataclasses import asdict, replace
from datetime import date
from html import escape
import json
from pathlib import Path
import sqlite3

import streamlit as st

from bidloc.analysis import location, unitprice
from bidloc.config import ConfigError, load_settings
from bidloc.exports import safe_url
from bidloc.query_service import (SearchFilters, csv_bytes, detail, export_metadata,
                                 load_snapshot, notice_export_rows, review_reasons, search)
from bidloc.ui.cache import fingerprint, read_snapshot

MENU = [('공고 검색', ':material/manage_search:'), ('지역 비교', ':material/bar_chart:'),
        ('단가계약 분석', ':material/description:'), ('검토 대기', ':material/schedule:'),
        ('수집·품질', ':material/database:'), ('API 연동', ':material/link:')]
PROFILE = ['미확정', '기존 법인 면허 추가', '신규 법인', '본점 이전']


@st.cache_data(show_spinner=False, max_entries=2)
def cached_snapshot(database, data_mode, version):
    return read_snapshot(database, data_mode)


def money(value):
    return '미수집' if value is None else f'{value:,.0f}'


def html_text(value):
    return escape(str(value if value is not None else '미수집'))


def navigate(page):
    st.session_state.page = page
    st.session_state.selected = None
    st.session_state.result_page = 0


def open_notice(no, ord_):
    st.session_state.selected = (no, ord_)


def sidebar():
    with st.sidebar:
        st.html('<div class="brand">면허 입지 분석기</div>')
        for i, (name, icon) in enumerate(MENU):
            if i == 4:
                st.divider()
            st.button(name, icon=icon, key='nav_'+name, width='stretch',
                      type='primary' if st.session_state.page == name else 'secondary',
                      on_click=navigate, args=(name,))
        st.space('large')
        st.divider()
        st.selectbox('회사 프로필', PROFILE, key='profile')
        st.caption('선택값은 분석 가정입니다.\n\n실적·주력분야·본점 기준일은 별도 확인합니다.')


def reset_filters(defaults):
    for k, v in defaults.items():
        st.session_state[k] = v
    st.session_state.applied = None
    st.session_state.result_page = 0


def filters_panel(records, meta):
    saved = meta.get('filters', {})
    end = date.fromisoformat(saved.get('end') or date.today().isoformat())
    begin = date.fromisoformat(saved.get('begin') or end.replace(month=1, day=1).isoformat())
    begin = max(begin, end.replace(month=1, day=1))
    defaults = {'q':'', 'dates':(begin,end), 'hq':'전체', 'scope':'4992',
                'amount':'전체', 'contract':'전체', 'status':'전체', 'sort':'공고일 최신순'}
    for k,v in defaults.items():
        st.session_state.setdefault(k,v)
    regions = sorted({r for d in records for r in d['regions'] if len(r.split()) <= 3})
    contracts = sorted({d['cntrct_cncls_mthd_nm'] for d in records if d.get('cntrct_cncls_mthd_nm')})
    with st.form('search_filters', border=False):
        a,b = st.columns([9,1], vertical_alignment='bottom')
        a.text_input('검색어', placeholder='공고명 · 공고번호 · 발주기관 검색', key='q', label_visibility='collapsed')
        submitted = b.form_submit_button('검색', type='primary', width='stretch')
        a,b,c = st.columns([1.25,1,1])
        a.date_input('기간', key='dates', format='YYYY.MM.DD')
        b.selectbox('업종 / 조회 범위', ['4992','분석 후보 전체'], key='scope')
        c.selectbox('본점 소재지 (가정)', ['전체',*regions], key='hq')
        a,b,c,d = st.columns([1.25,1,1,.65], vertical_alignment='bottom')
        a.selectbox('추정가격', ['전체','1억 원 미만','1억~3억 원','3억 원 이상'], key='amount')
        b.selectbox('계약방식', ['전체',*contracts], key='contract')
        c.selectbox('상태', ['전체','마감 전','마감','허용지역 미수집','자료·차수 검토'], key='status')
        d.form_submit_button('초기화', on_click=reset_filters, args=(defaults,), width='stretch')
    if submitted or not st.session_state.get('applied'):
        dates = st.session_state.dates
        if len(dates) != 2:
            st.warning('시작일과 종료일을 모두 선택하세요.')
        else:
            amounts = {'전체':(None,None),'1억 원 미만':(None,99_999_999),
                       '1억~3억 원':(100_000_000,299_999_999),'3억 원 이상':(300_000_000,None)}
            low, high = amounts[st.session_state.amount]
            st.session_state.applied = SearchFilters(query=st.session_state.q,
                begin=dates[0].isoformat(), end=dates[1].isoformat(), scope=st.session_state.scope,
                hq='' if st.session_state.hq=='전체' else st.session_state.hq,
                contract='' if st.session_state.contract=='전체' else st.session_state.contract,
                min_amount=low,max_amount=high,status=st.session_state.status)
            st.session_state.result_page = 0
    filters = st.session_state.get('applied') or SearchFilters()
    return replace(filters, profile=st.session_state.profile, sort=st.session_state.get('sort','공고일 최신순'))


def downloads(records, meta, filters):
    info = export_metadata(meta, filters)
    prefix = 'synthetic-' if meta.get('data_mode')=='synthetic' else ''
    csv_col,json_col=st.columns([1.15,1])
    csv_col.download_button('CSV 다운로드', csv_bytes(notice_export_rows(records),info),
                       prefix+'notices.csv','text/csv',icon=':material/download:',key='csv_notices')
    with json_col.popover('JSON / 조건'):
        st.json(asdict(filters),expanded=True)
        st.download_button('JSON 다운로드',json.dumps({'metadata':info,'notices':records},ensure_ascii=False,default=str),
                           prefix+'notices.json','application/json',key='json_notices')


def notice_list(rows, meta, filters):
    a,b,c = st.columns([3.5,3,1.5],vertical_alignment='center')
    a.subheader(f'검색 결과 · {len(rows):,}건')
    with b:
        downloads(rows,meta,filters)
    c.selectbox('정렬',['공고일 최신순','마감 빠른순','추정가격 높은순'],key='sort',label_visibility='collapsed')
    if not rows:
        st.info('조건에 맞는 공고가 없습니다. 기간·검색어·허용지역을 조정해 보세요.')
        return
    widths = [4.2,2.1,1.45,1.4,1.5,.85]
    with st.container(key='table_header'):
        columns = st.columns(widths)
        for col,label in zip(columns,['공고명 / 번호','허용지역','추정가격 (원)','마감','검토 상태','']):
            col.html(f'<div class="table-head">{label or "&nbsp;"}</div>')
    page_size = 10
    pages = (len(rows)+page_size-1)//page_size
    page = min(st.session_state.get('result_page',0),pages-1)
    st.session_state.result_page = page
    for item in rows[page*page_size:(page+1)*page_size]:
        with st.container(key='notice_row_'+item['key']):
            a,b,c,d,e,f = st.columns(widths,vertical_alignment='center')
            a.html(f'<div class="notice-title">{html_text(item["bid_ntce_nm"])}</div>'
                   f'<div class="muted">{html_text(item["bid_ntce_no"])} / {html_text(item["bid_ntce_ord"])} · {html_text(item.get("ntce_instt_nm"))}</div>')
            b.html(f'<div class="cell">{html_text(" · ".join(item["regions"]) or "미수집")}</div>')
            c.html(f'<div class="cell money">{money(item.get("presmpt_prce"))}</div>')
            d.html(f'<div class="cell">{html_text(item.get("deadline") or "미수집")}</div>')
            e.html(f'<span class="status">{html_text(review_reasons(item)[0])}</span>')
            f.button('상세 →',key='open_'+item['key'],on_click=open_notice,args=(item['bid_ntce_no'],item['bid_ntce_ord']),type='tertiary')
    st.caption('허용지역과 현장주소는 다릅니다. 미수집 값은 0으로 집계하지 않습니다.')
    a,b,c,d = st.columns([6,1,1,1])
    if b.button('이전',disabled=page==0,width='stretch'):
        st.session_state.result_page-=1
        st.rerun()
    c.markdown(f'**{page+1} / {pages}**')
    if d.button('다음',disabled=page+1>=pages,width='stretch'):
        st.session_state.result_page+=1
        st.rerun()


def condition_table(row):
    region_state = '차수 확인' if row.get('region_revision_mismatch') else ('자료 확인' if row['regions'] else 'UNKNOWN')
    data = [ ('허용지역',' · '.join(row['regions']) or '미수집',region_state),
             ('통합업종',' · '.join(row['license_codes']) or '미수집','자료 확인' if row['license_codes'] else 'UNKNOWN'),
             ('주력분야','원문 확인 필요','UNKNOWN'),('복수면허 조건','AND / OR 미확인','UNKNOWN'),
             ('본점 기준일','미확인','UNKNOWN'),('실적·적격심사','별도 검토','UNKNOWN')]
    content='<table class="detail-grid"><thead><tr><th>항목</th><th>확인 내용</th><th>판정</th></tr></thead><tbody>'
    for name,value,status in data:
        content+=f'<tr><td>{html_text(name)}</td><td>{html_text(value)}</td><td><span class="status">{html_text(status)}</span></td></tr>'
    st.html(content+'</tbody></table>')


def notice_detail(row, meta):
    st.caption('공고 검색 / 공고 상세')
    a,b=st.columns([5,1],vertical_alignment='center')
    a.title(row['bid_ntce_nm'])
    url=safe_url(row.get('url'))
    if url:
        b.link_button('원문 열기 ↗',url)
    else:
        b.button('원문 URL 미수집',disabled=True)
    st.caption(f'{row["bid_ntce_no"]} / {row["bid_ntce_ord"]} · {row.get("ntce_instt_nm") or "기관 미수집"}')
    st.warning('검토 필요 · 지역·면허만으로 참가자격을 확정할 수 없습니다.')
    basic,conditions,history,sources=st.tabs(['기본정보','참가조건','개찰·변경 이력','출처'],default='참가조건')
    with basic:
        st.write({'공고일':row.get('bid_ntce_dt'),'마감':row.get('deadline'),'계약방식':row.get('cntrct_cncls_mthd_nm'),
                  '허용지역':row['regions'],'공사현장':row.get('cnstrtsite_rgn_nm'),'분류':row.get('relevance')})
    with conditions:
        a,b=st.columns([2.2,1],gap='large')
        with a:
            st.subheader('조건별 확인')
            condition_table(row)
            st.space('small')
            st.subheader('개찰·재입찰 이력')
            if row['openings']:
                st.caption(f'정확한 공고 차수에 연결된 개찰단위 {len(row["openings"])}개 · 상세는 개찰·변경 이력 탭')
            else:
                st.info('개찰 자료 미수집 · 미개찰·조회 실패·참여수 0은 구분합니다.')
        with b:
            st.subheader('금액과 출처')
            for label,value in [('추정가격',row.get('presmpt_prce')),('배정예산',row.get('bdgt_amt'))]:
                st.markdown(f'{label}　 **{money(value)}'+(' 원**' if value is not None else '**'))
            st.caption('기초금액·낙찰금액: 이 조회에는 미연결')
            st.divider()
            st.markdown('**자료 시점**')
            st.caption(row.get('last_seen_utc') or meta.get('last_response_at_utc') or '미수집')
            st.markdown('**근거 연결**')
            st.caption('공고번호 + 차수\n\n분할입찰 + 재입찰 회차')
            with st.popover('응답 근거 보기'):
                st.json({'공고응답ID':row.get('last_response_id'),'지역응답ID':row.get('region_sources'),
                         '지역자료차수':row.get('region_ord'),'면허응답ID':[x.get('response_id') for x in row['licenses']]})
    with history:
        if row['openings']:
            st.dataframe(row['openings'],hide_index=True,width='stretch')
            st.caption('참가업체수는 최초 개찰단위의 명부 전체 수이며 유효 경쟁자 수와 다릅니다. 최종 낙찰 확정 자료가 아닙니다.')
        else:
            st.info('연결된 개찰 자료가 없습니다.')
        st.caption('검색은 최신 공고 기준입니다. 과거 모든 정정 원문의 열람은 후속 기능입니다.')
    with sources:
        st.json({'snapshot_id':meta.get('snapshot_id'),'notice_response_id':row.get('last_response_id'),
                 'region_response_ids':row.get('region_sources'),'license_rows':row.get('licenses'),
                 'quality':review_reasons(row)},expanded=True)
    st.button('← 검색 결과로 돌아가기',on_click=lambda:st.session_state.update(selected=None))


def api_page():
    st.title('API 연동')
    st.html('<div class="subtitle">다른 앱에서도 같은 조건과 같은 근거로 조회합니다.</div>')
    st.warning('설계 단계 · API 서버는 아직 연결되지 않았습니다.')
    intro,endpoints,logs=st.tabs(['시작하기','엔드포인트','호출 기록'])
    paths=[('/api/v1/notices','공고 검색 · 기간/업종/지역 필터'),('/api/v1/notices/{no}/{ord}','공고 상세 · 버전과 원문 근거'),
           ('/api/v1/analytics/location','같은 조건의 지역 비교'),('/api/v1/collection/status','수집 범위와 품질 상태')]
    with intro:
        a,b=st.columns([1.1,1],gap='large')
        with a:
            st.subheader('조회 API')
            st.caption('후속 HTTP API의 제안 계약입니다.')
            for path,label in paths:
                st.html(f'<div class="endpoint"><span class="verb">GET</span><code>{html_text(path)}</code><div class="muted">{label}</div></div>')
            st.space('medium')
            st.subheader('앱 연결')
            st.write('같은 필터 · 같은 데이터 시점 · 같은 집계 기준')
            st.info('외부 연결 시 앱 전용 인증 적용\n\n나라장터 인증키는 서버에서만 보관')
            st.button('연결 테스트',disabled=True,help='API 서버 구현 후 사용할 수 있습니다.')
        with b:
            with st.container(border=True):
                st.subheader('요청 예시')
                st.code('GET /api/v1/notices?q=방수&license=4992&limit=20',language='http',wrap_lines=True)
                st.subheader('응답 예시 · 합성 데이터')
                st.json({'items':[],'next_cursor':None,'meta':{'data_mode':'synthetic','quality':'NOT_COLLECTED'}})
    with endpoints:
        st.table([{'메서드':'GET','경로':p,'내용':d,'상태':'미구현'} for p,d in paths])
    with logs:
        st.info('API 서버 미구현 · 호출 기록이 없습니다.')


def analysis_page(records, meta, filters):
    # Geographic comparison must not inherit one candidate HQ filter.
    cohort=search(records,replace(filters,hq='',scope='4992'))
    st.caption(f'동일 조건의 고유 공고 {len(cohort):,}건 · 지역별 중복을 합산하지 않습니다.')
    if st.session_state.page=='지역 비교':
        result=location(cohort)
        st.caption(f'집계 기준: {result["basis"]} · 허용지역 연결률: '+(f'{result["region_coverage"]:.1%}' if result['region_coverage'] is not None else '미수집'))
        rows=result['rows']
        choices=st.multiselect('비교할 지역 (최대 3개)',[r['region'] for r in rows],max_selections=3)
        shown=[r for r in rows if not choices or r['region'] in choices]
        labels={'region':'지역','local_notices':'세부지역 공고','province_notices':'시·도 공통 공고','total_notices':'고유 공고',
                'local_competition_median':'세부지역 최초 참여수 중앙값','local_competition_n':'세부지역 참여수 표본',
                'province_competition_median':'시·도 공통 최초 참여수 중앙값','province_competition_n':'시·도 공통 참여수 표본',
                'opening_link_rate':'개찰 연결률','estimated_price_median':'추정가격 중앙값 (원)'}
        st.dataframe([{v:r.get(k) for k,v in labels.items()} for r in shown],hide_index=True,width='stretch')
        st.caption('세부지역과 시·도 공통 공고의 참여수 표본은 별도입니다. 최초 명부 전체 수이며 유효 경쟁자 수가 아닙니다. 작은 표본·낮은 연결률은 자료 부족으로 해석하세요.')
        st.download_button('지역 비교 CSV',csv_bytes(shown,export_metadata(meta,replace(filters,hq='',scope='4992'))),'location.csv','text/csv')
        if shown:
            selected=st.selectbox('근거 공고를 볼 지역',[r['region'] for r in shown])
            keys=set(next(r['notice_keys'] for r in shown if r['region']==selected))
            with st.expander('근거 공고 보기'):
                notice_list([r for r in cohort if r['key'] in keys],meta,replace(filters,hq=selected,scope='4992'))
    else:
        # Unit-price candidate analysis intentionally includes other license candidates.
        candidates=search(records,replace(filters,scope='분석 후보 전체'))
        result=unitprice(candidates,filters.begin,filters.end)
        st.caption('단가 공고는 제목·기관 코드로 분류합니다. 4992 외 후보도 포함하며 금액은 배정예산 기준입니다.')
        rows=[{'분류':r['category'],'사업연도':r['year'],'공고 수':r['notices'],'기관 수':r['municipalities'],
               '공고 배정예산 중앙값 (원)':r['notice_budget']['median'],'예산 표본':r['notice_budget']['n'],
               '예산 미수집':r['missing_budget'],'부분연도':r['partial_year']} for r in result['rows']]
        st.dataframe(rows,hide_index=True,width='stretch')
        st.download_button('단가계약 CSV',csv_bytes(rows,export_metadata(meta,replace(filters,scope='분석 후보 전체'))),'unitprice.csv','text/csv')
        with st.expander('근거 공고 보기'):
            notice_list(result['details'],meta,replace(filters,scope='분석 후보 전체'))


def quality_page(meta):
    st.title('수집·품질')
    st.html('<div class="subtitle">저장된 수집 범위와 확인이 필요한 자료를 살펴봅니다.</div>')
    st.write('수집 상태: **'+meta.get('collection_status','미수집')+'**')
    st.caption('집계는 마지막 조회 시점의 스냅샷입니다. 이 화면에서 수집을 실행하지 않습니다.')
    if meta.get('partitions'):
        st.dataframe(meta['partitions'],hide_index=True,width='stretch')
    else:
        st.info('수집 작업 미등록 또는 미수집')
    st.subheader('품질 검토')
    names={'license_field_shift':'면허 필드 밀림 의심 행','amount_out_of_range':'금액 범위 초과 행','conflicts':'동일 키 내용 충돌'}
    st.table([{'항목':names.get(k,k),'관측 건수':v} for k,v in meta.get('quality',{}).items()])
    st.caption('위 품질 건수는 전체 저장소 기준입니다. 검색 결과의 분모와 다릅니다.')
    st.subheader('판독 기준')
    for note in meta.get('notes',[]):
        st.caption('• '+note)


def main():
    st.set_page_config(page_title='면허 입지 분석기',page_icon='📋',layout='wide')
    st.html('<style>'+Path(__file__).with_name('style.css').read_text(encoding='utf-8')+'</style>')
    st.session_state.setdefault('page','공고 검색')
    st.session_state.setdefault('selected',None)
    st.session_state.setdefault('profile','미확정')
    sidebar()
    if st.session_state.page=='API 연동':
        api_page()
        return
    try:
        settings=load_settings()
        db=settings.database_path
        if 'snapshot_version' not in st.session_state:
            st.session_state.snapshot_version=fingerprint(str(db),settings.data_mode)
        left,right=st.columns([7,1.4])
        if right.button('조회 새로고침',icon=':material/refresh:',width='stretch'):
            cached_snapshot.clear()
            st.session_state.snapshot_version=fingerprint(str(db),settings.data_mode)
            st.session_state.selected=None
        with st.spinner('저장된 DB를 읽고 있습니다. 첫 조회는 시간이 걸릴 수 있습니다.'):
            records,meta=cached_snapshot(str(db),settings.data_mode,st.session_state.snapshot_version)
    except (ConfigError,sqlite3.Error,OSError):
        st.error('DB를 읽을 수 없습니다. 저장 경로와 마이그레이션 상태를 확인한 뒤 다시 조회하세요.')
        st.stop()
    if meta.get('data_mode')=='synthetic':
        st.warning('합성 데이터 모드 · 실제 시장 데이터가 아닙니다.')
    if st.session_state.selected:
        selected=detail(records,*st.session_state.selected)
        if selected:
            notice_detail(selected,meta)
        else:
            st.warning('현재 스냅샷에서 공고를 찾을 수 없습니다.')
            st.button('검색으로 돌아가기',on_click=lambda:st.session_state.update(selected=None))
    elif st.session_state.page=='수집·품질':
        quality_page(meta)
    else:
        page=st.session_state.page
        st.title(page)
        captions={'공고 검색':'조건으로 찾고, 원문 근거까지 확인하세요.',
                  '지역 비교':'같은 조건의 공고를 지역별로 나란히 비교합니다.',
                  '단가계약 분석':'기관·사업연도별 단가계약의 예산과 표본을 확인합니다.',
                  '검토 대기':'미수집·차수 불일치·원본 품질을 근거와 함께 확인합니다.'}
        st.html('<div class="subtitle">'+captions[page]+'</div>')
        if not records:
            st.info('미수집 · 저장된 분석 대상 공고가 없습니다. 별도 수집 CLI로 데이터를 준비하세요.')
        else:
            filters=filters_panel(records,meta)
            st.caption('회사 프로필 '+st.session_state.profile+' · 참가자격은 별도 확인이 필요합니다.')
            st.caption('조회 범위: 4992 관련 공고 및 단가 분석 후보 · 전체 나라장터 공고 검색이 아닙니다.')
            st.divider()
            if page in ('지역 비교','단가계약 분석'):
                analysis_page(records,meta,filters)
            else:
                rows=search(records,filters)
                if page=='검토 대기':
                    reasons=sorted({reason for row in rows for reason in review_reasons(row)})
                    reason=st.selectbox('검토 사유',['전체',*reasons])
                    rows=[row for row in rows if reason=='전체' or reason in review_reasons(row)]
                notice_list(rows,meta,filters)
    mode='합성 데이터' if meta.get('data_mode')=='synthetic' else '실데이터'
    st.html('<div class="app-footer">'+html_text(f'{mode} · 조회 {meta.get("generated_at_kst", "미수집")} · 수집 {meta.get("collection_status", "미수집")} · 스냅샷 {meta.get("snapshot_id", "-")}')+'</div>')
