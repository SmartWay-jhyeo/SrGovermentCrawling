"""Package only after actual CSV verification. No recomputation of source data."""
import csv,datetime,hashlib,json,shutil,sys,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/sigongnote_market_nationwide'

def read(name):
    with (OUT/name).open(encoding='utf-8-sig',newline='') as f:yield from csv.DictReader(f)

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    validation=json.loads((OUT/'validation_results.json').read_text(encoding='utf-8'))
    independent=json.loads((OUT/'independent_validation.json').read_text(encoding='utf-8'))
    assert all(validation['checks'].values()) and all(independent['checks'].values())
    examples=list(read('historical_name_source_examples.csv'))
    assert len(examples)==4 and all(r['same_code']=='True' and r['same_name']=='True' for r in examples)
    snapshot=validation['source']['snapshot'];st=Path(snapshot['path']).stat()
    assert (st.st_size,st.st_mtime_ns)==(snapshot['bytes'],snapshot['mtime_ns'])
    alias_counts={}
    for r in read('historical_agency_name_audit.csv'):alias_counts[r['notice_year']]=alias_counts.get(r['notice_year'],0)+int(r['source_revision_count'])
    validation['independent_csv_verification']={'file':'independent_validation.json','passed':sum(independent['checks'].values()),'groups_recomputed':independent['national_distribution_groups']}
    validation['targeted_raw_agency_examples']={'count':4,'matching_names_codes':4,'not_an_all_json_audit':True}
    validation['data_quality_findings']={'stored_combined_agency_name_by_notice_year':alias_counts,'historical_organization_validity':'미확인; 과거 전남/광주로 배분하지 않음'}
    validation['final_source_signature_unchanged']=True
    (OUT/'validation_results.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2),encoding='utf-8')
    report=(OUT/'national_market_summary.md').read_text(encoding='utf-8')
    # Human-facing tables must not express an empty reference sample as a zero-won market.
    lines=[];headers=[]
    for line in report.splitlines():
        if line.startswith('|'):
            values=[x.strip() for x in line.strip('|').split('|')]
            if not headers:headers=values
            elif not all(set(x)<=set('-: ') for x in values):
                for i,(h,v) in enumerate(zip(headers,values)):
                    if v=='0' and '합계' in h:values[i]='미집계'
                line='| '+' | '.join(values)+' |'
        else:headers=[]
        lines.append(line)
    report='\n'.join(lines)+'\n'
    report=report.replace('## 산출물',
      '## 독립 검증과 보완 기록\n\n생성기 집계검사 21개, 독립 CSV 검사 17개, 분류 회귀검사 10개가 각각 통과했다. 독립 검사는 전국 분포 840개 그룹의 건수·합계·평균·중앙값·사분위수·최소·최대와 1~5% 산술을 후보 CSV에서 재계산했다. 검사 결과를 서로 다른 기록으로 보존한다.\n\n통합 명칭 관련 연도별 1개씩 총 4개 공고의 원본 item_json에서 기관 코드·명칭만 제한적으로 읽었다. 네 건 모두 DB/캐시와 일치했다(historical_name_source_examples.csv). 표기 갱신 원인 및 당시 조직/코드 유효기간은 여전히 미확인이다. 이는 원본 JSON 전수검사가 아니다. 과거 명칭상 지역의 관측 0건은 시장 수요 0의 뜻이 아니며, 참고액 표본이 없으면 금액은 미집계로 표시했다.\n\n재실행 순서: `python tools/sigongnote_market_nationwide.py` → `python tools/sigongnote_nationwide_agency_audit.py` → `python tools/verify_sigongnote_nationwide.py` → `python tools/finalize_sigongnote_nationwide.py`. 회귀검사는 `python tools/test_sigongnote_nationwide.py`.\n\n## 산출물')
    report+='\n추가 검증 산출물: historical_name_source_examples.csv(4행), independent_validation.json, validation_results.json 보완 기록. 최종 파일별 행 수·바이트·SHA-256은 manifest.json이 기준이다. ZIP은 원본 DB·JSON·원문 문서를 포함하지 않는다.\n'
    (OUT/'national_market_summary.md').write_text(report,encoding='utf-8')
    (OUT/'execution_notes.md').write_text('''# 실행 기록

- 완료: `python tools/sigongnote_market_nationwide.py`. 전국 502,346차수의 키·핵심 스칼라·본문 해시 열 일치, 불일치 0. 고정 사본 해시는 기존 기록과 경로/크기/mtime를 확인하여 사용했다.
- 완료: `--refresh-outputs`로 경량 열, 설명표, 원문 180일/28일 빈도 구분을 정리했다. 원본 전수 재검사는 반복하지 않았다.
- 완료: `python tools/test_sigongnote_nationwide.py` — 회귀검사 10개 PASS.
- 완료: 저장 명칭의 `전남광주통합특별시`를 발견하여 `--refresh-outputs --remap-organizations`로 기관 표시 규칙을 4.1로 보완했다. 파생 13,876행의 기관유형/지역/상위 표시가 바뀌었으며 전국 후보 총수는 변하지 않았다. 기관명 전수 집계는 전국 source 502,346차수를 다시 대조했다. 공고 당시 조직·코드는 복원하지 않았다.
- 완료: `python tools/sigongnote_nationwide_agency_audit.py` — 연도별 1건, 원본 JSON 4건의 기관 코드·명칭이 저장 열과 일치. 전체 JSON을 재검사한 것은 아니다.
- 완료: 생성기 검사 21개 PASS. `python tools/verify_sigongnote_nationwide.py` — 독립 CSV 검사 17개 PASS, 전국 분포 840그룹 재계산 일치.
- 완료: 최종 문서에서 참고금액 표본이 없는 금액의 0 표기를 미집계로 바꾸고 manifest/ZIP을 갱신했다. 원본/이전 stage1·stage2·verified 결과는 보존했다.
- 실패: 선택적 `Get-CimInstance Win32_Process` 성능 조회가 Windows AccessDenied로 실패했다. 추가 권한 요청 없이 생략했으며 데이터 읽기·집계·검증에 사용하지 않았다.
- 미검증: 제공 계산근거 ZIP 미발견, 공고 당시 기관 체계/코드 유효기간, 전체 수집 완전성, 대부분의 실제 과업·금액 총액성·기간, 모든 실제 계약/집행/정산액, 구리시 우선 3건의 원문, 서비스 가격 수용성, 벤처나라 등록 요건.
- SKIPPED: 외부 API/네트워크/다운로드 테스트(범위 밖). 새 수집·다운로드·자동 재시도·collector/예약작업 실행 0회.
''',encoding='utf-8')
    for name in ['sigongnote_market_nationwide.py','sigongnote_nationwide_rules.py','test_sigongnote_nationwide.py','verify_sigongnote_nationwide.py','sigongnote_nationwide_agency_audit.py','finalize_sigongnote_nationwide.py']:
        shutil.copyfile(ROOT/'tools'/name,OUT/name)
    manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf-8'));old=manifest['files'];files={}
    for p in sorted(OUT.iterdir()):
        if not p.is_file() or p.name in ('manifest.json','nationwide_results.zip'):continue
        m={'file_bytes':p.stat().st_size,'sha256':sha(p)}
        if p.suffix=='.csv':
            m['rows']=old[p.name]['rows'] if p.name in old and old[p.name]['file_bytes']==p.stat().st_size and 'rows' in old[p.name] else sum(1 for _ in read(p.name))
        files[p.name]=m
    manifest['files']=files;manifest['finalized_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    manifest['verification']={'generator_passed':21,'independent_csv_passed':17,'regression_tests_passed':10,'targeted_raw_name_checks':4}
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    with zipfile.ZipFile(OUT/'nationwide_results.zip','w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(OUT.iterdir()):
            if p.is_file() and p.name!='nationwide_results.zip':z.write(p,p.name)
    with zipfile.ZipFile(OUT/'nationwide_results.zip') as z:
        assert z.testzip() is None
        assert set(z.namelist())==set(files)|{'manifest.json'}
        assert all(not n.lower().endswith(('.sqlite3','.hwp','.hwpx','.pdf','.xls','.xlsx','.bin')) for n in z.namelist())
    print(json.dumps(dict(packaged_files=len(files)+1,zip_bytes=(OUT/'nationwide_results.zip').stat().st_size,zip_crc='PASS',source_signature='UNCHANGED',checks=manifest['verification']),ensure_ascii=False))

if __name__=='__main__':main()
