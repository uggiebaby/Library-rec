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

class Yes24AcquisitionManager:
    """
    YES24 Open API (apis.yes24.com/v1) 기반 도서관 신간/인기 도서 수서 지원 전용 매니저
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

    # 기본 제외 키워드
    DEFAULT_EXCLUDE_KEYWORDS = [
        "수험서", "문제집", "자격증", "기출", "워크북", "참고서", "모의고사", 
        "그래픽노블", "라이트노벨", "컬러링북", "학습지", "잡지", "연속간행물", 
        "큰글자책", "큰글씨책", "대학교재", "토익", "토플", "오픽", "시험", "답안", "세트"
    ]

    # 예스24 국내도서(001) 주요 분야 카테고리 ID
    YES24_CATEGORIES = {
        "전체 분야": "001",
        "소설/시/희곡": "001001046",
        "에세이": "001001047",
        "인문": "001001019",
        "사회 정치": "001001022",
        "역사": "001001010",
        "자연과학": "001001002",
        "경제 경영": "001001025",
        "자기계발": "001001026",
        "IT 모바일": "001001003",
        "예술": "001001007",
        "종교": "001001021",
        "유아": "001001027",
        "어린이": "001001016",
        "청소년": "001001005",
        "가정 살림": "001001001",
        "건강 취미": "001001011",
        "여행": "001001009",
        "국어 외국어 사전": "001001004",
        "인물": "001001020"
    }

    def __init__(self, yes24_api_key: str, info_naru_key: str = ""):
        self.yes24_api_key = yes24_api_key
        self.info_naru_key = info_naru_key
        self.yes24_base_url = "https://apis.yes24.com/v1"
        self.naru_base_url = "http://data4library.kr/api"
        
        # 영구 디스크 캐시 파일 경로
        self.cache_dir = "data"
        self.owned_cache_path = os.path.join(self.cache_dir, "owned_cache.json")
        self.owned_cache = self._load_cache(self.owned_cache_path)

        # HTTP 세션 및 재시도 설정
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(pool_connections=50, pool_maxsize=100, max_retries=retries)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

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
        if not isbn:
            return ""
        return re.sub(r"[^0-9X]", "", str(isbn).strip().upper())

    def format_pub_date(self, raw_date: str) -> str:
        """예스24 publishDate (YYYYMMDD 또는 YYYY-MM-DD) -> YYYY-MM-DD 변환"""
        if not raw_date:
            return ""
        d_str = str(raw_date).strip()
        if len(d_str) == 8 and d_str.isdigit():
            return f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]}"
        return d_str

    def _safe_get(self, url: str, headers: dict = None, params: dict = None, timeout: int = 10):
        try:
            resp = self.session.get(url, headers=headers, params=params, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception:
            return None

    def fetch_yes24_books_for_category(self, query_type: str, category_id: str, max_results: int) -> list:
        """
        단일 카테고리에 대해 지정된 수량만큼 YES24 API 도서 수집
        """
        headers = {
            "X-Api-Key": self.yes24_api_key,
            "Accept": "application/json"
        }
        
        books = []
        page_size = min(max_results, 50)
        max_pages = (max_results // page_size) + (1 if max_results % page_size != 0 else 0)

        # 쿼리 유형별 엔드포인트 및 파라미터 매핑
        if query_type == "BestsellerRealtime":
            # 실시간 베스트셀러는 한번에 최대 100권 반환 (detail=Y 로 실제 판매지수 포함)
            params = {"categoryId": category_id, "pageSize": min(max_results, 100), "detail": "Y"}
            data = self._safe_get(f"{self.yes24_base_url}/category/bestsellerRealtime", headers=headers, params=params)
            if data and data.get("success"):
                return data.get("data", {}).get("items", [])[:max_results]
            return []

        elif query_type == "BestsellerMonthly":
            endpoint = f"{self.yes24_base_url}/category/bestsellerMonthly"
        elif query_type == "NewBooksRecent":
            # 신간/최근 등록 도서 검색
            endpoint = f"{self.yes24_base_url}/goods/itemList"
        else:
            # 기본 종합 베스트셀러
            endpoint = f"{self.yes24_base_url}/category/bestseller"

        def fetch_page(p_num):
            if query_type == "NewBooksRecent":
                # 최신순 도서 검색 (detail=Y 로 판매지수 포함)
                params = {
                    "query": "*",
                    "category": "BOOK",
                    "categoryId": category_id,
                    "sort": "RECENT",
                    "page": p_num,
                    "pageSize": page_size,
                    "detail": "Y"
                }
            else:
                params = {
                    "categoryId": category_id,
                    "page": p_num,
                    "pageSize": page_size,
                    "detail": "Y"
                }
            resp_data = self._safe_get(endpoint, headers=headers, params=params)
            if resp_data and resp_data.get("success"):
                return resp_data.get("data", {}).get("items", [])
            return []

        # 병렬 페이지 수집
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_page = {executor.submit(fetch_page, p): p for p in range(1, max_pages + 1)}
            for future in concurrent.futures.as_completed(future_to_page):
                try:
                    res = future.result()
                    if res:
                        books.extend(res)
                except Exception:
                    pass

        return books[:max_results]

    def fetch_yes24_books_multi(self, query_type: str = "Bestseller", category_ids: list = None, max_results: int = 200) -> list:
        """
        여러 카테고리 분야를 병렬로 동시 수집하여 균형있게 교차 병합 (Round-Robin)
        """
        if not category_ids:
            category_ids = ["001"] # 전체 분야

        # 전체 분야 선택 시
        if "001" in category_ids:
            return self.fetch_yes24_books_for_category(query_type, "001", max_results)

        valid_cids = list(dict.fromkeys(category_ids))
        per_cat_limit = max(30, (max_results // len(valid_cids)) + 20)

        cat_results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(valid_cids), 8)) as executor:
            future_to_cid = {
                executor.submit(self.fetch_yes24_books_for_category, query_type, cid, per_cat_limit): cid
                for cid in valid_cids
            }
            for future in concurrent.futures.as_completed(future_to_cid):
                cid = future_to_cid[future]
                cat_results[cid] = future.result()

        # 라운드로빈 교차 병합 및 ISBN 중복 제거
        combined_items = []
        seen_isbns = set()

        max_len = max([len(items) for items in cat_results.values()]) if cat_results else 0
        for i in range(max_len):
            for cid in valid_cids:
                items = cat_results.get(cid, [])
                if i < len(items):
                    item = items[i]
                    isbn = self.clean_isbn(item.get("isbn13") or item.get("isbn10"))
                    if isbn and isbn not in seen_isbns:
                        seen_isbns.add(isbn)
                        combined_items.append(item)

        return combined_items[:max_results]

    def infer_kdc(self, category_name: str, title: str = "", goods_sort_nm: str = "") -> tuple:
        """
        예스24 카테고리명 및 제목 키워드를 분석하여 10대 KDC 코드 및 분류명 추정
        """
        text = f"{category_name} {goods_sort_nm} {title}"

        if any(k in text for k in ["철학", "심리", "윤리", "명상", "인간관계", "지혜", "사상"]):
            return "1", self.KDC_NAMES["1"]
        elif any(k in text for k in ["종교", "기독교", "불교", "천주교", "성경", "교회"]):
            return "2", self.KDC_NAMES["2"]
        elif any(k in text for k in ["사회", "정치", "경제", "경영", "법률", "교육", "국방", "트렌드", "재테크", "주식", "투자", "부동산", "사회복지"]):
            return "3", self.KDC_NAMES["3"]
        elif any(k in text for k in ["자연과학", "수학", "물리", "화학", "천문", "지구", "생물", "우주", "뇌과학", "생명과학"]):
            return "4", self.KDC_NAMES["4"]
        elif any(k in text for k in ["기술과학", "의학", "건강", "컴퓨터", "IT", "인공지능", "코딩", "모바일", "요리", "육아", "농업", "공학", "건축"]):
            return "5", self.KDC_NAMES["5"]
        elif any(k in text for k in ["예술", "미술", "음악", "체육", "스포츠", "연극", "영화", "사진", "디자인", "만화"]):
            return "6", self.KDC_NAMES["6"]
        elif any(k in text for k in ["언어", "국어", "영어", "외국어", "작문", "회화", "문법", "사전"]):
            return "7", self.KDC_NAMES["7"]
        elif any(k in text for k in ["소설", "시", "희곡", "에세이", "문학", "수필", "웹소설", "어린이", "유아", "동화", "그림책"]):
            return "8", self.KDC_NAMES["8"]
        elif any(k in text for k in ["역사", "지리", "관광", "여행", "전기", "세계사", "한국사", "인물"]):
            return "9", self.KDC_NAMES["9"]
        elif any(k in text for k in ["총류", "백과사전", "문고", "일반", "정보학", "도서관", "독서"]):
            return "0", self.KDC_NAMES["0"]

        if any(k in text for k in ["소설", "에세이", "유아", "어린이"]):
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
        try:
            resp = self.session.get(f"{self.naru_base_url}/bookExist", params=params, timeout=4)
            if resp.status_code == 200:
                data = resp.json()
                has_book = data.get("response", {}).get("result", {}).get("hasBook", "N")
                is_owned = (has_book == "Y")
                self.owned_cache[cache_key] = is_owned
                return is_owned
        except Exception:
            pass

        return False

    def process_acquisition_candidates(
        self,
        raw_books: list,
        exclude_keywords: list = None,
        exclude_isbns: set = None,
        min_pub_date: str = "",
        discount_rate: float = 0.10,
        lib_code: str = "",
        owned_isbns_set: set = None,
        auto_exclude_owned: bool = True,
        target_loan_map: dict = None,
        trend_loan_map: dict = None,
        eui_weights: dict = None,
        target_label: str = "",
        allow_steady_sellers: bool = True
    ) -> pd.DataFrame:
        """
        수집된 예스24 원본 도서 목록을 필터링, KDC 분류, 소장 여부 확인 및 v2.0 이용기대율(EUI) 점수 계산을 거쳐 DataFrame으로 정제
        """
        if exclude_keywords is None:
            exclude_keywords = self.DEFAULT_EXCLUDE_KEYWORDS
        if exclude_isbns is None:
            exclude_isbns = set()
        if owned_isbns_set is None:
            owned_isbns_set = set()
        if target_loan_map is None:
            target_loan_map = {}
        if trend_loan_map is None:
            trend_loan_map = {}
        if eui_weights is None:
            eui_weights = {"sales": 0.35, "loans": 0.25, "target": 0.25, "trend": 0.15}

        processed = []
        seen_isbns = set()
        candidate_items = []

        for item in raw_books:
            isbn13 = self.clean_isbn(item.get("isbn13") or item.get("isbn10"))
            if not isbn13:
                continue

            # 중복 제거
            if isbn13 in seen_isbns or isbn13 in exclude_isbns:
                continue

            title = item.get("title", "").strip()
            goods_sort_nm = item.get("goodsSortNm", "")
            raw_pub_date = item.get("publishDate", "")
            pub_date = self.format_pub_date(raw_pub_date)
            sales_point = int(item.get("salePoint") or 0)

            # 키워드 필터링
            full_text = f"{title} {goods_sort_nm}"
            if any(k.lower() in full_text.lower() for k in exclude_keywords):
                continue

            # 품절/절판 도서 필터링
            item_status = item.get("itemStatus", "판매중")
            if item_status not in ["판매중", "예약판매", ""]:
                continue

            # 출간일 조건 체크 (단, 판매지수/대출율 우수 도서는 스테디셀러 예외 허용)
            is_high_perf = (
                sales_point >= 50000 or
                isbn13 in target_loan_map or
                isbn13 in trend_loan_map
            )
            is_older_than_limit = False
            if min_pub_date and pub_date and pub_date < min_pub_date:
                if allow_steady_sellers and is_high_perf:
                    is_older_than_limit = True
                else:
                    continue

            seen_isbns.add(isbn13)
            candidate_items.append((isbn13, item, pub_date, is_older_than_limit))

        # 정보나루 소장 여부 병렬 확인
        ownership_map = {}
        if self.info_naru_key and lib_code:
            def check_item(isbn):
                if isbn in owned_isbns_set:
                    return isbn, True
                return isbn, self.check_naru_book_exist(lib_code, isbn)

            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                futures = [executor.submit(check_item, isbn) for isbn, _, _, _ in candidate_items]
                for future in concurrent.futures.as_completed(futures):
                    isbn, is_owned = future.result()
                    ownership_map[isbn] = is_owned

            self._save_cache(self.owned_cache, self.owned_cache_path)
        else:
            for isbn, _, _, _ in candidate_items:
                ownership_map[isbn] = (isbn in owned_isbns_set)

        for rank_idx, (isbn13, item, pub_date, is_older_than_limit) in enumerate(candidate_items, start=1):
            is_owned = ownership_map.get(isbn13, False)
            if is_owned and auto_exclude_owned:
                continue

            title = item.get("title", "").strip()
            author = item.get("author", "").strip()
            publisher = item.get("publisher", "").strip()
            goods_sort_nm = item.get("goodsSortNm", "")
            category_name = goods_sort_nm.replace("국내도서-", "") if goods_sort_nm else ""

            # 가격 처리 (shopPrice가 정가, salePrice가 판매가)
            price_standard = int(float(item.get("shopPrice") or 0))
            if price_standard <= 0:
                price_standard = int(float(item.get("salePrice") or 0))

            estimated_price = int(price_standard * (1.0 - discount_rate))

            # 예스24 실제 판매지수(salePoint) 추출
            raw_sale_point = item.get("salePoint")
            sort_order = item.get("sortOrder") or rank_idx
            if raw_sale_point is not None and int(raw_sale_point) > 0:
                sales_point = int(raw_sale_point)
            else:
                sales_point = max(100, 100000 - (sort_order * 1000))

            kdc_code, kdc_name = self.infer_kdc(category_name, title, goods_sort_nm)

            # --- v2.0 이용기대율(EUI: Expected Utilization Index) 점수 계산 ---
            norm_sales = min(sales_point / 50000.0, 1.0) * 100.0

            target_info = target_loan_map.get(isbn13, {})
            target_rank = target_info.get("ranking", 0)
            target_loan_cnt = target_info.get("loan_count", 0)
            target_score = max(0.0, (1.0 - (target_rank / 300.0)) * 100.0) if target_rank > 0 else 0.0

            trend_info = trend_loan_map.get(isbn13, {})
            trend_rank = trend_info.get("ranking", 0)
            trend_score = max(0.0, (1.0 - (trend_rank / 200.0)) * 100.0) if trend_rank > 0 else 0.0

            norm_loans = min(target_loan_cnt / 100.0, 1.0) * 100.0 if target_loan_cnt > 0 else (norm_sales * 0.5)

            w_s = eui_weights.get("sales", 0.35)
            w_l = eui_weights.get("loans", 0.25)
            w_ta = eui_weights.get("target", 0.25)
            w_tr = eui_weights.get("trend", 0.15)

            eui_score = round(
                (w_s * norm_sales) + 
                (w_l * norm_loans) + 
                (w_ta * target_score) + 
                (w_tr * trend_score), 
                1
            )

            # 추천 사유 뱃지 생성
            badges = []
            if is_older_than_limit:
                badges.append("⭐ 스테디셀러")
            if trend_rank > 0:
                badges.append("🔥 급상승")
            if target_rank > 0:
                badges.append(f"👨‍👩‍👧 {target_label or '타겟'}추천")
            if sales_point >= 100000 or target_loan_cnt >= 50:
                badges.append("📚 인기대출")
            if not badges:
                badges.append("📖 일반추천")

            recommend_tag = " | ".join(badges)

            processed.append({
                "선정": not is_owned,
                "소장여부": "소장중" if is_owned else "미소장",
                "EUI점수": eui_score,
                "추천사유": recommend_tag,
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
                "예스24URL": item.get("link", "")
            })

        df = pd.DataFrame(processed)
        if not df.empty:
            df = df.sort_values(by=["EUI점수", "판매지수"], ascending=[False, False]).reset_index(drop=True)

        return df

        return df

    def update_ownership_status(self, df: pd.DataFrame, lib_code: str, owned_isbns_set: set = None) -> pd.DataFrame:
        """기존 DataFrame 도서들에 대해 정보나루 소장 여부를 병렬 실시간 재조회"""
        if df.empty:
            return df
        if owned_isbns_set is None:
            owned_isbns_set = set()

        df_copy = df.copy()
        isbns = df_copy["ISBN13"].tolist()
        ownership_results = {}

        def check_single(isbn):
            if isbn in owned_isbns_set:
                return isbn, True
            if self.info_naru_key and lib_code:
                return isbn, self.check_naru_book_exist(lib_code, isbn)
            return isbn, False

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(check_single, isbn) for isbn in isbns]
            for future in concurrent.futures.as_completed(futures):
                isbn, owned = future.result()
                ownership_results[isbn] = owned

        self._save_cache(self.owned_cache, self.owned_cache_path)

        new_ownership = []
        new_selected = []
        for _, row in df_copy.iterrows():
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
        """예산액 및 목표 권수 제한에 맞게 도서 선정 유무 자동 갱신"""
        if df.empty:
            return df

        df_copy = df.copy()
        accumulated_cost = 0
        selected_count = 0
        new_selected = []

        for _, row in df_copy.iterrows():
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
            "EUI점수", "추천사유", "KDC코드", "KDC분류", "도서명", "저자", "출판사", "출간일", 
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
