"""Regression cases from the user's review; offline, no synthetic source DB."""
import unittest
from sigongnote_nationwide_rules import classify_national,organization,site_region,OBSERVED_COMBINED_REGION

class NationalRules(unittest.TestCase):
    def test_multi_core(self):
        r=classify_national('2025년 교통안전표지 및 노면표시 정비공사 연간단가')
        self.assertEqual(r['category'],'복수 핵심시설 통합');self.assertEqual(r['candidate'],1)
    def test_install_requires_review(self):
        r=classify_national('노후포장도로 노면표시 설치공사')
        self.assertEqual(r['candidate'],1);self.assertEqual(r['commercial_tier'],'개별검토')
    def test_greenhouse_and_ancillary(self):
        for t in ['여울공원 전시온실 건립공사(건축, 토목, 조경, 기계)','문화체육센터 생활SOC복합화사업(건축,토목,조경,기계)']:
            self.assertEqual(classify_national(t)['commercial_tier'],'별도사업모델')
    def test_station(self):
        self.assertEqual(classify_national('문화공원역 소방시설 개량공사')['candidate'],0)
    def test_green_management(self):
        for t in ['2025년 잔디관리 공사','조경관리 공사','병해충 방제 공사']:
            r=classify_national(t);self.assertEqual(r['category'],'공원녹지');self.assertEqual(r['work_type'],'유지보수')
    def test_paint_and_disaster_context(self):
        r=classify_national('재난대비 노면표시 도색공사 연간단가')
        self.assertEqual(r['candidate'],1);self.assertEqual(r['work_type'],'미확인')
    def test_original_organization(self):
        r=organization('인천광역시 종합건설본부')
        self.assertEqual(r['parent_local_government'],'인천광역시')
        self.assertEqual(organization('경기도 고양시 덕양구')['parent_local_government'],'경기도 고양시')
        self.assertEqual(organization('강원도 춘천시')['parent_local_government'],'강원도 춘천시')
    def test_separate_types(self):
        for s in ['한국도로공사 경기본부','전라남도 교육청','경기도 구리도시공사']:
            self.assertNotEqual(organization(s)['buyer_type'],'지자체 수요기관')
    def test_multiple_sites(self):
        self.assertEqual(site_region('경기도 구리시, 강원도 춘천시'),'복수지역/배분 미확인')
        self.assertEqual(site_region('경기도 광주시'),'경기')
        self.assertEqual(site_region('광주광역시 북구'),'광주')
    def test_observed_combined_name_is_not_backdated(self):
        r=organization('전남광주통합특별시 해남군')
        self.assertEqual(r['buyer_type'],'지자체 수요기관')
        self.assertEqual(r['buyer_region'],OBSERVED_COMBINED_REGION)
        self.assertEqual(r['parent_local_government'],'전남광주통합특별시 해남군')
        self.assertEqual(organization('전남광주통합특별시 광주청사')['buyer_type'],'지자체 수요기관')
        self.assertEqual(organization('전남광주통합특별시교육청')['buyer_type'],'교육기관 후보')

if __name__=='__main__':unittest.main()
