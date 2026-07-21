import streamlit as st
import pandas as pd
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from utils.api_handler import LibraryAPIHandler
import concurrent.futures
import io

# 설정 로드 및 핸들러 초기화
load_dotenv()
st.set_page_config(page_title="도서관 스마트 자료구입 지원 시스템", layout="wide", page_icon="📚")

if "rec_df" not in st.session_state: st.session_state.rec_df = None

naru_key = os.getenv("INFO_NARU_KEY", "")
aladin_key = os.getenv("ALADIN_TTB_KEY", "")
handler = LibraryAPIHandler(naru_key, aladin_key)

# --- 사이드바 설정 ---
with st.sidebar:
    st.header("⚙️ 시스템 설정")
    with st.expander("🔑 API 키 관리", expanded=False):
        naru_key = st.text_input("정보나루 API 키", value=naru_key, type="password")
        aladin_key = st.text_input("알라딘 TTB 키", value=aladin_key, type="password")
        handler.info_naru_key, handler.aladin_ttb_key = naru_key, aladin_key

    with st.expander("🏛️ 나의 도서관 설정", expanded=True):
        lib_code = st.text_input("나의 도서관 코드", value="141005")
        manual_owned_file = st.file_uploader("📂 최신 소장 목록 업로드", type=["xlsx", "csv", "txt"])
        isbn_input = st.text_area("🚫 제외 ISBN 직접 입력", placeholder="ISBN들을 엔터나 콤마로 구분하여 입력...", help="이미 구입했거나 추천에서 제외할 ISBN을 직접 입력하세요.")
        
    with st.expander("📊 벤치마킹 그룹 설정", expanded=True):
        reg_options = ["선택 안함 (초고속/신작 중심)"] + [k for k in handler.REGION_OPTIONS.keys() if k not in ["선택 안함 (초고속/판매지수 중심)", "전체"]]
        reg_name = st.selectbox("비교 대상 지역/그룹", reg_options)
        do_bench = "선택 안함" not in reg_name
        if do_bench:
            col_d1, col_d2 = st.columns(2)
            b_start = col_d1.date_input("대조 시작일", datetime.now() - timedelta(days=365))
            b_end = col_d2.date_input("대조 종료일", datetime.now())
        else: st.info("🚀 **초고속 모드**: 신작/판매지수 위주로 분석합니다.")

# --- 메인 화면 ---
st.title("📚 스마트 자료구입 추천 시스템")
tab1, tab2 = st.tabs(["🔍 추천 검색 및 KDC 목표 설정", "📋 상세 결과 및 분석"])

