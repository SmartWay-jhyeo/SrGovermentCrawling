"""Synthetic regression cases; no network or real expected market totals."""
import copy
import unittest
from sigongnote_stage2_rules import commercial, GREEN, LANE, TRAFFIC
from prepare_sigongnote_stage2 import link_check
from sigongnote_market_stage2 import observed_representative, quantiles


def sample(title,work='미확인',annual=False,**values):
    return dict(title=title,work_type=work,annual_unit_candidate=annual,title_business_years=[],geo_review=False,**values)


class CommercialRules(unittest.TestCase):
    def test_legacy_fields_not_overwritten(self):
        p=sample('노면표시 도색공사(연간단가)',annual=True,presmpt_prce='12345');saved=copy.deepcopy(p)
        new=commercial(p)
        self.assertEqual(p,saved);self.assertFalse(set(p)&set(new))
    def test_annual_painting_priority_without_work_promotion(self):
        p=sample('연수구 재난대비 노면표시 도색공사(연간단가)',annual=True)
        self.assertEqual(commercial(p)['s2_commercial_tier'],'우선검토');self.assertEqual(p['work_type'],'미확인')
    def test_painting_not_universal_maintenance(self):
        p=sample('청사 벽면 도색공사',annual=True)
        self.assertFalse(commercial(p)['s2_commercial_candidate'])
    def test_disaster_preparedness_not_work_confirmation(self):
        p=sample('재난대비 노면표시 공사')
        self.assertEqual(commercial(p)['s2_commercial_tier'],'개별검토');self.assertEqual(p['work_type'],'미확인')
    def test_two_core_facilities_single_group(self):
        p=sample('교통안전표지 및 노면표시 정비공사 연간단가','유지보수',True)
        new=commercial(p)
        self.assertEqual(new['s2_exclusive_group'],'통합 핵심: 복수 핵심시설')
        self.assertEqual(new['s2_facility_query_tags'],[LANE,TRAFFIC]);self.assertEqual(new['s2_commercial_tier'],'우선검토')
    def test_old_paving_marking_installation_individual(self):
        p=sample('노후포장도로 노면표시 설치공사(2구역)','신설·확장')
        new=commercial(p)
        self.assertEqual(new['s2_commercial_tier'],'개별검토');self.assertEqual(new['s2_commercial_scope'],'핵심')
        self.assertEqual(p['work_type'],'신설·확장')
    def test_school_zone_installation_not_auto_maintenance(self):
        new=commercial(sample('어린이보호구역 노면표시 및 노란색 횡단보도 설치 사업','신설·확장'))
        self.assertEqual(new['s2_commercial_tier'],'개별검토')
    def test_explicit_new_installation_separate_model(self):
        new=commercial(sample('신규 도로 신설 및 노면표시 설치공사','신설·확장'))
        self.assertEqual(new['s2_commercial_tier'],'별도사업모델');self.assertFalse(new['s2_commercial_candidate'])
    def test_greenhouse_construction_separate(self):
        new=commercial(sample('여울공원 전시온실 건립공사(건축, 토목, 조경, 기계)'))
        self.assertEqual(new['s2_commercial_tier'],'별도사업모델')
    def test_soc_ancillary_landscape_separate(self):
        new=commercial(sample('문화체육센터 생활SOC복합화사업(건축,토목,조경,기계)'))
        self.assertFalse(new['s2_commercial_candidate'])
    def test_station_name_not_park(self):
        new=commercial(sample('동대문역사문화공원역 근무환경 개선 소방공사'))
        self.assertNotIn(GREEN,new['s2_facility_query_tags']);self.assertFalse(new['s2_commercial_candidate'])
    def test_station_name_with_real_green_space_kept(self):
        new=commercial(sample('문화공원역 주변 녹지 유지관리','유지보수'))
        self.assertIn(GREEN,new['s2_facility_query_tags']);self.assertTrue(new['s2_commercial_candidate'])
    def test_median_trees_location_not_traffic_work(self):
        new=commercial(sample('가로화단 및 중앙분리대 등 상록조형수목 관리공사','유지보수'))
        self.assertEqual(new['s2_facility_query_tags'],[GREEN])
    def test_median_replacement_and_trees_genuinely_multitag(self):
        new=commercial(sample('중앙분리대 교체 및 수목 정비공사','유지보수'))
        self.assertEqual(new['s2_facility_query_tags'],[TRAFFIC,GREEN])
    def test_actual_paving_and_marking_separate_composite(self):
        new=commercial(sample('도로 재포장 및 차선도색 보수공사','유지보수'))
        self.assertEqual(new['s2_commercial_scope'],'핵심·인접 복합')
    def test_missing_year_not_replaced_by_publication(self):
        p=sample('공원 보수',notice_year='2025')
        self.assertEqual(commercial(p)['s2_title_year_candidate_bucket'],'미기재')
    def test_two_year_title_not_duplicated(self):
        p=sample('공원 보수');p['title_business_years']='["2024","2025"]'
        self.assertEqual(commercial(p)['s2_title_year_candidate_bucket'],'복수연도 미확인')
    def test_title_year_not_verified_period(self):
        p=sample('2025년 노면표시 연간단가 1차(A구역)',notice_year='2024');p['title_business_years']=['2025']
        new=commercial(p)
        self.assertEqual(new['s2_title_year_candidate_bucket'],'2025');self.assertEqual(new['s2_verified_business_year'],'')
        self.assertEqual(new['s2_service_subscription_count'],'');self.assertTrue(new['s2_term_or_zone_markers'])
    def test_small_amount_never_scaled(self):
        p=sample('노면표시 유지보수 연간단가','유지보수',True,presmpt_prce='137101')
        new=commercial(p);self.assertEqual(p['presmpt_prce'],'137101');self.assertTrue(new['s2_small_annual_amount_review'])
        self.assertEqual(new['s2_billing_basis_candidate_amount'],'');self.assertEqual(new['s2_price_X'],'미정')
    def test_budget_residual_not_billing_basis(self):
        p=sample('노면표시',presmpt_prce='100',bdgt_amt='170',vat='10')
        new=commercial(p);self.assertEqual(new['s2_budget_price_vat_residual'],60);self.assertEqual(new['s2_billing_basis_candidate_amount'],'')
    def test_null_not_zero(self):
        new=commercial(sample('공원 보수',presmpt_prce='',bdgt_amt='100',vat='10'))
        self.assertEqual(new['s2_budget_price_vat_residual'],'')
    def test_geography_unknown_downgrades_review_priority(self):
        p=sample('노면표시 유지관리 연간단가','유지보수',True);p['geo_review']=True
        self.assertEqual(commercial(p)['s2_commercial_tier'],'개별검토')
    def test_representative_requires_known_not_cancelled(self):
        p=dict(is_opportunity_representative='1',in_scope='1',representative_status='분석용 대표 확인',analysis_opportunity_id='OBS:DEMO',notice_kind='취소공고')
        self.assertFalse(observed_representative(p));p['notice_kind']='등록공고';self.assertTrue(observed_representative(p))
        p['analysis_opportunity_id']='';self.assertFalse(observed_representative(p))
    def test_quantiles_empty_and_type7(self):
        self.assertEqual(quantiles([])['sum'],'');self.assertEqual(quantiles([10,20,30,40])['p25'],17.5)
    def test_link_key_mismatch_and_credentials(self):
        self.assertTrue(link_check('https://www.g2b.go.kr/file?bidPbancNo=A&bidPbancOrd=001','A','000').startswith('불가'))
        self.assertTrue(link_check('https://www.g2b.go.kr/file?%73erviceKey=synthetic','A','000').startswith('불가'))
    def test_buyer_not_title_location(self):
        p=sample('남동구 노면표시 도색 연간단가',annual=True,dminstt_nm='인천광역시 종합건설본부')
        commercial(p);self.assertEqual(p['dminstt_nm'],'인천광역시 종합건설본부')


if __name__=='__main__':unittest.main(verbosity=2)
