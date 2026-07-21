import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import re

class LibraryAPIHandler:
    # --- 공통 상수 관리 ---
    REGION_OPTIONS = {"전체": "", "서울": "11", "경기": "31", "광주": "24", "부산": "21", "인천": "23", "대구": "22", "경기도교육청": "GOE"}
    GOE_LIBS = {"141004": "과천", "141006": "경기평생", "141008": "중앙", "141012": "성남", "141106": "화성", "141010": "의정부", "141141": "평택", "141009": "광주", "141017": "김포", "141130": "포천"}
    DEFAULT_EXCLUSIONS = ["수험서", "문제집", "자격증", "기출", "워크북", "그래픽노블", "라이트노벨", "판타지소설", "무협소설", "컬러링북", "학습지", "잡지", "연속간행물", "큰글자책", "큰글씨책", "모의고사", "참고서", "대학교재"]
    KDC_BASE_NAMES = {"0": "00 총류", "1": "10 철학", "2": "20 종교", "3": "30 사회과학", "4": "40 자연과학", "5": "50 기술과학", "6": "60 예술", "7": "70 언어", "8": "81 문학", "9": "90 역사"}
    ALADIN_CAT_MAP = {"전체": [0], "일반": [0], "청소년": [1108], "아동": [1106, 13789]}
    
    KDC_TO_ALADIN_CID = {
        "0": [1], "1": [656], "2": [1230], "3": [798], "4": [987], 
        "5": [5347, 2173], "6": [517], "7": [1322], "8": [1], "9": [74]
    }

    def __init__(self, info_naru_key, aladin_ttb_key):
        self.info_naru_key = info_naru_key
        self.aladin_ttb_key = aladin_ttb_key
        self.naru_base_url = "http://data4library.kr/api"
        self.aladin_base_url = "http://www.aladin.co.kr/ttb/api"
        
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(pool_connections=50, pool_maxsize=100, max_retries=retries)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def _safe_get(self, url, params, timeout=10):
        try:
            resp = self.session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except: return None

    def clean_isbn(self, isbn):
        """ISBN에서 숫자만 추출 (하이픈 제거 등)"""
        return re.sub(r'[^0-9X]', '', str(isbn))

    def fetch_popular_books(self, page_size=500):
        params = {"authKey": self.info_naru_key, "format": "json", "pageSize": page_size}
        resp = self._safe_get(f"{self.naru_base_url}/loanItemSrch", params)
        return resp.json().get("response", {}).get("docs", []) if resp else []

    def check_book_existence(self, lib_code, isbn13):
        params = {"authKey": self.info_naru_key, "libCode": lib_code, "isbn13": self.clean_isbn(isbn13), "format": "json"}
        resp = self._safe_get(f"{self.naru_base_url}/bookExist", params, timeout=5)
        return resp.json().get("response", {}).get("result", {}).get("hasBook", "N") if resp else "N"

    def fetch_library_new_additions(self, lib_code, start_dt, end_dt, page_size=1000):
        params = {"authKey": self.info_naru_key, "libCode": lib_code, "startDt": start_dt, "endDt": end_dt, "pageSize": page_size, "format": "json"}
        resp = self._safe_get(f"{self.naru_base_url}/libItemSrch", params, timeout=15)
        if not resp: return {}
        docs = resp.json().get("response", {}).get("docs", [])
        return {self.clean_isbn(doc['doc'].get('isbn13')): (doc['doc'].get('class_no', ''), int(doc['doc'].get('loan_count', 0))) for doc in docs if doc['doc'].get('isbn13')}

    def fetch_libraries_by_region(self, region_code, page_size=10):
        params = {"authKey": self.info_naru_key, "region": region_code, "pageSize": page_size, "format": "json"}
        resp = self._safe_get(f"{self.naru_base_url}/libSrch", params)
        if not resp: return {}
        libs = resp.json().get("response", {}).get("libs", [])
        return {lib['lib']['libCode']: lib['lib']['libName'] for lib in libs}

    def fetch_new_books(self, query_type='ItemNewAll', max_results=50, category_id=0):
        all_items = []
        page_size = 50
        num_pages = (min(max_results, 1000) // page_size) + (1 if min(max_results, 1000) % page_size > 0 else 0)
        for page in range(1, num_pages + 1):
            params = {"ttbkey": self.aladin_ttb_key, "QueryType": query_type, "MaxResults": page_size, "start": page, "SearchTarget": "Book", "output": "js", "Version": "20131101", "OptRes": "stockStatus,categoryId,subInfo", "CategoryId": category_id}
            resp = self._safe_get(f"{self.aladin_base_url}/ItemList.aspx", params)
            if not resp: break
            try:
                data_str = resp.text.strip()
                if data_str.endswith(";"): data_str = data_str[:-1]
                items = json.loads(data_str).get("item", [])
                if not items: break
                all_items.extend(items)
                if len(all_items) >= max_results: break
            except: break
        return all_items[:max_results]

    def fetch_book_details(self, isbn13):
        params = {"ttbkey": self.aladin_ttb_key, "ItemId": self.clean_isbn(isbn13), "ItemIdType": "ISBN13", "output": "js", "Version": "20131101", "OptRes": "stockStatus,categoryId,subInfo"}
        resp = self._safe_get(f"{self.aladin_base_url}/ItemLookUp.aspx", params)
        if not resp: return None
        try:
            data_str = resp.text.strip()
            if data_str.endswith(";"): data_str = data_str[:-1]
            items = json.loads(data_str).get("item", [])
            return items[0] if items else None
        except: return None

    def analyze_category(self, item, user_mapping=None, naru_kdc=None):
        category_name = item.get('categoryName', '')
        sub_info = item.get('subInfo', {})
        actual_kdc = naru_kdc if naru_kdc else sub_info.get('kdc', '')

        target = "일반"
        if any(kw in category_name for kw in ["어린이", "유아", "전집", "초등", "그림책"]): target = "아동"
        elif "청소년" in category_name: target = "청소년"

        kdc_code = ""
        if actual_kdc:
            try:
                clean_kdc = re.sub(r'[^0-9.]', '', str(actual_kdc))
                if clean_kdc:
                    kdc_code = clean_kdc[:2].zfill(2)
                    if kdc_code.startswith('8'): kdc_code = '81'
            except: pass

        if not kdc_code:
            kdc_map = {
                "성공학": "19", "자기계발": "19", "처세": "19", "컴퓨터": "00", "철학": "10", 
                "심리학": "18", "불교": "22", "부처": "22", "스님": "22", "명상": "22",
                "경전": "22", "종교": "20", "기독교": "23", "경제": "32", "사회": "33", 
                "정치": "34", "법학": "36", "교육": "37", "수학": "41", "물리": "42", 
                "화학": "43", "생물": "47", "의학": "51", "한의학": "51", "공학": "53", 
                "요리": "59", "가정": "59", "예술": "60", "음악": "67", "운동": "69", 
                "한국어": "71", "영어": "74", "언어": "70", "한국문학": "81", "소설": "81", 
                "시/희곡": "81", "에세이": "81", "영미문학": "84", "역사": "90", "아시아": "91", 
                "유럽": "92", "지리": "98", "여행": "98"
            }
            if user_mapping: kdc_map.update(user_mapping)
            sorted_keys = sorted(kdc_map.keys(), key=len, reverse=True)
            kdc_code = "90"
            for part in reversed(category_name.split(">")):
                for key in sorted_keys:
                    if key in part: 
                        return target, kdc_map[key]
        return target, kdc_code
