"""로컬 CSV/JSON. UTF-8 BOM, 수식 셀 무력화, 원공고 키/응답 ID 추적."""
import csv
import json
from pathlib import Path
from urllib.parse import urlsplit


def safe_cell(value):
    if value is None:
        return ''
    if isinstance(value,(dict,list,tuple)):
        value=json.dumps(value,ensure_ascii=False)
    text=str(value)
    return "'"+text if text.lstrip().startswith(('=','+','-','@','\t','\r','\n')) else text


def safe_url(value):
    try:
        url=urlsplit(value or '')
        if url.scheme=='https' and url.hostname and (url.hostname=='g2b.go.kr' or url.hostname.endswith('.g2b.go.kr')) and not url.username:
            return value
    except ValueError:
        pass
    return None


def write_csv(path, rows):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    fields=list(dict.fromkeys(k for r in rows for k in r)) or ['상태']
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream)
        writer.writerow(fields)
        for row in rows:
            writer.writerow([safe_cell(row.get(k)) for k in fields])


def write_json(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')


def export_analysis(directory, kind, meta, result, details=None):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    write_json(directory/(kind+'.json'),{'metadata':meta,**result})
    rows=result.get('rows')
    if rows is None:
        rows=[{**d,'candidate_status':label} for label in ('allowed','unspecified','revision_review') for d in result[label]]
    if kind=='unitprice':
        flattened=[]
        for row in rows:
            flat={k:v for k,v in row.items() if k not in ('notice_budget','annual_budget','estimated_price')}
            for name in ('notice_budget','annual_budget','estimated_price'):
                flat.update({name+'_'+k:v for k,v in row[name].items()})
            flattened.append(flat)
        rows=flattened
    write_csv(directory/(kind+'.csv'),rows)
    if details is not None:
        write_csv(directory/(kind+'_notices.csv'),details)
        write_json(directory/(kind+'_notices.json'),{'metadata':meta,'notices':details})
    if 'annual' in result:
        write_csv(directory/(kind+'_annual.csv'),result['annual'])
