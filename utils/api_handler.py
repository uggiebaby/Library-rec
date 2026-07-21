import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import re
import os
from datetime import datetime

class LibraryAPIHandler:
    # --- 공통 상수 관리 ---
    REGION_OPTIONS = {"전체": "", "서울": "11", "경기": "31", "광주": "24", "부산": "21", "인천": "23", "대구": "22", "경기도교육청": "GOE"}
    GOE_LIBS = {
        "141004": "과천", "141141": "경기평생", "141010": "중앙", "141008": "성남", 
        "141130": "화성", "141106": "의정부", "141009": "평택", "141006": "김포", 
        "141017": "포천", "141012": "여주가남"
    }
    DEFAULT_EXCLUSIONS = ["수험서", "문제집", "자격증", "기출", "워크북", "그래픽노블", "라이트노벨", "판타지소설", "무협소설", "컬러링북", "학습지", "잡지", "연속간행물", "큰글자책", "큰글씨책", "모의고사", "참고서", "대학교재", "토익", "토플", "오픽"]
    KDC_BASE_NAMES = {"0": "000 총류", "1": "100 철학", "2": "200 종교", "3": "300 사회과학", "4": "400 자연과학", "5": "500 기술과학", "6": "600 예술", "7": "700 언어", "8": "800 문학", "9": "900 역사"}
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
        
        # 성능 최적화를 위한 영구 로컬 캐시 설정
        self.cache_path = "data/kdc_cache.json"
        self.owned_cache_path = "data/owned_cache.json"
        self.bench_cache_path = "data/bench_cache.json"
        self.my_collection_path = "data/my_collection.json"
        
        self.kdc_cache = self._load_cache(self.cache_path)
        self.owned_cache = self._load_cache(self.owned_cache_path)
        self.bench_cache = self._load_cache(self.bench_cache_path)
        
        # 로컬 장서 DB 로드 (ISBN 세트로 변환)
        coll_data = self._load_cache(self.my_collection_path)
        self.my_collection = set(coll_data.keys())
        
        # 병렬 처리를 위한 고성능 HTTP 세션 초기화
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(pool_connections=200, pool_maxsize=300, max_retries=retries)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def _load_cache(self, path):
        if not os.path.exists("data"): os.makedirs("data", exist_ok=True)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except: return {}
        return {}

    def _save_cache(self, data, path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except: pass

    def _safe_get(self, url, params, timeout=10):
        try:
            resp = self.session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except: return None

    def clean_isbn(self, isbn):
        """ISBN에서 숫자만 추출 (하이픈 제거 등)"""
        return re.sub(r'[^0-9X]', '', str(isbn))

    def fetch_popular_books(self, page_size=500, start_dt=None, end_dt=None):
        params = {"authKey": self.info_naru_key, "format": "json", "pageSize": page_size}
        if start_dt: params["startDt"] = start_dt
        if end_dt: params["endDt"] = end_dt
        resp = self._safe_get(f"{self.naru_base_url}/loanItemSrch", params)
        return resp.json().get("response", {}).get("docs", []) if resp else []

    def check_book_existence(self, lib_code, isbn13):
        isbn = self.clean_isbn(isbn13)
        # 1순위: 로컬 전체 장서 DB 확인 (가장 빠름)
        if isbn in self.my_collection: return "Y"
        # 2순위: 개별 조회 캐시 확인
        cache_key = f"{lib_code}_{isbn}"
        if cache_key in self.owned_cache: return self.owned_cache[cache_key]
        
        params = {"authKey": self.info_naru_key, "libCode": lib_code, "isbn13": isbn, "format": "json"}
        resp = self._safe_get(f"{self.naru_base_url}/bookExist", params, timeout=5)
        res = resp.json().get("response", {}).get("result", {}).get("hasBook", "N") if resp else "N"
        self.owned_cache[cache_key] = res
        self._save_cache(self.owned_cache, self.owned_cache_path)
        return res

    def sync_my_collection(self, lib_code, start_dt, progress_callback=None):
        """우리 도서관의 장서 목록을 정보나루에서 가져와 로컬 DB 업데이트 (고속 병렬 수집)"""
        import concurrent.futures
        
        if not os.path.exists("data"): os.makedirs("data", exist_ok=True)
        current_db = self._load_cache(self.my_collection_path)
        
        # 1. 전체 개수 파악을 위한 1차 호출
        params = {
            "authKey": self.info_naru_key, "libCode": lib_code,
            "startDt": start_dt, "endDt": datetime.now().strftime("%Y-%m-%d"),
            "pageNo": 1, "pageSize": "1", "format": "json"
        }
        resp = self._safe_get(f"{self.naru_base_url}/itemSrch", params, timeout=20)
        num_found = 0
        if resp:
            try: num_found = int(resp.json().get("response", {}).get("numFound", 0))
            except: pass
        
        if num_found == 0: return 0

        # 2. 병렬 수집 계획 (페이지당 200권)
        page_size = 200
        total_pages = (num_found // page_size) + (1 if num_found % page_size > 0 else 0)
        total_pages = min(total_pages, 500) # 최대 10만권 안전장치
        
        new_isbns = {}
        processed_pages = 0
        
        def fetch_page(page_no):
            p = {
                "authKey": self.info_naru_key, "libCode": lib_code,
                "startDt": start_dt, "endDt": datetime.now().strftime("%Y-%m-%d"),
                "pageNo": page_no, "pageSize": str(page_size), "format": "json"
            }
            r = self._safe_get(f"{self.naru_base_url}/itemSrch", p, timeout=30)
            if not r: return []
            try:
                return r.json().get("response", {}).get("docs", [])
            except: return []

        # 최대 10개의 쓰레드로 병렬 호출
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_page = {executor.submit(fetch_page, p): p for p in range(1, total_pages + 1)}
            for future in concurrent.futures.as_completed(future_to_page):
                docs = future.result()
                for d in docs:
                    item_doc = d['doc']
                    isbn = self.clean_isbn(item_doc.get('isbn13'))
                    reg_date = item_doc.get('reg_date', datetime.now().strftime("%Y-%m-%d"))
                    if isbn:
                        new_isbns[isbn] = reg_date
                        self.my_collection.add(isbn)
                
                processed_pages += 1
                if progress_callback:
                    # 실시간 진행률 업데이트
                    progress_callback(len(new_isbns), num_found)

        # 3. 최종 저장 및 메모리 갱신
        current_db.update(new_isbns)
        self._save_cache(current_db, self.my_collection_path)
        self.my_collection = set(current_db.keys())
        return len(new_isbns)

    def fetch_library_new_additions(self, lib_code, start_dt, end_dt, page_size=1000):
        """특정 도서관의 신규 등록 도서 목록 조회 (로컬 캐시 적용 및 데이터 확보 강화)"""
        cache_key = f"{lib_code}_{start_dt}_{end_dt}"
        if cache_key in self.bench_cache: return self.bench_cache[cache_key]

        params = {"authKey": self.info_naru_key, "libCode": lib_code, "type": "ALL", "startDt": start_dt, "endDt": end_dt, "pageSize": "200", "format": "json"}
        resp = self._safe_get(f"{self.naru_base_url}/itemSrch", params, timeout=20)
        
        if not resp: return {}
        docs = resp.json().get("response", {}).get("docs", [])
        result = {}
        for d in docs:
            item = d['doc']
            isbn = self.clean_isbn(item.get('isbn13'))
            if not isbn: continue
            
            shelf_name = ""
            call_nums = item.get('callNumbers', [])
            if call_nums and isinstance(call_nums, list):
                try: shelf_name = call_nums[0].get('callNumber', {}).get('separate_shelf_name', '')
                except: pass

            result[isbn] = {
                "title": item.get('bookname'), "author": item.get('authors'), "publisher": item.get('publisher'),
                "pubDate": item.get('publication_year', '1900'), "kdc": item.get('class_no', ''),
                "shelf_name": shelf_name, "addition_symbol": item.get('addition_symbol', ''),
                "loan_count": int(item.get('loan_count', 0)) if item.get('loan_count') is not None else 0
            }
        
        self.bench_cache[cache_key] = result
        if result: self._save_cache(self.bench_cache, self.bench_cache_path)
        return result

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

    def bulk_fetch_book_details(self, isbn_list):
        if not isbn_list: return []
        clean_isbns = [self.clean_isbn(i) for i in isbn_list if i]
        params = {"ttbkey": self.aladin_ttb_key, "ItemId": ";".join(clean_isbns), "ItemIdType": "ISBN13", "output": "js", "Version": "20131101", "OptRes": "stockStatus,categoryId,subInfo"}
        resp = self._safe_get(f"{self.aladin_base_url}/ItemLookUp.aspx", params)
        if not resp: return []
        try:
            data_str = resp.text.strip()
            if data_str.endswith(";"): data_str = data_str[:-1]
            return json.loads(data_str).get("item", [])
        except: return []

    def analyze_category(self, item, user_mapping=None, naru_kdc=None, shelf_name=None):
        category_name = item.get('categoryName', '')
        sub_info = item.get('subInfo', {})
        actual_kdc = naru_kdc if naru_kdc else sub_info.get('kdc', '')
        add_symbol = str(item.get('addition_symbol', ''))

        # --- 1순위: 별치기호 기반 대상 판정 ---
        target = "일반"
        if shelf_name:
            if any(kw in shelf_name for kw in ['청', '청소년']): target = "청소년"
            elif any(kw in shelf_name for kw in ['C', '아']): target = "아동"
            elif any(kw in shelf_name for kw in ['B', '유']): target = "유아"
        
        # --- 2순위: 부가기호 기반 대상 판정 ---
        if target == "일반":
            if add_symbol.startswith('4'): target = "청소년"
            elif add_symbol.startswith('7'): target = "아동"
            elif add_symbol.startswith('5'): target = "아동" 

        # --- 3순위: 알라딘 카테고리 기반 대상 판정 ---
        if target == "일반":
            if any(kw in category_name for kw in ["어린이", "유아", "전집", "초등", "그림책"]): target = "아동"
            elif "청소년" in category_name: target = "청소년"

        kdc_code = ""
        if actual_kdc:
            try:
                clean_kdc = re.sub(r'[^0-9.]', '', str(actual_kdc))
                if clean_kdc:
                    kdc_code = clean_kdc.split('.')[0]
                    if len(kdc_code) < 3: kdc_code = kdc_code.ljust(3, '0')
            except: pass

        if not kdc_code:
            kdc_map = {
                # 000 총류
                "총류": "000", "컴퓨터": "004", "IT": "004", "프로그래밍": "004", "데이터베이스": "004", "인터넷": "004",
                "인공지능": "004", "빅데이터": "004", "도서관": "020", "문헌정보": "020", "백과사전": "030", 
                "신문": "070", "언론": "070", "학회": "060", "서지": "010", "강연집": "040", "연속간행물": "050",
                # 100 철학/심리학
                "철학": "100", "형이상학": "110", "인식론": "120", "논리학": "170", "심리학": "180", 
                "심리": "180", "윤리학": "190", "도덕": "190", "처세": "199", "자기계발": "199", 
                "명상": "189", "성공": "199", "행복": "199", "동양철학": "150", "서양철학": "160", 
                "인문학": "100", "인문": "100", "정신분석": "180",
                # 200 종교
                "종교": "200", "불교": "220", "기독교": "230", "천주교": "230", "가톨릭": "230", 
                "성경": "230", "신학": "230", "이슬람": "250", "힌두교": "260", "신화": "219", "비교종교": "210",
                # 300 사회과학
                "사회과학": "300", "사회": "300", "통계": "310", "경제": "320", "경영": "325", "주식": "327", 
                "투자": "327", "재테크": "327", "무역": "326", "사회학": "330", "사회문제": "330", 
                "정치": "340", "외교": "340", "행정": "350", "법학": "360", "교육": "370", 
                "특수교육": "377", "사회복지": "380", "노동": "321", "민속": "390", "풍속": "390", "군사": "390",
                # 400 자연과학
                "자연과학": "400", "과학": "400", "기초과학": "400", "교양과학": "400", "수학": "410", 
                "물리학": "420", "물리": "420", "화학": "430", "천문학": "440", "천문": "440", 
                "우주": "440", "지구과학": "450", "기상": "450", "지질": "450", "생물학": "470", 
                "생물": "470", "생명과학": "470", "식물": "480", "동물": "490", "해양": "450",
                # 500 기술과학
                "기술과학": "500", "기술": "500", "의학": "510", "간호": "510", "건강": "511", "약학": "510", 
                "농업": "520", "원예": "520", "공학": "530", "기계": "530", "전기": "540", 
                "전자": "550", "통신": "560", "화학공학": "570", "식품": "570", "제조업": "580", 
                "가정학": "590", "요리": "594", "육아": "598", "임신": "598", "출산": "598", "부모": "598", "인테리어": "591", "패션": "592",
                # 600 예술
                "예술": "600", "예능": "600", "대중문화": "600", "건축": "610", "조각": "620", "공예": "630", 
                "서예": "640", "회화": "650", "그림": "650", "디자인": "650", "사진": "660", "음악": "670", 
                "공연예술": "680", "영화": "688", "드라마": "688", "시나리오": "680", "작법": "600", 
                "스포츠": "690", "오락": "690", "체육": "690", "만화": "657", "애니메이션": "657",
                # 700 언어
                "언어": "700", "한국어": "710", "국어": "710", "중국어": "720", "일본어": "730", 
                "영어": "740", "독일어": "750", "프랑스어": "760", "스페인어": "770", "이탈리아어": "780",
                # 800 문학
                "문학": "800", "한국문학": "810", "한국소설": "813", "한국시": "811", "한국희곡": "812", 
                "한국수필": "814", "중국문학": "820", "중국소설": "823", "일본문학": "830", "일본소설": "833", 
                "영미문학": "840", "영미소설": "843", "미국소설": "843", "영국소설": "843",
                "독일문학": "850", "독일소설": "853", "프랑스문학": "860", "프랑스소설": "863", 
                "스페인문학": "870", "스페인소설": "873", "러시아문학": "890", "러시아소설": "893",
                "소설": "813", "시": "811", "희곡": "812", "수필": "814", "에세이": "814",
                "추리소설": "813", "미스터리소설": "813", "판타지소설": "813", "SF소설": "813", 
                "무협소설": "813", "로맨스소설": "813",
                # 900 역사
                "역사": "900", "아시아": "910", "한국사": "911", "조선사": "911", "조선시대": "911", 
                "중국사": "920", "일본사": "930", "유럽사": "940", "아프리카": "950", "북아메리카": "960", 
                "남아메리카": "970", "오세아니아": "960", "지리": "980", "여행": "980", "전기": "990", "인물": "990"
            }
            if user_mapping: kdc_map.update(user_mapping)
            sorted_keys = sorted(kdc_map.keys(), key=len, reverse=True)
            temp_kdc = "000" 
            for part in category_name.split(">"):
                for key in sorted_keys:
                    if key in part: 
                        new_kdc = kdc_map[key]
                        if temp_kdc.startswith('8') and new_kdc == '813':
                            if temp_kdc not in ['813', '800']: temp_kdc = temp_kdc[:2] + '3'
                            else: temp_kdc = new_kdc
                        elif (temp_kdc == '813' or temp_kdc == '000') and new_kdc.startswith('8') and new_kdc != '813':
                            temp_kdc = new_kdc[:2] + '3'
                        else: temp_kdc = new_kdc
                        break 
            return target, temp_kdc
        return target, kdc_code
