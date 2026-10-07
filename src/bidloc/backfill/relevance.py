"""공사 목록의 최신 복합키 기준 집합 판정. 밀린 필드로 부정 판정하지 않는다."""
import re

from bidloc.backfill.store import transaction
from bidloc.timeutil import now_utc, to_iso_utc


def finalize(conn, codes, *, license_complete):
    now = to_iso_utc(now_utc())
    with transaction(conn):
        conn.execute("DROP TABLE IF EXISTS temp._current_relevance")
        conn.execute("""CREATE TEMP TABLE _current_relevance AS
            SELECT bid_ntce_no, MAX(bid_ntce_ord) AS ord,
                   MAX(CASE WHEN ntce_kind_nm LIKE '%취소%' THEN 1 ELSE 0 END) AS cancelled
            FROM bf_notice_revision GROUP BY bid_ntce_no""")
        conn.execute("CREATE UNIQUE INDEX temp.idx_current_relevance ON _current_relevance(bid_ntce_no,ord)")
        conn.execute("DROP TABLE IF EXISTS temp._license_summary")
        conn.execute("""CREATE TEMP TABLE _license_summary AS
            SELECT l.bid_ntce_no, COUNT(*) AS n,
                   SUM(CASE WHEN quality_flag IS NOT NULL THEN 1 ELSE 0 END) AS suspect
            FROM _current_relevance c JOIN bf_license_limit l ON l.bid_ntce_no=c.bid_ntce_no AND l.bid_ntce_ord=c.ord
            GROUP BY l.bid_ntce_no""")
        conn.execute("CREATE UNIQUE INDEX temp.idx_license_summary ON _license_summary(bid_ntce_no)")
        conn.execute("DROP TABLE IF EXISTS temp._target_relevance")
        conn.execute("CREATE TEMP TABLE _target_relevance (bid_ntce_no TEXT PRIMARY KEY)")
        placeholders = ','.join('?' for _ in codes)
        like = ' OR '.join('l.permsn_indstryty_list LIKE ?' for _ in codes)
        rows = conn.execute(f"""SELECT l.bid_ntce_no,l.license_code,l.permsn_indstryty_list
            FROM bf_license_limit l JOIN _current_relevance c
            ON l.bid_ntce_no=c.bid_ntce_no AND l.bid_ntce_ord=c.ord
            WHERE l.license_code IN ({placeholders}) OR {like}""", (*codes, *(f'%{x}%' for x in codes)))
        targets = {no for no, code, permsn in rows if set(codes) & ({code} | set(re.findall(r'\d+', permsn or '')))}
        conn.executemany("INSERT INTO _target_relevance VALUES (?)", ((no,) for no in targets))
        conn.execute("""INSERT OR IGNORE INTO bf_notice_state(bid_ntce_no,relevance,updated_at_utc)
                        SELECT bid_ntce_no,'PENDING',? FROM _current_relevance""", (now,))
        conn.execute("""UPDATE bf_notice_state SET relevance='CANCELLED',relevance_basis='목록의 어느 차수든 취소',updated_at_utc=?
                        WHERE bid_ntce_no IN (SELECT bid_ntce_no FROM _current_relevance WHERE cancelled=1)""", (now,))
        if license_complete:
            conn.execute("""UPDATE bf_notice_state SET relevance='UNKNOWN',
                relevance_basis='최신 차수의 면허제한 미수집 또는 필드 밀림 의심',updated_at_utc=?
                WHERE relevance!='CANCELLED' AND bid_ntce_no IN (SELECT bid_ntce_no FROM _current_relevance)""", (now,))
            conn.execute("""UPDATE bf_notice_state SET relevance='NOT_RELEVANT',
                relevance_basis='최신 차수의 정상 면허제한 행에 목표 코드 없음',updated_at_utc=?
                WHERE relevance!='CANCELLED' AND bid_ntce_no IN
                    (SELECT bid_ntce_no FROM _license_summary WHERE suspect=0)""", (now,))
        conn.execute("""UPDATE bf_notice_state SET relevance='RELEVANT',relevance_basis=?,updated_at_utc=?
            WHERE relevance!='CANCELLED' AND bid_ntce_no IN (SELECT bid_ntce_no FROM _target_relevance)""",
                     ('최신 공고 차수의 목표 면허 코드 일치: ' + ','.join(codes) + ' (참가조건 전체 확인 아님)', now))
        conn.execute("""UPDATE bf_notice_state SET
            license_ord=(SELECT c.ord FROM _current_relevance c JOIN _license_summary l
                ON c.bid_ntce_no=l.bid_ntce_no WHERE c.bid_ntce_no=bf_notice_state.bid_ntce_no),
            region_ord=(SELECT MAX(r.bid_ntce_ord) FROM bf_allowed_region r JOIN _current_relevance c
                ON c.bid_ntce_no=r.bid_ntce_no AND r.bid_ntce_ord<=c.ord
                WHERE r.bid_ntce_no=bf_notice_state.bid_ntce_no)
            WHERE bid_ntce_no IN (SELECT bid_ntce_no FROM _current_relevance)""")
    return dict(conn.execute("""SELECT s.relevance,COUNT(*) FROM bf_notice_state s JOIN _current_relevance c
                                ON c.bid_ntce_no=s.bid_ntce_no GROUP BY s.relevance"""))
