import streamlit as st
import pandas as pd
import numpy as np
import os
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from utils.aladin_acq import AladinAcquisitionManager
from utils.api_handler import LibraryAPIHandler

# 환경변수 로드 및 기본 설정
load_dotenv()
st.set_page_config(
    page_title="알라딘 & 정보나루 v2.0 지능형 이용기대율(EUI) 수서 시스템",
    page_icon="📖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS 스타일링
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Pretendard:wght@300;400;600;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Pretendard', sans-serif;
    }
    
    .main-header {
        background: linear-gradient(135deg, #1E293B 0%, #0F172A 100%);
        padding: 1.8rem 2rem;
        border-radius: 16px;
        color: white;
        margin-bottom: 1.5rem;
        box-shadow: 0 10px 25px -5px rgba(0,0,0,0.1);
        border: 1px solid rgba(255,255,255,0.1);
    }
    
    .main-header h1 {
        color: #F8FAFC;
        font-size: 1.8rem;
        font-weight: 700;
        margin: 0 0 0.3rem 0;
        display: flex;
        align-items: center;
        gap: 10px;
    }
    
    .main-header p {
        color: #94A3B8;
        font-size: 0.95rem;
        margin: 0;
    }
    
    .metric-card {
        background: #FFFFFF;
        padding: 1.2rem;
        border-radius: 12px;
        border: 1px solid #E2E8F0;
        box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
        text-align: center;
    }
    
    .metric-value {
        font-size: 1.6rem;
        font-weight: 700;
        color: #0F172A;
        margin-top: 0.2rem;
    }
    
    .metric-label {
        font-size: 0.85rem;
        color: #64748B;
        font-weight: 600;
    }

    .stButton>button {
        border-radius: 10px;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

# Streamlit 다중 카테고리 데이터 캐시 함수 (0.001초 반환)
@st.cache_data(ttl=3600, show_spinner=False)
def cached_fetch_aladin_books_multi(aladin_key: str, query_type: str, category_ids: tuple, max_results: int):
    mgr = AladinAcquisitionManager(aladin_key)
    return mgr.fetch_aladin_books_multi(query_type=query_type, category_ids=list(category_ids), max_results=max_results)

# 세션 상태 초기화
if "raw_books" not in st.session_state:
    st.session_state.raw_books = []
if "processed_df" not in st.session_state:
    st.session_state.processed_df = pd.DataFrame()
if "owned_isbns" not in st.session_state:
    st.session_state.owned_isbns = set()

# API 기본 키 및 설정 로드
default_aladin_key = os.getenv("ALADIN_TTB_KEY", "ttbsinddung1748001")
default_naru_key = os.getenv("INFO_NARU_KEY", "")
default_lib_code = os.getenv("DEFAULT_LIB_CODE", "141005")

# Header
st.markdown("""
<div class="main-header">
    <h1>📖 알라딘 & 도서관정보나루 v2.0 지능형 이용기대율(EUI) 수서 시스템</h1>
    <p>알라딘 서지 및 도서관정보나루 연령/성별 대출 빅데이터, 급상승 트렌드를 결합하여 이용기대율(EUI) 점수 기반 최적 수서를 지원합니다.</p>
</div>
""", unsafe_allow_html=True)

# --- 사이드바 설정 ---
with st.sidebar:
    st.title("⚙️ 수서 설정 대시보드 (v2.0)")
    
    with st.expander("🔑 API 인증 키 관리", expanded=False):
        aladin_key = st.text_input("알라딘 TTB Key", value=default_aladin_key, type="password")
        naru_key = st.text_input("도서관 정보나루 Key", value=default_naru_key, type="password")
        
    manager = AladinAcquisitionManager(aladin_key, naru_key)
    api_handler = LibraryAPIHandler(naru_key, aladin_key)
    
    with st.expander("🎯 v2.0 지능형 이용기대율(EUI) 수서 설정", expanded=True):
        st.markdown("##### 1. 타겟층 대출 빅데이터 분석")
        target_age_label = st.selectbox(
            "주 타겟 연령대",
            options=list(LibraryAPIHandler.AGE_OPTIONS.keys()),
            index=6,
            help="선택한 연령대의 공공도서관 전국 대출 상위 도서에 우선 가중치를 부여합니다."
        )
        target_gender_label = st.selectbox(
            "주 타겟 성별",
            options=list(LibraryAPIHandler.GENDER_OPTIONS.keys()),
            index=0,
            help="선택한 성별의 대출 상위 패턴을 분석합니다."
        )

        st.markdown("##### 2. 급상승 트렌드 반영")
        use_hot_trend = st.checkbox(
            "🔥 공공도서관 급상승 트렌드 반영",
            value=True,
            help="최근 대출 순위가 급증한 공공도서관 라이징 도서에 트렌드 점수를 부여합니다."
        )

        with st.expander("🧮 이용기대율(EUI) 가중치 비율 조정", expanded=False):
            w_sales = st.slider("알라딘 판매지수 가중치", 0.0, 1.0, 0.35, 0.05)
            w_loans = st.slider("전국 대출건수 가중치", 0.0, 1.0, 0.25, 0.05)
            w_target = st.slider("타겟층 대출가중치", 0.0, 1.0, 0.25, 0.05)
            w_trend = st.slider("급상승 트렌드가중치", 0.0, 1.0, 0.15, 0.05)

            eui_weights = {
                "sales": w_sales,
                "loans": w_loans,
                "target": w_target,
                "trend": w_trend
            }

    with st.expander("🏛️ 우리 도서관 소장 설정", expanded=True):
        lib_code = st.text_input("나의 도서관 코드 (정보나루)", value=default_lib_code, help="도서관 정보나루 도서관 코드 (예: 141005)")
        auto_exclude_owned = st.checkbox("소장 중인 도서 자동 제외", value=True, help="정보나루 API 또는 업로드된 파일로 이미 소장 중으로 확인된 도서를 발주 후보에서 자동 제어합니다.")
        
        owned_file = st.file_uploader("📂 소장 도서 목록 업로드 (.csv/.xlsx)", type=["csv", "xlsx"])
        if owned_file:
            try:
                df_owned = pd.read_excel(owned_file) if owned_file.name.endswith(".xlsx") else pd.read_csv(owned_file)
                isbn_col = [c for c in df_owned.columns if "ISBN" in c.upper() or "상세" in c or "번호" in c]
                if isbn_col:
                    isbns = set(manager.clean_isbn(i) for i in df_owned[isbn_col[0]].dropna())
                    st.session_state.owned_isbns = isbns
                    st.success(f"✅ 소장 도서 {len(isbns):,}권의 ISBN을 등록했습니다.")
                else:
                    st.warning("⚠️ 파일에서 'ISBN' 컬럼을 찾지 못했습니다.")
            except Exception as e:
                st.error(f"❌ 소장 목록 읽기 실패: {e}")

        if st.session_state.owned_isbns:
            st.caption(f"💾 등록된 소장 ISBN: {len(st.session_state.owned_isbns):,}권")

    st.subheader("📚 수서 검색 대상")
    query_types = {
        "ItemNewSpecial": "🌟 주목할 만한 신간 (추천)",
        "ItemNewAll": "🆕 전체 신간",
        "Bestseller": "🔥 베스트셀러",
        "BlogBest": "✍️ 블로거 추천 도서"
    }
    selected_query_type = st.selectbox(
        "수서 도서 수집 유형",
        options=list(query_types.keys()),
        format_func=lambda x: query_types[x]
    )
    
    with st.expander("📚 수서 분야 선택 (체크박스 복수 선택)", expanded=True):
        all_categories = [c for c in manager.ALADIN_CATEGORIES.keys() if c != "전체 분야"]
        
        chk_col1, chk_col2 = st.columns([1.2, 1])
        select_all = chk_col1.checkbox("전체 분야 선택", value=False, help="체크 시 모든 분야의 신간 도서를 수집합니다.")
        
        selected_category_names = []
        
        if select_all:
            selected_category_names = ["전체 분야"]
            st.caption("✨ '전체 분야'가 선택되었습니다.")
        else:
            cat_cols = st.columns(2)
            default_checked = ["소설/시/희곡", "에세이", "유아", "아동 (어린이)"]
            
            for idx, cat_name in enumerate(all_categories):
                with cat_cols[idx % 2]:
                    is_checked = st.checkbox(
                        cat_name, 
                        value=(cat_name in default_checked), 
                        key=f"cat_chk_{idx}"
                    )
                    if is_checked:
                        selected_category_names.append(cat_name)

        if not selected_category_names:
            st.warning("⚠️ 선택된 분야가 없어 '전체 분야'로 수집됩니다.")
            selected_category_names = ["전체 분야"]

    # 선택된 카테고리 CID 리스트 변환
    selected_category_ids = [manager.ALADIN_CATEGORIES[cat] for cat in selected_category_names]
    
    max_fetch_count = st.slider("수집 대상 도서 수", min_value=50, max_value=500, value=200, step=50)

    st.markdown("---")
    st.subheader("💰 예산 및 수서 조건")
    
    col_b1, col_b2 = st.columns(2)
    with col_b1:
        discount_rate_pct = st.number_input("수서 할인율 (%)", min_value=0, max_value=50, value=10, step=1)
    with col_b2:
        max_books_target = st.number_input("목표 수서 권수", min_value=0, max_value=1000, value=50, step=10)
        
    total_budget_limit = st.number_input("수서 상한 예산액 (원, 0=무제한)", min_value=0, value=1000000, step=100000)

    st.markdown("---")
    st.subheader("📅 출간일 조건")
    days_ago = st.selectbox("출간 시점 필터", options=[30, 60, 90, 180, 365], index=1, format_func=lambda x: f"최근 {x}일 이내 출간")
    min_pub_date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")

    with st.expander("🚫 제외 키워드 & ISBN 관리", expanded=False):
        exclude_kw_str = st.text_area(
            "제외할 키워드 (쉼표 구분)",
            value=", ".join(manager.DEFAULT_EXCLUDE_KEYWORDS),
            height=100
        )
        exclude_keywords = [k.strip() for k in exclude_kw_str.split(",") if k.strip()]
        
        exclude_isbn_str = st.text_area("제외할 ISBN (줄바꿈 구분)", placeholder="이미 소장 중이거나 제외할 ISBN13...")
        exclude_isbns = set(manager.clean_isbn(i) for i in exclude_isbn_str.splitlines() if i.strip())

    st.markdown("---")
    fetch_btn = st.button("🚀 v2.0 지능형 신간 도서 수집 및 이용기대율 분석", type="primary", use_container_width=True)

# --- 메인 로직 처리 ---
if fetch_btn or (st.session_state.raw_books and st.session_state.processed_df.empty):
    if not aladin_key:
        st.error("⚠️ 알라딘 TTB Key를 입력해주세요!")
    else:
        start_time = time.time()
        cat_disp_count = len(selected_category_names)
        with st.spinner(f"📡 알라딘 및 정보나루 빅데이터 연동 분석 중 ({cat_disp_count}개 분야)..."):
            # 1. 알라딘 도서 수집
            raw_books = cached_fetch_aladin_books_multi(
                aladin_key=aladin_key,
                query_type=selected_query_type,
                category_ids=tuple(selected_category_ids),
                max_results=max_fetch_count
            )
            st.session_state.raw_books = raw_books

            # 2. 정보나루 타겟층 대출 상위 도서 일괄 수집
            age_code = LibraryAPIHandler.AGE_OPTIONS.get(target_age_label, "")
            gender_code = LibraryAPIHandler.GENDER_OPTIONS.get(target_gender_label, "")
            target_label_str = f"{target_age_label.split()[0]}" if target_age_label != "전체 연령" else ""
            if target_gender_label != "전체 성별":
                target_label_str = f"{target_label_str}/{target_gender_label}"

            target_loan_map = {}
            if naru_key:
                target_loan_map = api_handler.fetch_target_demographic_loans(
                    age=age_code,
                    gender=gender_code,
                    page_size=300
                )

            # 3. 정보나루 급상승 트렌드 수집
            trend_loan_map = {}
            if naru_key and use_hot_trend:
                trend_loan_map = api_handler.fetch_hot_trends(page_size=200)
            
            # 4. 데이터 정제 및 EUI 점수 산출 & 소장여부 병렬 검사
            df = manager.process_acquisition_candidates(
                raw_books=raw_books,
                exclude_keywords=exclude_keywords,
                exclude_isbns=exclude_isbns,
                min_pub_date=min_pub_date,
                discount_rate=discount_rate_pct / 100.0,
                lib_code=lib_code,
                owned_isbns_set=st.session_state.owned_isbns,
                auto_exclude_owned=auto_exclude_owned,
                target_loan_map=target_loan_map,
                trend_loan_map=trend_loan_map,
                eui_weights=eui_weights,
                target_label=target_label_str
            )
            
            # 5. 예산 제한 적용
            df = manager.apply_budget_cap(
                df,
                max_budget=total_budget_limit,
                max_books=max_books_target
            )
            
            st.session_state.processed_df = df
            elapsed = time.time() - start_time
            cat_desc = ", ".join(selected_category_names[:2]) + (f" 외 {len(selected_category_names)-2}개" if len(selected_category_names) > 2 else "")
            st.toast(f"⚡ v2.0 이용기대율(EUI) 분석 완료! ({len(df)}권 후보 정제, {elapsed:.2f}초 소요)", icon="🚀")

# --- 메인 화면 렌더링 ---
df = st.session_state.processed_df

if df.empty:
    st.info("👈 왼쪽 사이드바에서 수서 조건 및 타겟층을 선택한 후 **[🚀 v2.0 지능형 신간 도서 수집 및 이용기대율 분석]** 버튼을 클릭하세요.")
else:
    # 1. 요약 메트릭
    selected_mask = df["선정"] == True
    selected_df = df[selected_mask]
    
    total_candidates = len(df)
    selected_count = len(selected_df)
    owned_count = len(df[df["소장여부"] == "소장중"]) if "소장여부" in df.columns else 0
    total_list_price = selected_df["정가"].sum() if not selected_df.empty else 0
    total_est_price = selected_df["추정실구입가"].sum() if not selected_df.empty else 0
    budget_usage = (total_est_price / total_budget_limit * 100) if total_budget_limit > 0 else 0
    
    m_col1, m_col2, m_col3, m_col4, m_col5 = st.columns(5)
    with m_col1:
        st.markdown(f'<div class="metric-card"><div class="metric-label">총 후보 도서</div><div class="metric-value">{total_candidates:,}권</div></div>', unsafe_allow_html=True)
    with m_col2:
        st.markdown(f'<div class="metric-card"><div class="metric-label">최종 수서 선정</div><div class="metric-value" style="color:#2563EB;">{selected_count:,}권</div></div>', unsafe_allow_html=True)
    with m_col3:
        st.markdown(f'<div class="metric-card"><div class="metric-label">이미 소장 중</div><div class="metric-value" style="color:#D97706;">{owned_count:,}권</div></div>', unsafe_allow_html=True)
    with m_col4:
        st.markdown(f'<div class="metric-card"><div class="metric-label">예상 실구입가 ({100-discount_rate_pct}%)</div><div class="metric-value" style="color:#059669;">{total_est_price:,}원</div></div>', unsafe_allow_html=True)
    with m_col5:
        color = "#DC2626" if budget_usage > 100 else "#2563EB"
        st.markdown(f'<div class="metric-card"><div class="metric-label">예산 집행률</div><div class="metric-value" style="color:{color};">{budget_usage:.1f}%</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # 2. 통계 시각화 및 액션 탭
    tab_list, tab_stats = st.tabs(["📑 v2.0 수서 도서 목록 및 발주 관리", "📊 KDC 분야별 분포 및 EUI 분석"])

    with tab_list:
        top_bar_col1, top_bar_col2, top_bar_col3 = st.columns([2.5, 1.2, 1.3])
        with top_bar_col1:
            st.subheader("📚 v2.0 EUI 우선순위 수서 목록 (소장 여부 연동)")
        with top_bar_col2:
            if st.button("🔍 소장여부 실시간 재조회", use_container_width=True, help="정보나루 API를 병렬 호출하여 목록 전체의 실시간 소장 여부를 빠르게 다시 확인합니다."):
                with st.spinner("🔍 정보나루 API 병렬 소장여부 확인 중..."):
                    updated_df = manager.update_ownership_status(
                        df,
                        lib_code=lib_code,
                        owned_isbns_set=st.session_state.owned_isbns
                    )
                    st.session_state.processed_df = updated_df
                    st.success("✅ 실시간 소장 여부가 최신화되었습니다!")
                    time.sleep(0.5)
                    st.rerun()

        with top_bar_col3:
            excel_bytes = manager.create_excel_order_sheet(df)
            st.download_button(
                label="📥 엑셀 발주서 다운로드",
                data=excel_bytes,
                file_name=f"알라딘_v2.0_수서발주목록_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True
            )

        # 데이터 에디터로 수정 가능하도록 제공
        edited_df = st.data_editor(
            df,
            column_config={
                "선정": st.column_config.CheckboxColumn("발주선정", help="체크 시 발주 목록에 포함됩니다."),
                "EUI점수": st.column_config.NumberColumn("이용기대율(EUI)", format="%.1f점", help="알라딘 판매지수, 전국 대출건수, 타겟층 대출, 급상승 트렌드를 종합 연산한 기대율 점수"),
                "추천사유": st.column_config.TextColumn("추천 사유 뱃지", width="medium"),
                "소장여부": st.column_config.TextColumn("도서관 소장", help="정보나루 API 또는 업로드된 소장목록 기준"),
                "KDC분류": st.column_config.TextColumn("KDC 분야", width="medium"),
                "도서명": st.column_config.TextColumn("도서명", width="large"),
                "저자": st.column_config.TextColumn("저자", width="medium"),
                "출판사": st.column_config.TextColumn("출판사", width="medium"),
                "정가": st.column_config.NumberColumn("정가(원)", format="%d원"),
                "추정실구입가": st.column_config.NumberColumn("실구입가(원)", format="%d원"),
                "판매지수": st.column_config.NumberColumn("판매지수", format="%d"),
                "알라딘URL": st.column_config.LinkColumn("알라딘 상세", display_text="보기"),
            },
            disabled=["EUI점수", "추천사유", "소장여부", "KDC코드", "KDC분류", "도서명", "저자", "출판사", "출간일", "정가", "추정실구입가", "판매지수", "ISBN13", "카테고리", "알라딘URL"],
            hide_index=True,
            use_container_width=True,
            height=500
        )
        
        st.session_state.processed_df = edited_df

    with tab_stats:
        if not selected_df.empty:
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("📊 KDC 10대 분류별 도서 권수")
                kdc_counts = selected_df["KDC분류"].value_counts().reset_index()
                kdc_counts.columns = ["KDC분류", "권수"]
                st.bar_chart(kdc_counts.set_index("KDC분류"))
                
            with c2:
                st.subheader("💵 KDC 분류별 실구입 소요 예산")
                kdc_budget = selected_df.groupby("KDC분류")["추정실구입가"].sum().reset_index()
                st.bar_chart(kdc_budget.set_index("KDC분류"))
        else:
            st.warning("선정된 도서가 없습니다. 도서 목록에서 '선정' 체크박스를 선택해주세요.")

# 하단 푸터
st.markdown("---")
st.caption("💡 본 시스템은 알라딘 TTB Open API 및 도서관 정보나루 빅데이터 API를 활용한 v2.0 지능형 수서 솔루션입니다.")
