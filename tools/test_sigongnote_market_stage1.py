"""Offline synthetic checks for high-impact classification/deduplication boundaries."""
import importlib.util
import sqlite3
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location('market',Path(__file__).with_name('sigongnote_market_stage1.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Rules(unittest.TestCase):
    def test_public_corporation_not_local_government(self):
        self.assertNotEqual(m.organization('한국도로공사 수도권본부')[0],'수도권 지자체 수요기관 후보')
        self.assertNotEqual(m.organization('경기도 경기주택도시공사')[0],'수도권 지자체 수요기관 후보')
        self.assertNotEqual(m.organization('서울특별시교육청')[0],'수도권 지자체 수요기관 후보')
    def test_parent_preserves_city_under_province(self):
        self.assertEqual(m.organization('경기도 성남시 분당구')[1],'경기도 성남시')
    def test_site_and_buyer_are_separate(self):
        p=m.geography('서울특별시 강남구','한국도로공사')
        self.assertTrue(p['in_scope']);self.assertEqual(p['buyer_segment'],'수도권 현장 기타기관')
        p=m.geography('충청남도 천안시','경기도 남양주시')
        self.assertTrue(p['in_scope']);self.assertTrue(p['geo_review'])
    def test_noncapital_unknown_buyer_not_inferred(self):
        self.assertFalse(m.geography('충청남도','한국도로공사')['in_scope'])
    def test_maintenance_without_annual_phrase_remains(self):
        p=m.classify('교통신호등 보수공사')
        self.assertTrue(p['thematic_candidate']);self.assertFalse(p['annual_unit_candidate'])
    def test_road_paving_not_lane_painting(self):
        p=m.classify('노면 재포장 및 도로 보수공사')
        self.assertEqual(p['primary_class'],'확장후보: 도로·보도 일반보수')
    def test_new_park_excluded(self):
        self.assertEqual(m.classify('도시공원 조성공사')['classification_status'],m.EXCLUDE)
    def test_mixed_construction_review(self):
        self.assertEqual(m.classify('공원 시설보수 및 신규 설치공사')['work_type'],'혼합')
        self.assertEqual(m.classify('공원 시설보수 및 신규 설치공사')['classification_status'],m.REVIEW)
    def test_multiple_facilities_one_primary(self):
        p=m.classify('차선도색 및 교통신호기 정비공사')
        self.assertTrue(p['primary_class'].startswith('혼합'));self.assertGreaterEqual(len(p['facility_tags']),2)
    def test_title_year_no_fallback(self):
        self.assertEqual(m.title_years('2025년 가로수 정비공사'),['2025'])
        self.assertEqual(m.title_years('가로수 정비공사'),[])
    def test_money_null_zero_overflow(self):
        self.assertEqual(m.money(None),(None,'NULL'));self.assertEqual(m.money('0'),(0,'0'))
        self.assertEqual(m.money('999999999999999999999999'),(None,'이상값'))
        self.assertEqual(m.money('-1'),(None,'이상값'));self.assertEqual(m.money('1,234'),(1234,'유효 양수'))
    def test_urls_and_csv_safety(self):
        self.assertEqual(m.safe_url('https://example.test/a?serviceKey=not-a-real-key'),'')
        self.assertTrue(m.csv_value('=1+1').startswith("'"));self.assertEqual(m.csv_value('000'),'000')

class History(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row
        self.c.executescript('CREATE TABLE base(no,ord,dt,year,org,prev,reyn,kind,title);CREATE TABLE conflicts(no,ord);')
    def tearDown(self):self.c.close()
    def add(self,no,ord_,date,kind='등록공고',prev='',org='A',title='보수공사'):
        self.c.execute('INSERT INTO base VALUES (?,?,?,?,?,?,?,?,?)',(no,ord_,date,date[:4],org,prev,'Y' if prev else 'N',kind,title))
    def test_cancel_then_new_non_cancel(self):
        self.add('A','000','2024-01-01 00:00:00','취소공고');self.add('A','001','2024-01-02 00:00:00')
        m.build_nodes(self.c);r=self.c.execute('SELECT * FROM nodes').fetchone()
        self.assertEqual(r['kind'],'등록공고');self.assertEqual(r['cancel_history'],1)
    def test_ord_date_conflict_unknown(self):
        self.add('A','000','2024-01-02 00:00:00');self.add('A','001','2024-01-01 00:00:00')
        m.build_nodes(self.c);self.assertEqual(self.c.execute('SELECT selection FROM nodes').fetchone()[0],'대표 선정 미확인')
    def test_explicit_same_year_chain(self):
        self.add('A','000','2024-01-01 00:00:00');self.add('B','000','2024-01-02 00:00:00',prev='A-000')
        m.build_nodes(self.c);m.lineage(self.c)
        self.assertEqual({r[0] for r in self.c.execute('SELECT op_id FROM links')},{'OBS:A'})
        self.assertEqual({r[0] for r in self.c.execute('SELECT terminal FROM links')},{'B'})
    def test_crossyear_not_merged_or_silently_shifted(self):
        self.add('A','000','2024-12-31 00:00:00');self.add('B','000','2025-01-01 00:00:00',prev='A')
        m.build_nodes(self.c);m.lineage(self.c);self.assertEqual({r[0] for r in self.c.execute('SELECT op_id FROM links')},{''})
    def test_branch_not_merged(self):
        self.add('A','000','2024-01-01 00:00:00')
        self.add('B','000','2024-01-02 00:00:00',prev='A');self.add('C','000','2024-01-03 00:00:00',prev='A')
        m.build_nodes(self.c);m.lineage(self.c);self.assertEqual({r[0] for r in self.c.execute('SELECT op_id FROM links')},{''})
    def test_cycles_unknown(self):
        self.add('A','000','2024-01-01 00:00:00',prev='B');self.add('B','000','2024-01-02 00:00:00',prev='A')
        m.build_nodes(self.c);m.lineage(self.c);self.assertEqual({r[0] for r in self.c.execute('SELECT op_id FROM links')},{''})
    def test_same_title_separate_years_not_merged(self):
        self.add('A','000','2024-01-01 00:00:00');self.add('B','000','2025-01-01 00:00:00')
        m.build_nodes(self.c);m.lineage(self.c);self.assertEqual(self.c.execute('SELECT COUNT(*) FROM links').fetchone()[0],0)

if __name__=='__main__':unittest.main(verbosity=2)
