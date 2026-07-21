import streamlit as st
import pandas as pd
import numpy as np
import os
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from utils.aladin_acq import AladinAcquisitionManager

# 환경변수 로드 및 기본 설정
load_dotenv()
st.set_page_config(
    page_title="알라딘 API 기반 도서관 신간 수서 스마트 시스템",
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

# [속도 개선 4] Streamlit 캐시 함수 정의 (동일 조건 0.01초 초고속 반환)
@st.cache_data(ttl=3600, show_spinner=False)
def cached_fetch_aladin_books(aladin_key: str, query_type: str, category_id: int, max_results: int):
    mgr = AladinAcquisitionManager(aladin_key)
    return mgr.fetch_aladin_books(query_type=query_type, category_id=category_id, max_results=max_results)

# 세션 상태 초기화
if "raw_books" not in st.session_state:
    st.session_state.raw_books = []
if "processed_df" not in st.session_state:
    st.session_state.processed_df = pd.DataFrame()
if "owned_isbns" not in st.session_state:
    st.session_state.owned_isbns = set()

# API 기본 키 로드
default_aladin_key = os.getenv("ALADIN_TTB_KEY", "ttbsinddung1748001")
default_naru_key = os.getenv("INFO_NARU_KEY", "")
default_lib_code = os.getenv("DEFAULT_LIB_CODE", "141005")

# Header
st.markdown("""
<div class="main-header">
    <h1>📖 알라딘 API 도서관 신간 수서 지원 시스템</h1>
    <p>알라딘 서지 데이터 및 도서관 정보나루 소장 여부를 연동하여 신간도서 추천, 소장 중복 제어 및 발주 엑셀을 자동 생성합니다.</p>
</div>
""", unsafe_allow_html=True)

# --- 사이드바 설정 ---
with st.sidebar:
    st.title("⚙️ 수서 설정 대시보드")
    
    with st.expander("🔑 API 인증 키 관리", expanded=False):
        aladin_key = st.text_input("알라딘 TTB Key", value=default_aladin_key, type="password")
        naru_key = st.text_input("도서관 정보나루 Key", value=default_naru_key, type="password")
        
    manager = AladinAcquisitionManager(aladin_key, naru_key)
    
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

    st.subheader("🎯 수서 검색 대상")
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
    
    selected_category_name = st.selectbox(
        "분야 (카테고리)",
        options=list(manager.ALADIN_CATEGORIES.keys())
    )
    selected_category_id = manager.ALADIN_CATEGORIES[selected_category_name]
    
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
    fetch_btn = st.button("🚀 알라딘 신간 도서 수집 및 분석", type="primary", use_container_width=True)

# --- 메인 로직 처리 ---
if fetch_btn or (st.session_state.raw_books and st.session_state.processed_df.empty):
    if not aladin_key:
        st.error("⚠️ 알라딘 TTB Key를 입력해주세요!")
    else:
        start_time = time.time()
        with st.spinner("📡 알라딘 API 병렬 수집 및 소장 여부 확인 중..."):
            # 캐시된 수집 함수 활용
            raw_books = cached_fetch_aladin_books(
                aladin_key=aladin_key,
                query_type=selected_query_type,
                category_id=selected_category_id,
                max_results=max_fetch_count
            )
            st.session_state.raw_books = raw_books
            
            # 데이터 정제 및 필터링 & 소장여부 병렬 검사
            df = manager.process_acquisition_candidates(
                raw_books=raw_books,
                exclude_keywords=exclude_keywords,
                exclude_isbns=exclude_isbns,
                min_pub_date=min_pub_date,
                discount_rate=discount_rate_pct / 100.0,
                lib_code=lib_code,
                owned_isbns_set=st.session_state.owned_isbns,
                auto_exclude_owned=auto_exclude_owned
            )
            
            # 예산 제한 적용
            df = manager.apply_budget_cap(
                df,
                max_budget=total_budget_limit,
                max_books=max_books_target
            )
            
            st.session_state.processed_df = df
            elapsed = time.time() - start_time
            st.toast(f"⚡ 총 {len(df)}권 수서 후보 정제 완료! ({elapsed:.2f}초 소요)", icon="🚀")

# --- 메인 화면 렌더링 ---
df = st.session_state.processed_df

if df.empty:
    st.info("👈 왼쪽 사이드바에서 수서 조건 및 도서관 코드를 설정한 후 **[🚀 알라딘 신간 도서 수집 및 분석]** 버튼을 클릭하세요.")
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
    tab_list, tab_stats = st.tabs(["📑 수서 도서 목록 및 발주 관리", "📊 KDC 분야별 분포 및 분석"])

    with tab_list:
        top_bar_col1, top_bar_col2, top_bar_col3 = st.columns([2.5, 1.2, 1.3])
        with top_bar_col1:
            st.subheader("📚 선정 수서 목록 (소장 여부 연동)")
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
                file_name=f"알라딘_신간_수서발주목록_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True
            )

        # 데이터 에디터로 수정 가능하도록 제공
        edited_df = st.data_editor(
            df,
            column_config={
                "선정": st.column_config.CheckboxColumn("발주선정", help="체크 시 발주 목록에 포함됩니다."),
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
            disabled=["소장여부", "KDC코드", "KDC분류", "도서명", "저자", "출판사", "출간일", "정가", "추정실구입가", "판매지수", "ISBN13", "카테고리", "알라딘URL"],
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
st.caption("💡본 시스템은 알라딘 TTB Open API 및 도서관 정보나루 API 데이터를 활용하여 도서관 수서 업무를 지원합니다.")
