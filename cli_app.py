import os
import json
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
from utils.api_handler import LibraryAPIHandler
import concurrent.futures

# 1. 환경 설정 로드
load_dotenv()
NARU_KEY = os.getenv("INFO_NARU_KEY")
ALADIN_KEY = os.getenv("ALADIN_TTB_KEY")

REGION_OPTIONS = {"전체": "", "서울": "11", "경기": "31", "광주": "24", "부산": "21", "인천": "23", "대구": "22", "경기도교육청": "GOE"}
GOE_LIBS = {"141004": "과천", "141006": "경기평생", "141008": "중앙", "141012": "성남", "141106": "화성", "141010": "의정부", "141141": "평택", "141009": "광주", "141017": "김포", "141130": "포천"}
DEFAULT_EXCLUSIONS = ["수험서", "문제집", "자격증", "기출", "워크북", "그래픽노블", "라이트노벨", "판타지소설", "무협소설", "컬러링북", "학습지", "잡지", "연속간행물", "큰글자책", "큰글씨책", "모의고사", "참고서", "대학교재", "토익", "토플", "오픽"]

ALADIN_CAT_MAP = {"전체": 0, "일반": 0, "청소년": 1108, "아동": 1106}

def get_input(prompt, default):
    val = input(f"{prompt} (기본: {default}): ")
    return val if val else default

def load_manual_isbn_list(file_path):
    if not file_path or not os.path.exists(file_path): return set()
    try:
        df = pd.read_excel(file_path) if file_path.endswith('.xlsx') else pd.read_csv(file_path)
        col = [c for c in df.columns if 'isbn' in c.lower() or '도서번호' in c][0]
        return set(df[col].astype(str).str.strip().unique())
    except: return set()

def check_book_data(item, handler, my_lib, bulk_bench_data, pop_cache, start_year, end_year, exclude_list, manual_owned, w_sales, w_popular, w_bench, discount, selected_target):
    """초고속 분석 엔진 (벌크 데이터 기반)"""
    isbn = item.get('isbn13'); title = item.get('title', ''); category = item.get('categoryName', '')
    if not isbn: return None
    
    target_grp, kdc_code = handler.analyze_category(item)
    if selected_target != "전체" and target_grp != selected_target: return None

    # 1. 로컬 필터링
    try:
        pub_year = int(item.get('pubDate', '1900')[:4])
        if not (start_year <= pub_year <= end_year): return None
    except: return None
    if any(kw in title or kw in category for kw in exclude_list): return None
    if isbn in manual_owned: return None
    if item.get('stockStatus', '정상판매') not in ["정상판매", "예약판매", ""]: return None

    # 2. 벤치마킹 대조 (로컬 메모리에서 즉시 확인 - API 호출 0회)
    owned_names = [name for name, isbns in bulk_bench_data.items() if isbn in isbns]
    owned_count = len(owned_names)
    
    # 3. 내 도서관 소장 여부 (이건 API 호출 필요)
    if handler.check_book_existence(my_lib, isbn) == "Y": return None
            
    # 4. 고도화된 점수 계산 (벤치마킹 소장 수 반영)
    loan_cnt = pop_cache.get(isbn, 0)
    sales_point = item.get('salesPoint', 0)
    # 점수 산식: 판매지수 + 대출건수(가중) + 벤치마킹소장수(가중)
    score = (sales_point * w_sales) + (loan_cnt * 100 * w_popular) + (owned_count * 1000 * w_bench)

    return {
        "대상": target_grp, "KDC": kdc_code, "서명": title, "저자명": item.get('author'),
        "출판사": item.get('publisher'), "출판년도": str(pub_year), "가격": item.get('priceStandard', 0),
        "실구입가": int(item.get('priceStandard', 0)*(1-discount)), "ISBN": isbn,
        "판매지수": sales_point, "인기대출": loan_cnt, "추천점수": round(score, 2),
        "벤치마킹": f"{owned_count}/{len(bulk_bench_data)}곳 소장",
        "소장도서관": ", ".join(owned_names)
    }

