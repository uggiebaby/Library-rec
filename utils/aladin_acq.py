import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import re
import os
import pandas as pd
from datetime import datetime, timedelta
import io
import concurrent.futures

class AladinAcquisitionManager:
    """
    알라딘 API 기반 도서관 신간/인기 도서 수서 지원 전용 매니저 (다중 카테고리 완벽 수집 지원)
    """
    
    # 10대 KDC 기본 분류 정의
    KDC_NAMES = {
        "0": "000 총류",
        "1": "100 철학",
        "2": "200 종교",
        "3": "300 사회과학",
        "4": "400 자연과학",
        "5": "500 기술과학",
        "6": "600 예술",
        "7": "700 언어",
        "8": "800 문학",
        "9": "900 역사"
    }

    # 기본 제외 키워드 ('세트' 포함)
    DEFAULT_EXCLUDE_KEYWORDS = [
        "수험서", "문제집", "자격증", "기출", "워크북", "참고서", "모의고사", 
        "그래픽노블", "라이트노벨", "컬러링북", "학습지", "잡지", "연속간행물", 
        "큰글자책", "큰글씨책", "대학교재", "토익", "토플", "오픽", "시험", "답안", "세트"
    ]

    # 알라딘 주요 분야 카테고리 CID
    ALADIN_CATEGORIES = {
        "전체 분야": 0,
        "소설/시/희곡": 1,
        "에세이": 55889,
        "인문학": 656,
        "사회과학": 798,
        "역사": 74,
        "과학/기술": 987,
        "경제경영": 170,
        "자기계발": 336,
        "예술/대중문화": 517,
        "종교/역학": 1237,
        "유아": 13789,
        "아동 (어린이)": 1108,
        "청소년": 1137,
        "국어/외국어": 1322,
        "컴퓨터/모바일": 351,
        "요리/살림": 53470,
        "건강/취미/실용": 55890,
        "여행": 1196
    }

    def __init__(self, aladin_ttb_key: str, info_naru_key: str = ""):
        self.aladin_ttb_key = aladin_ttb_key
        self.info_naru_key = info_naru_key
        self.aladin_base_url = "http://www.aladin.co.kr/ttb/api"
        self.naru_base_url = "http://data4library.kr/api"
        
        # 영구 디스크 캐시 파일 경로
        self.cache_dir = "data"
        self.owned_cache_path = os.path.join(self.cache_dir, "owned_cache.json")
        self.owned_cache = self._load_cache(self.owned_cache_path)
        
        # HTTP Session 최적화
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(pool_connections=100, pool_maxsize=200, max_retries=retries)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def _load_cache(self, path: str) -> dict:
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir, exist_ok=True)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_cache(self, data: dict, path: str):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def clean_isbn(self, isbn: str) -> str:
        """ISBN 하이픈 및 특수문자 제거"""
        if not isbn:
            return ""
        return re.sub(r'[^0-9X]', '', str(isbn).upper())

    def _safe_get(self, url: str, params: dict, timeout: int = 10):
        try:
            resp = self.session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception:
            return None

    def fetch_aladin_books_for_category(self, query_type: str, category_id: int, max_results: int) -> list:
        """단일 카테고리 ID 수집 헬퍼 함수 (병렬 처리용)"""
        page_size = 50
        target_count = min(max_results, 300)
        num_pages = (target_count // page_size) + (1 if target_count % page_size > 0 else 0)

        page_items = []

        def fetch_page(page_no):
            params = {
                "ttbkey": self.aladin_ttb_key,
                "QueryType": query_type,
                "MaxResults": page_size,
                "start": page_no,
                "SearchTarget": "Book",
                "output": "js",
                "Version": "20131101",
                "OptRes": "stockStatus,categoryId,subInfo",
                "CategoryId": category_id
            }
            resp = self._safe_get(f"{self.aladin_base_url}/ItemList.aspx", params)
            if not resp:
                return []
            try:
                data_str = resp.text.strip()
                if data_str.endswith(";"):
                    data_str = data_str[:-1]
                data = json.loads(data_str)
                return data.get("item", [])
            except Exception:
                return []

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(num_pages, 10)) as executor:
            futures = [executor.submit(fetch_page, p) for p in range(1, num_pages + 1)]
            for future in concurrent.futures.as_completed(futures):
                page_items.extend(future.result())

        return page_items

    def fetch_aladin_books_multi(self, query_type: str = "ItemNewSpecial", category_ids: list = None, max_results: int = 200) -> list:
        """
        [다중 카테고리 완벽 수집] 선택된 각 CID를 멀티스레딩 병렬 수집 후 고르게 교차 병합
        """
        if not self.aladin_ttb_key:
            return []
        
        # 0(전체 분야) 제외한 유효 CID 추출 (유효 CID가 있으면 0 제거)
        valid_cids = [cid for cid in (category_ids or []) if cid != 0]
        if not valid_cids:
            valid_cids = [0]

        # 카테고리별 목표 수집 권수 계산
        per_cat_limit = max_results if len(valid_cids) == 1 else max(50, max_results // len(valid_cids))

        cat_results = {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(valid_cids), 10)) as executor:
            future_to_cid = {
                executor.submit(self.fetch_aladin_books_for_category, query_type, cid, per_cat_limit): cid 
                for cid in valid_cids
            }
            for future in concurrent.futures.as_completed(future_to_cid):
                cid = future_to_cid[future]
                cat_results[cid] = future.result()

        # 각 카테고리 도서들을 순차적으로 교차 병합 (Round-Robin Interleaving)하여 중복 제거
        combined_items = []
        seen_isbns = set()

        max_len = max([len(items) for items in cat_results.values()]) if cat_results else 0
        for i in range(max_len):
            for cid in valid_cids:
                items = cat_results.get(cid, [])
                if i < len(items):
                    item = items[i]
                    isbn = self.clean_isbn(item.get("isbn13") or item.get("isbn"))
                    if isbn and isbn not in seen_isbns:
                        seen_isbns.add(isbn)
                        combined_items.append(item)

        return combined_items[:max_results]

    def infer_kdc(self, category_name: str, title: str = "", sub_kdc: str = "") -> tuple:
        """
        알라딘 카테고리명 및 제목 키워드를 분석하여 10대 KDC 코드 및 분류명 추정
        """
        if sub_kdc and str(sub_kdc).isdigit() and len(str(sub_kdc)) >= 1:
            code = str(sub_kdc)[0]
            if code in self.KDC_NAMES:
                return code, self.KDC_NAMES[code]

        cat = category_name or ""
        text = f"{cat} {title}"

        if any(k in text for k in ["철학", "심리", "윤리", "명상", "인간관계", "지혜"]):
            return "1", self.KDC_NAMES["1"]
        elif any(k in text for k in ["종교", "기독교", "불교", "천주교", "성경", "명상"]):
            return "2", self.KDC_NAMES["2"]
        elif any(k in text for k in ["사회과학", "경제", "경영", "정치", "사회", "법률", "교육", "국방", "트렌드", "재테크", "주식", "투자"]):
            return "3", self.KDC_NAMES["3"]
        elif any(k in text for k in ["자연과학", "수학", "물리", "화학", "천문", "지구", "생물", "우주", "뇌과학"]):
            return "4", self.KDC_NAMES["4"]
        elif any(k in text for k in ["기술과학", "의학", "건강", "컴퓨터", "IT", "인공지능", "코딩", "요리", "육아", "농업", "공학", "건축"]):
            return "5", self.KDC_NAMES["5"]
        elif any(k in text for k in ["예술", "미술", "음악", "체육", "스포츠", "연극", "영화", "사진", "디자인", "만화"]):
            return "6", self.KDC_NAMES["6"]
        elif any(k in text for k in ["언어", "국어", "영어", "외국어", "작문", "회화", "문법"]):
            return "7", self.KDC_NAMES["7"]
        elif any(k in text for k in ["소설", "시", "희곡", "에세이", "문학", "수필", "웹소설", "어린이", "유아", "동화"]):
            return "8", self.KDC_NAMES["8"]
        elif any(k in text for k in ["역사", "지리", "관광", "여행", "전기", "세계사", "한국사"]):
            return "9", self.KDC_NAMES["9"]
        elif any(k in text for k in ["총류", "백과사전", "문고", "일반", "정보학", "도서관"]):
            return "0", self.KDC_NAMES["0"]

        if "소설" in cat or "에세이" in cat or "유아" in cat or "어린이" in cat:
            return "8", self.KDC_NAMES["8"]

        return "0", self.KDC_NAMES["0"]

    def check_naru_book_exist(self, lib_code: str, isbn13: str) -> bool:
        """영구 디스크 캐시 기반 정보나루 bookExist API 연동"""
        if not self.info_naru_key or not lib_code or not isbn13:
            return False

        cache_key = f"{lib_code}_{isbn13}"
        if cache_key in self.owned_cache:
            return self.owned_cache[cache_key]

        params = {
            "authKey": self.info_naru_key,
            "libCode": lib_code,
            "isbn13": isbn13,
            "format": "json"
        }
        resp = self._safe_get(f"{self.naru_base_url}/bookExist", params, timeout=4)
        if not resp:
            return False

        try:
            has_book = resp.json().get("response", {}).get("result", {}).get("hasBook", "N") == "Y"
            self.owned_cache[cache_key] = has_book
            return has_book
        except Exception:
            return False

    def process_acquisition_candidates(
        self,
        raw_books: list,
        exclude_keywords: list = None,
        exclude_isbns: set = None,
        min_pub_date: str = None,
        discount_rate: float = 0.10,
        lib_code: str = "",
        owned_isbns_set: set = None,
        auto_exclude_owned: bool = True
    ) -> pd.DataFrame:
        """
        알라딘 수집 도서를 도서관 수서 정책에 따라 정제, 병렬 소장여부 조회, 예산 계산 후 DataFrame 반환
        """
        if exclude_keywords is None:
            exclude_keywords = self.DEFAULT_EXCLUDE_KEYWORDS
        if exclude_isbns is None:
            exclude_isbns = set()
        if owned_isbns_set is None:
            owned_isbns_set = set()

        processed = []
        seen_isbns = set()

        candidate_items = []
        for item in raw_books:
            isbn13 = self.clean_isbn(item.get("isbn13") or item.get("isbn"))
            if not isbn13 or isbn13 in seen_isbns or isbn13 in exclude_isbns:
                continue

            title = item.get("title", "").strip()
            stock_status = item.get("stockStatus", "")
            category_name = item.get("categoryName", "")
            pub_date = item.get("pubDate", "")

            if "품절" in stock_status or "절판" in stock_status:
                continue

            full_text = f"{title} {category_name}"
            if any(kw in full_text for kw in exclude_keywords if kw.strip()):
                continue

            if min_pub_date and pub_date and pub_date < min_pub_date:
                continue

            seen_isbns.add(isbn13)
            candidate_items.append((isbn13, item))

        ownership_map = {}
        if self.info_naru_key and lib_code:
            def check_item(isbn):
                if isbn in owned_isbns_set:
                    return isbn, True
                return isbn, self.check_naru_book_exist(lib_code, isbn)

            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                futures = [executor.submit(check_item, isbn) for isbn, _ in candidate_items]
                for future in concurrent.futures.as_completed(futures):
                    isbn, is_owned = future.result()
                    ownership_map[isbn] = is_owned

            self._save_cache(self.owned_cache, self.owned_cache_path)
        else:
            for isbn, _ in candidate_items:
                ownership_map[isbn] = isbn in owned_isbns_set

        for isbn13, item in candidate_items:
            is_owned = ownership_map.get(isbn13, False)
            if is_owned and auto_exclude_owned:
                continue

            title = item.get("title", "").strip()
            author = item.get("author", "").strip()
            publisher = item.get("publisher", "").strip()
            pub_date = item.get("pubDate", "")
            category_name = item.get("categoryName", "")
            sales_point = int(item.get("salesPoint", 0))
            price_standard = int(item.get("priceStandard", 0))
            estimated_price = int(price_standard * (1.0 - discount_rate))

            sub_kdc = item.get("subInfo", {}).get("kdc", "")
            kdc_code, kdc_name = self.infer_kdc(category_name, title, sub_kdc)

            processed.append({
                "선정": not is_owned,
                "소장여부": "소장중" if is_owned else "미소장",
                "KDC코드": kdc_code,
                "KDC분류": kdc_name,
                "도서명": title,
                "저자": author,
                "출판사": publisher,
                "출간일": pub_date,
                "정가": price_standard,
                "추정실구입가": estimated_price,
                "판매지수": sales_point,
                "ISBN13": isbn13,
                "카테고리": category_name,
                "알라딘URL": item.get("link", "")
            })

        df = pd.DataFrame(processed)
        if not df.empty:
            df = df.sort_values(by="판매지수", ascending=False).reset_index(drop=True)

        return df

    def update_ownership_status(self, df: pd.DataFrame, lib_code: str, owned_isbns_set: set = None) -> pd.DataFrame:
        """기존 DataFrame 후보 도서들에 대해 정보나루 API 및 로컬 DB를 병렬 조회하여 소장여부 및 선정 유무를 실시간 업데이트"""
        if df.empty:
            return df
        if owned_isbns_set is None:
            owned_isbns_set = set()

        df_copy = df.copy()
        isbns = df_copy["ISBN13"].tolist()

        ownership_results = {}

        def check_single_isbn(isbn):
            if isbn in owned_isbns_set:
                return isbn, True
            if self.info_naru_key and lib_code:
                return isbn, self.check_naru_book_exist(lib_code, isbn)
            return isbn, False

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(check_single_isbn, isbn) for isbn in isbns]
            for future in concurrent.futures.as_completed(futures):
                isbn, owned = future.result()
                ownership_results[isbn] = owned

        self._save_cache(self.owned_cache, self.owned_cache_path)

        new_ownership = []
        new_selected = []

        for idx, row in df_copy.iterrows():
            isbn = row["ISBN13"]
            is_owned = ownership_results.get(isbn, False)
            new_ownership.append("소장중" if is_owned else "미소장")
            
            if is_owned:
                new_selected.append(False)
            else:
                new_selected.append(row["선정"])

        df_copy["소장여부"] = new_ownership
        df_copy["선정"] = new_selected
        return df_copy

    def apply_budget_cap(self, df: pd.DataFrame, max_budget: int = 0, max_books: int = 0) -> pd.DataFrame:
        """예산액 및 목표 권수 제한에 맞게 도서 선정 유무(선정 컬럼) 자동 업데이트"""
        if df.empty:
            return df

        df_copy = df.copy()
        accumulated_cost = 0
        selected_count = 0

        new_selected = []

        for idx, row in df_copy.iterrows():
            if row.get("소장여부") == "소장중":
                new_selected.append(False)
                continue

            cost = row["추정실구입가"]
            is_valid = True

            if max_books > 0 and selected_count >= max_books:
                is_valid = False

            if max_budget > 0 and (accumulated_cost + cost) > max_budget:
                is_valid = False

            if is_valid:
                new_selected.append(True)
                accumulated_cost += cost
                selected_count += 1
            else:
                new_selected.append(False)

        df_copy["선정"] = new_selected
        return df_copy

    def create_excel_order_sheet(self, df: pd.DataFrame) -> bytes:
        """수서 선정 도서를 발주용 Excel 파일로 변환하여 bytes 반환"""
        output = io.BytesIO()

        selected_df = df[df["선정"] == True].copy() if "선정" in df.columns else df.copy()

        cols_to_export = [
            "KDC코드", "KDC분류", "도서명", "저자", "출판사", "출간일", 
            "정가", "추정실구입가", "소장여부", "ISBN13", "판매지수", "카테고리"
        ]
        available_cols = [c for c in cols_to_export if c in selected_df.columns]
        export_df = selected_df[available_cols]

        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            export_df.to_excel(writer, index=False, sheet_name="신간도서_발주목록")

            if "KDC분류" in selected_df.columns and not selected_df.empty:
                kdc_summary = selected_df.groupby("KDC분류").agg(
                    권수=("도서명", "count"),
                    총정가=("정가", "sum"),
                    총실구입가=("추정실구입가", "sum")
                ).reset_index()
                kdc_summary.to_excel(writer, index=False, sheet_name="KDC분류별통계")

        output.seek(0)
        return output.getvalue()