with tab1:
    st.subheader("🎯 기본 및 KDC 목표 설정")
    col_top1, col_top2, col_top3 = st.columns([1, 1, 1])
    selected_target = col_top1.selectbox("추천 대상", list(handler.ALADIN_CAT_MAP.keys()))
    start_year = col_top2.number_input("출판 시작 연도", value=datetime.now().year - 1)
    end_year = col_top3.number_input("출판 종료 연도", value=datetime.now().year)
    
    col_mid1, col_mid2, col_mid3, col_mid4 = st.columns([1.5, 1, 1, 1])
    budget_total = col_mid1.number_input("총 구입 예산 (원)", value=2000000, step=100000)
    discount_rate = col_mid2.slider("할인율 (%)", 0, 30, 10)
    buffer_rate = col_mid3.slider("추가 조사 비율 (%)", 0, 100, 30)
    
    st.markdown("**📑 KDC 류별 목표 권수 설정**")
    k_data = pd.DataFrame({f"{i}0": [10 if i in [0,3,4,5] else 20 if i in [8,9] else 5] for i in range(10)})
    k_data.columns = [handler.KDC_BASE_NAMES[str(i)] for i in range(10)]
    edited_k_df = st.data_editor(k_data, use_container_width=True, hide_index=True)
    kdc_targets = {str(i): edited_k_df.iloc[0, i] for i in range(10)}

    col_bot1, col_bot2 = st.columns([1, 1.5])
    with col_bot1:
        st.subheader("❌ 필터링 설정")
        with st.expander("🔍 자동 제외 키워드 목록"): st.write(", ".join(handler.DEFAULT_EXCLUSIONS))
        exclude_input = st.text_area("추가 제외 키워드 (콤마 구분)", placeholder="문제집, 웹툰...")
        exclude_list = handler.DEFAULT_EXCLUSIONS + [k.strip() for k in exclude_input.split(",") if k.strip()]
        
    with col_bot2:
        st.subheader("⚖️ 추천 가중치 설정 (합계 1.0)")
        if "w_sales" not in st.session_state: st.session_state.w_sales = 0.7
        w_sales = st.slider("알라딘 판매지수 비중", 0.0, 1.0, st.session_state.w_sales, key="w_sales_slider")
        st.session_state.w_sales = w_sales
        w_popular = round(1.0 - w_sales, 1)
        st.write(f"📊 정보나루 대출순위 비중 (자동): **{w_popular}**")
        w_bench = st.slider("벤치마킹 소장수 가중치 (보너스)", 0.0, 1.0, 0.3 if do_bench else 0.0, disabled=not do_bench)

    if st.button("🚀 추천 리스트 생성 시작", use_container_width=True, type="primary"):
        if not naru_key or not aladin_key: st.error("API 키를 확인해 주세요.")
        else:
            # 1. 소장 목록 및 제외 ISBN 정규화 합치기
            manual_owned = set()
            if manual_owned_file:
                try:
                    df_m = pd.read_excel(manual_owned_file) if manual_owned_file.name.endswith('.xlsx') else pd.read_csv(manual_owned_file)
                    col = [c for c in df_m.columns if 'isbn' in c.lower() or '도서번호' in c][0]
                    manual_owned.update({handler.clean_isbn(i) for i in df_m[col].astype(str).unique() if i})
                except: pass
            if isbn_input:
                manual_owned.update({handler.clean_isbn(i) for i in isbn_input.replace(",", "\n").split("\n") if i.strip()})

            with st.spinner("📊 데이터 수집 및 정밀 분석 중..."):
                bulk_bench_data = {}; naru_kdc_map = {}; bench_loan_map = {}
                
                # 벤치마킹 데이터 수집
                if do_bench:
                    bench_libs = handler.GOE_LIBS if reg_name == "경기도교육청" else handler.fetch_libraries_by_region(handler.REGION_OPTIONS[reg_name], 5)
                    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                        f_to_l = {executor.submit(handler.fetch_library_new_additions, c, b_start.strftime("%Y-%m-%d"), b_end.strftime("%Y-%m-%d")): n for c, n in bench_libs.items()}
                        for f in concurrent.futures.as_completed(f_to_l): 
                            lib_res = f.result() # {isbn: (kdc, loan_cnt)}
                            bulk_bench_data[f_to_l[f]] = set(lib_res.keys())
                            for isbn, (kdc, l_cnt) in lib_res.items():
                                if kdc and isbn not in naru_kdc_map: naru_kdc_map[isbn] = kdc
                                bench_loan_map[isbn] = bench_loan_map.get(isbn, 0) + l_cnt

                # 전국 인기 도서 수집
                pop_data = handler.fetch_popular_books(500)
                pop_cache = {handler.clean_isbn(d['doc'].get('isbn13')): int(d['doc'].get('loan_count', 0)) for d in pop_data if d['doc'].get('isbn13')}
                for d in pop_data:
                    isbn = handler.clean_isbn(d['doc'].get('isbn13')); kdc = d['doc'].get('class_no', '')
                    if isbn and kdc and isbn not in naru_kdc_map: naru_kdc_map[isbn] = kdc
                
                # 분석 후보 ISBN 확보
                all_raw = []
                if do_bench:
                    status = st.status("📊 벤치마킹 그룹 대출 통계 분석 중...")
                    sorted_isbn_candidates = sorted(bench_loan_map.items(), key=lambda x: x[1], reverse=True)
                    candidates_to_fetch = [isbn for isbn, l_cnt in sorted_isbn_candidates if isbn not in manual_owned][:3000]
                    status.write(f"📡 후보 {len(candidates_to_fetch):,}권 알라딘 상세 조회 시작...")
                    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                        f_tasks = [executor.submit(handler.fetch_book_details, isbn) for isbn in candidates_to_fetch]
                        for f in concurrent.futures.as_completed(f_tasks):
                            res = f.result()
                            if res: all_raw.append(res)
                    status.update(label="✅ 데이터 확보 완료!", state="complete")
                else:
                    with st.status("🔍 알라딘 신작/베스트셀러 확장 검색 중...") as status:
                        q_types = [('ItemNewSpecial', 500), ('Bestseller', 500), ('ItemEditorChoice', 500), ('ItemNewAll', 1000)]
                        for cid in handler.ALADIN_CAT_MAP[selected_target]:
                            for q_type, m_res in q_types:
                                all_raw.extend(handler.fetch_new_books(q_type, m_res, cid))
                        status.update(label="✅ 신작 수집 완료!", state="complete")
                
                # 최종 분석 및 필터링
                new_books_map = {handler.clean_isbn(b.get('isbn13')): b for b in all_raw if b.get('isbn13')}
                candidates = sorted(new_books_map.values(), key=lambda x: x.get('salesPoint', 0), reverse=True)[:3000]
                
                def analyze_task(item):
                    isbn = handler.clean_isbn(item.get('isbn13')); title = item.get('title', ''); category = item.get('categoryName', '')
                    if not isbn or any(kw in title or kw in category for kw in exclude_list): return None
                    if item.get('stockStatus', '정상판매') not in ["정상판매", "예약판매", ""]: return None
                    if isbn in manual_owned: return None
                    try:
                        py = int(item.get('pubDate', '1900')[:4])
                        if not (start_year <= py <= end_year): return None
                    except: return None
                    
                    tgt_grp, kdc_code = handler.analyze_category(item, naru_kdc=naru_kdc_map.get(isbn))
                    if selected_target == "아동" and tgt_grp not in ["아동", "유아"]: pass
                    elif selected_target != "전체" and tgt_grp != selected_target: return None
                    
                    if handler.check_book_existence(lib_code, isbn) == "Y": return None
                    
                    owned_cnt = len([n for n, isbns in bulk_bench_data.items() if isbn in isbns]) if do_bench else 0
                    l_cnt = bench_loan_map.get(isbn, 0) if do_bench else pop_cache.get(isbn, 0)
                    s_pt = item.get('salesPoint', 0)
                    score = (s_pt * w_sales) + (l_cnt * 100 * w_popular) + (owned_cnt * 1000 * w_bench)
                    
                    return {
                        "대상": tgt_grp, "KDC_코드": kdc_code[0], "KDC": handler.KDC_BASE_NAMES.get(kdc_code[0], "90 역사"), "KDC_상세": kdc_code,
                        "알라딘 분류": category, "서명": title, "저자명": item.get('author'), "출판사": item.get('publisher'),
                        "출판년도": str(py), "정가": int(item.get('priceStandard', 0)), "실구입가": int(item.get('priceStandard', 0)*(1-discount_rate/100)),
                        "ISBN": isbn, "판매지수": s_pt, "인기대출": l_cnt, "추천점수": round(score, 2), "벤치마킹": f"{owned_cnt}곳" if do_bench else "-"
                    }

                with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                    results = [r for r in list(executor.map(analyze_task, candidates)) if r]

                if results:
                    df_all = pd.DataFrame(results).sort_values(by="추천점수", ascending=False)
                    selected, spent, k_counts = [], 0, {str(i): 0 for i in range(10)}
                    k_limits = {k: int(v * (1 + buffer_rate/100)) for k, v in kdc_targets.items()}
                    for _, row in df_all.iterrows():
                        k, p = row['KDC_코드'], row['실구입가']
                        if spent + p <= budget_total and k_counts.get(k, 0) < k_limits.get(k, 999):
                            selected.append(row); spent += p; k_counts[k] += 1
                    st.session_state.rec_df = pd.DataFrame(selected).sort_values(by=["KDC_코드", "KDC_상세", "추천점수"], ascending=[True, True, False])
                    st.success(f"✅ {len(st.session_state.rec_df)}권의 리스트를 생성했습니다.")
                else: st.warning("⚠️ 조건에 맞는 도서가 없습니다.")