def run_cli_recommendation():
    print("\n" + "="*95)
    print("    🚀 초고속 벌크 벤치마킹 엔진 (등록일 기반 가속 & 검증 중심 추천)")
    print("="*95)
    
    if not NARU_KEY or not ALADIN_KEY:
        print("❌ API 키 오류"); return

    handler = LibraryAPIHandler(NARU_KEY, ALADIN_KEY)

    try:
        # 설정 입력
        target_choice = get_input("\n🎯 추천 대상 (1.전체 2.일반 3.청소년 4.아동)", "1")
        target_map = {"1": "전체", "2": "일반", "3": "청소년", "4": "아동"}
        selected_target = target_map.get(target_choice, "전체")
        
        lib_code = get_input("🔹 나의 도서관 코드", "141005")
        
        print("\n🏛️ [벤치마킹 기간 설정 - 해당 기간 구입 도서와 대조합니다]")
        b_start = get_input("   1. 시작일 (YYYY-MM-DD)", "2025-01-01")
        b_end = get_input("   2. 종료일 (YYYY-MM-DD)", datetime.now().strftime("%Y-%m-%d"))
        
        reg_name = get_input("\n🏛️ 벤치마킹 그룹 (경기도교육청/서울/경기 등)", "경기도교육청")
        bench_libs = GOE_LIBS if reg_name == "경기도교육청" else handler.fetch_libraries_by_region(REGION_OPTIONS.get(reg_name, ""), 5)

        # 벌크 데이터 로딩 (속도 개선의 핵심)
        print(f"\n[1/4] 벤치마킹 도서관 {len(bench_libs)}곳의 구입 목록 수집 중 (벌크 로딩)...")
        bulk_bench_data = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_lib = {executor.submit(handler.fetch_library_new_additions, code, b_start, b_end): name for code, name in bench_libs.items()}
            for future in concurrent.futures.as_completed(future_to_lib):
                name = future_to_lib[future]
                bulk_bench_data[name] = future.result()
        print(f"      ✅ 총 {sum(len(v) for v in bulk_bench_data.values()):,}권의 벤치마킹 데이터 로드 완료.")

        start_year = int(get_input("\n📅 출판 시작 연도 (YYYY)", "2024"))
        end_year = int(get_input("📅 출판 종료 연도 (YYYY)", str(datetime.now().year)))
        manual_owned = load_manual_isbn_list(input("📂 로컬 소장 목록 파일(생략 시 엔터): "))
        exclude_list = DEFAULT_EXCLUSIONS + [k.strip() for k in get_input("❌ 추가 제외 키워드", "").split(",") if k.strip()]

        print("\n💰 [예산 및 가중치]")
        target_qty = int(get_input(f"   1. {selected_target} 목표 권수", "100"))
        budget_total = int(get_input(f"   2. {selected_target} 예산 (원)", "2000000"))
        w_sales = float(get_input("⚖️ 판매지수 가중치 (0~1.0)", "0.5"))
        w_popular = float(get_input("⚖️ 인기대출 가중치 (0~1.0)", "0.2"))
        w_bench = float(get_input("⚖️ 벤치마킹 가중치 (0~1.0)", "0.3"))

        # 수집 및 분석
        print(f"\n[2/4] '{selected_target}' 분야 후보 최대 1,000권 수집 중...")
        pop_cache = {d['doc'].get('isbn13'): int(d['doc'].get('loan_count', 0)) for d in handler.fetch_popular_books(500)}
        new_books = handler.fetch_new_books(query_type='ItemNewAll', max_results=1000, category_id=ALADIN_CAT_MAP.get(selected_target, 0))
        
        print(f"[3/4] 정밀 병렬 분석 가동 (후보 {len(new_books)}권)...")
        processed = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(check_book_data, item, handler, lib_code, bulk_bench_data, pop_cache, start_year, end_year, exclude_list, manual_owned, w_sales, w_popular, w_bench, 0.1, selected_target) for item in new_books]
            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                res = future.result()
                if res: processed.append(res)
                if (i+1) % 200 == 0: print(f"     > {i+1}권 처리 완료...")

        # 최종 선정
        df = pd.DataFrame(processed).sort_values(by="추천점수", ascending=False)
        selected = []; spent = 0
        for _, row in df.iterrows():
            if spent + row['실구입가'] <= budget_total and len(selected) < target_qty * 1.3:
                selected.append(row); spent += row['실구입가']

        if not selected: print("\n⚠️ 결과가 없습니다."); return
        final_df = pd.DataFrame(selected)
        print("\n" + "="*95); print(f"✅ [{selected_target}] 최종 추천 완료 ({len(final_df)}권 선정)"); print(f"   💰 총 집행: {spent:,}원 / {budget_total:,}원"); print("="*95)
        print(final_df[["대상", "KDC", "서명", "실구입가", "추천점수", "벤치마킹"]].head(20).to_string(index=False))
        
        fname = f"rec_bulk_{selected_target}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        final_df[["대상", "KDC", "서명", "저자명", "출판사", "출판년도", "가격", "실구입가", "ISBN", "판매지수", "인기대출", "추천점수", "벤치마킹", "소장도서관"]].to_excel(fname, index=False)
        print(f"\n💾 벤치마킹 벌크 데이터가 반영된 엑셀 저장 완료: {fname}")

    except Exception as e: print(f"❌ 오류: {e}")

if __name__ == "__main__":
    run_cli_recommendation()
