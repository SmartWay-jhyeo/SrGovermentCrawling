"""외부 통신 없는 단일 HTML 대시보드. 수집기와 분리해 DB 스냅샷에서 만든다."""
import argparse
from html import escape
import json
from pathlib import Path

from bidloc.analysis import AnalysisRepository, Filters, first_openings, location, municipality, relevant_records, shortlist, unit_category, unitprice
from bidloc.config import load_settings
from bidloc.exports import export_analysis, safe_url, write_json
from bidloc.repositories.db import connect


def frontend_record(d):
    return {'key':d['key'],'no':d['bid_ntce_no'],'ord':d['bid_ntce_ord'],'title':d['bid_ntce_nm'],
            'published':d['bid_ntce_dt'],'deadline':d['deadline'],'opening':d['openg_dt'],'year':d['year'],
            'contract':d['cntrct_cncls_mthd_nm'],'agency':d['dminstt_nm'],'issuer':d['ntce_instt_nm'],
            'site':d['cnstrtsite_rgn_nm'],'budget':d['bdgt_amt'],'price':d['presmpt_prce'],
            'regions':d['regions'],'codes':d['license_codes'],'regionOrd':d['region_ord'],
            'regionMismatch':d['region_revision_mismatch'],'licenseSuspect':any(l['quality_flag'] for l in d['licenses']),
            'relevant':d['relevance']=='RELEVANT','counts':[o['prtcpt_cnum'] for o in first_openings(d) if o['prtcpt_cnum'] is not None],
            'linked':bool(first_openings(d)),'openingKeys':[[o['bid_clsfc_no'],o['rbid_no']] for o in d['openings']],
            'source':d['last_response_id'],'licenseSources':sorted({l['response_id'] for l in d['licenses'] if l['response_id'] is not None}),
            'regionSources':sorted({s for s in d['region_sources'] if s is not None}),
            'openingSources':sorted({o['response_id'] for o in d['openings'] if o['response_id'] is not None}),
            'url':safe_url(d['url']),'quality':d['quality_flag'],
            'category':unit_category(d['bid_ntce_nm']) if '단가' in (d['bid_ntce_nm'] or '') else None,
            'municipality':municipality(d['dminstt_cd'],d['dminstt_nm'])}


def build(conn, settings, hq='경기도 남양주시'):
    repo=AnalysisRepository(conn,Filters())
    records=repo.records()
    meta=repo.metadata()
    meta['data_mode']=getattr(settings,'data_mode','synthetic')
    return render_snapshot(records,meta,settings.report_dir,hq)


def render_snapshot(records,meta,directory,hq='경기도 남양주시'):
    """이미 읽은 스냅샷을 같은 계산·템플릿으로 내보낸다. API/DB 호출 없음."""
    loc=location(records)
    begin,end=meta['filters']['begin'],meta['filters']['end']
    unit=unitprice(records,begin,end)
    short=shortlist(records,hq)
    target=Path(directory)
    target.mkdir(parents=True,exist_ok=True)
    export_analysis(target,'location',meta,loc,relevant_records(records))
    export_analysis(target,'unitprice',meta,unit,unit['details'])
    export_analysis(target,'shortlist',meta,short)
    write_json(target/'agency_samples.json',unit['agency_samples'])
    data={'meta':meta,'hq':hq,'records':[frontend_record(d) for d in records
          if d['relevance']=='RELEVANT' or ('단가' in (d['bid_ntce_nm'] or '') and unit_category(d['bid_ntce_nm']) and municipality(d['dminstt_cd'],d['dminstt_nm']))]}
    package=Path(__file__).parent/'report_assets'
    template=(package/'dashboard.html').read_text(encoding='utf-8')
    # </script>와 HTML 엔티티를 데이터가 탈출할 수 없도록 이스케이프한다.
    payload=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    html=template.replace('/* INLINE_CSS */',(package/'dashboard.css').read_text(encoding='utf-8'))
    html=html.replace('/* INLINE_JS */',(package/'dashboard.js').read_text(encoding='utf-8'))
    html=html.replace('<!-- DATA_JSON -->',payload)
    html=html.replace('<!-- PERIOD -->',escape(f'{begin or "미수집"} — {end or "미수집"}'))
    html=html.replace('<!-- GENERATED -->',escape(meta['generated_at_kst']))
    (target/'progress.html').write_text(html,encoding='utf-8')
    return {'path':str(target/'progress.html'),'records':len(data['records']),'location_rows':len(loc['rows']),
            'unitprice_groups':len(unit['rows']),'shortlist_allowed':len(short['allowed']),
            'shortlist_unspecified':len(short['unspecified']),'metadata':meta}


def main():
    parser=argparse.ArgumentParser(description='로컬 분석/진행 HTML 및 CSV/JSON 생성 (네트워크 없음)')
    parser.add_argument('--hq',default='경기도 남양주시')
    args=parser.parse_args()
    settings=load_settings()
    conn=connect(settings.database_path,readonly=True)
    conn.execute('BEGIN')
    try:
        result=build(conn,settings,args.hq)
        write_json(settings.report_dir/'report_build.json',result)
        print(json.dumps({k:v for k,v in result.items() if k!='metadata'},ensure_ascii=False,indent=2))
        return 0
    finally:
        conn.close()


if __name__=='__main__':
    raise SystemExit(main())