with tab2:
    if st.session_state.rec_df is not None:
        df = st.session_state.rec_df
        c1, c2, c3 = st.columns(3)
        c1.metric("선정 권수", f"{len(df)}권"); c2.metric("집행 예산", f"{df['실구입가'].sum():,}원"); c3.metric("예산 잔액", f"{budget_total - df['실구입가'].sum():,}원")
        
        st.subheader("📋 KDC 목표 현황")
        curr_counts = df.groupby("KDC_코드").size().to_dict()
        st.table(pd.DataFrame([{"KDC": handler.KDC_BASE_NAMES[str(i)], "선정": curr_counts.get(str(i), 0), "목표": kdc_targets.get(str(i), 0)} for i in range(10)]))

        st.subheader("📋 최종 리스트")
        df_view = df.copy()
        for col in ['정가', '실구입가']: df_view[col] = df_view[col].apply(lambda x: f"{x:,}원")
        st.dataframe(df_view[["대상", "KDC", "KDC_상세", "알라딘 분류", "서명", "저자명", "출판사", "출판년도", "실구입가", "판매지수", "인기대출", "추천점수", "벤치마킹"]], use_container_width=True, hide_index=True)
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='추천리스트')
        st.download_button("💾 엑셀 다운로드", buffer.getvalue(), f"library_rec_{datetime.now().strftime('%Y%m%d')}.xlsx", use_container_width=True)
    else: st.info("💡 설정을 마친 후 추천 시작 버튼을 눌러주세요.")
