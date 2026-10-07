from pathlib import Path

from bidloc.analysis import AnalysisRepository, Filters, location, relevant_records, shortlist, unitprice
from bidloc.config import ConfigError, load_settings
from bidloc.exports import export_analysis
from bidloc.repositories.db import connect


def command(args):
    root=Path(args.project_root).resolve() if args.project_root else Path.cwd()
    settings=load_settings(root)
    if args.license!='4992':
        raise ConfigError('현재 분석 확정 대상은 면허 4992다. 다른 면허의 관련성을 검증한 뒤 분석을 확장한다')
    if not settings.database_path.exists():
        raise ConfigError('분석 DB가 없다. 수집 후 다시 실행한다')
    from datetime import date
    for value in (args.begin,args.end):
        if value:
            date.fromisoformat(value)
    if args.begin and args.end and args.begin>args.end:
        raise ConfigError('분석 시작일이 끝일보다 늦다')
    if args.min_amount is not None and args.max_amount is not None and args.min_amount>args.max_amount:
        raise ConfigError('최소 추정가격이 최대보다 크다')
    conn=connect(settings.database_path,readonly=True)
    conn.execute('BEGIN')
    try:
        repo=AnalysisRepository(conn,Filters(args.begin,args.end,args.license,args.contract,args.min_amount,args.max_amount,args.profile))
        records=repo.records()
        meta=repo.metadata()
        meta['data_mode']=settings.data_mode
        if args.analysis=='location':
            result=location(records,args.license,args.hq)
            details=relevant_records(records,args.license)
        elif args.analysis=='unitprice':
            result=unitprice(records,repo.begin,repo.end)
            details=result['details']
        else:
            result=shortlist(records,args.hq,args.license)
            details=None
        export_analysis(settings.report_dir,args.analysis,meta,result,details)
        n=len(result.get('rows',result.get('allowed',[])))
        print(f'[완료] {args.analysis}: {n}행, {settings._rel(settings.report_dir)}, {meta["collection_status"]}; API 호출 0회')
        return 0
    finally:
        conn.close()


def add_arguments(parser, *, hq_required=False):
    parser.add_argument('--hq',required=hq_required)
    parser.add_argument('--license',default='4992')
    parser.add_argument('--from',dest='begin')
    parser.add_argument('--to',dest='end')
    parser.add_argument('--contract')
    parser.add_argument('--min-amount',type=int)
    parser.add_argument('--max-amount',type=int)
    parser.add_argument('--profile',choices=['기존 법인 면허 추가','신규 법인','본점 이전'],default='기존 법인 면허 추가')
    parser.set_defaults(func=command)
