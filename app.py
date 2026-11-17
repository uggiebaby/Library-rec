import streamlit as st
import pandas as pd
import numpy as np
import os
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from utils.aladin_acq import AladinAcquisitionManager
from utils.yes24_acq import Yes24AcquisitionManager

# 환경변수 로드 및 기본 설정
load_dotenv()
st.set_page_config(
    page_title="도서관 스마트 신간 수서 지원 시스템 (YES24 / 알라딘)",
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

# Streamlit 다중 카테고리 데이터 캐시 함수 (v2.1 판매지수 detail=Y 포함)
@st.cache_data(ttl=3600, show_spinner=False)
def cached_fetch_yes24_books_multi(yes24_key: str, query_type: str, category_ids: tuple, max_results: int, cache_ver: str = "v2.1"):
    mgr = Yes24AcquisitionManager(yes24_key)
    return mgr.fetch_yes24_books_multi(query_type=query_type, category_ids=list(category_ids), max_results=max_results)

@st.cache_data(ttl=3600, show_spinner=False)
def cached_fetch_aladin_books_multi(aladin_key: str, query_type: str, category_ids: tuple, max_results: int, cache_ver: str = "v2.1"):
    mgr = AladinAcquisitionManager(aladin_key)
    return mgr.fetch_aladin_books_multi(query_type=query_type, category_ids=list(category_ids), max_results=max_results)

# 세션 상태 초기화
if "raw_books" not in st.session_state:
    st.session_state.raw_books = []
if "processed_df" not in st.session_state:
    st.session_state.processed_df = pd.DataFrame()
if "owned_isbns" not in st.session_state:
    st.session_state.owned_isbns = set()
if "active_source" not in st.session_state:
    st.session_state.active_source = "YES24"

# 세션에 이전 "인기도점수" 컬럼이 남아있다면 즉시 "판매지수"로 이름 변경
if not st.session_state.processed_df.empty and "인기도점수" in st.session_state.processed_df.columns:
    st.session_state.processed_df = st.session_state.processed_df.rename(columns={"인기도점수": "판매지수"})

# API 기본 키 및 설정 로드
default_yes24_key = os.getenv("YES24_API_KEY", "yk_live_c811cfa89b848ebcea3aa45131edec3fdc23f1aab3cd728f")
default_aladin_key = os.getenv("ALADIN_TTB_KEY", "ttbsinddung1748001")
default_naru_key = os.getenv("INFO_NARU_KEY", "")
default_lib_code = os.getenv("DEFAULT_LIB_CODE", "141005")

# Header
st.markdown("""
<div class="main-header">
    <h1>📖 도서관 스마트 신간 수서 지원 시스템</h1>
    <p>YES24 Open API 및 도서관 정보나루 소장 여부를 연동하여 신간도서 추천, 소장 중복 제어, KDC 정밀 분류 및 발주 엑셀을 자동 생성합니다.</p>
</div>
""", unsafe_allow_html=True)

# --- 사이드바 설정 ---
with st.sidebar:
    st.title("⚙️ 수서 설정 대시보드")
    
    # 1. 수서 데이터 소스 선택
    selected_source = st.radio(
        "🌐 수서 데이터 소스 선택",
        options=["YES24 (추천 / 신규 API)", "알라딘 (기존)"],
        index=0,
        help="알라딘 API 이용 중단에 따라 공식 YES24 Open API(일 20,000회)가 기본 탑재되었습니다."
    )
    is_yes24 = selected_source.startswith("YES24")
    current_source_name = "YES24" if is_yes24 else "알라딘"

    # 2. API 인증 키 관리
    with st.expander("🔑 API 인증 키 관리", expanded=False):
        if is_yes24:
            yes24_key = st.text_input("YES24 Live API Key", value=default_yes24_key, type="password")
            aladin_key = default_aladin_key
        else:
            aladin_key = st.text_input("알라딘 TTB Key", value=default_aladin_key, type="password")
            yes24_key = default_yes24_key
        naru_key = st.text_input("도서관 정보나루 Key", value=default_naru_key, type="password")
        
    # 매니저 객체 생성
    if is_yes24:
        manager = Yes24AcquisitionManager(yes24_key, naru_key)
        available_categories = Yes24AcquisitionManager.YES24_CATEGORIES
        query_types = {
            "BestsellerRealtime": "⚡ 실시간 베스트셀러 (인기순위 100)",
            "Bestseller": "🔥 종합 베스트셀러",
            "BestsellerMonthly": "📅 월간 베스트셀러",
            "NewBooksRecent": "🆕 최신 등록 도서 (신간)"
        }
        default_checked_cats = ["소설/시/희곡", "에세이", "유아", "어린이", "인문"]
    else:
        manager = AladinAcquisitionManager(aladin_key, naru_key)
        available_categories = AladinAcquisitionManager.ALADIN_CATEGORIES
        query_types = {
            "ItemNewSpecial": "🌟 주목할 만한 신간 (추천)",
            "ItemNewAll": "🆕 전체 신간",
            "Bestseller": "🔥 베스트셀러",
            "BlogBest": "✍️ 블로거 추천 도서"
        }
        default_checked_cats = ["소설/시/희곡", "에세이", "유아", "아동 (어린이)"]

    # 3. 소장 도서 설정
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

    # 4. 수서 검색 대상
    st.subheader(f"🎯 수서 검색 대상 ({current_source_name})")
    selected_query_type = st.selectbox(
        "수서 도서 수집 유형",
        options=list(query_types.keys()),
        format_func=lambda x: query_types[x]
    )
    
    # 카테고리 복수 선택 UI
    with st.expander("📚 수서 분야 선택 (체크박스 복수 선택)", expanded=True):
        all_categories = [c for c in available_categories.keys() if c != "전체 분야"]
        
        chk_col1, chk_col2 = st.columns([1.2, 1])
        select_all = chk_col1.checkbox("전체 분야 선택", value=False, help="체크 시 모든 분야의 도서를 수집합니다.")
        
        selected_category_names = []
        if select_all:
            selected_category_names = ["전체 분야"]
            st.caption("✨ '전체 분야'가 선택되었습니다.")
        else:
            cat_cols = st.columns(2)
            for idx, cat_name in enumerate(all_categories):
                with cat_cols[idx % 2]:
                    is_checked = st.checkbox(
                        cat_name, 
                        value=(cat_name in default_checked_cats), 
                        key=f"cat_{current_source_name}_{idx}"
                    )
                    if is_checked:
                        selected_category_names.append(cat_name)

        if not selected_category_names:
            st.warning("⚠️ 선택된 분야가 없어 '전체 분야'로 수집됩니다.")
            selected_category_names = ["전체 분야"]

    # 선택된 카테고리 ID 리스트 변환
    selected_category_ids = [available_categories[cat] for cat in selected_category_names]
    max_fetch_count = st.slider("수집 대상 도서 수", min_value=50, max_value=500, value=200, step=50)

    # 5. 예산 및 수서 조건
    st.markdown("---")
    st.subheader("💰 예산 및 수서 조건")
    
    col_b1, col_b2 = st.columns(2)
    with col_b1:
        discount_rate_pct = st.number_input("수서 할인율 (%)", min_value=0, max_value=50, value=10, step=1)
    with col_b2:
        max_books_target = st.number_input("목표 수서 권수", min_value=0, max_value=1000, value=50, step=10)
        
    total_budget_limit = st.number_input("수서 상한 예산액 (원, 0=무제한)", min_value=0, value=1000000, step=100000)

    # 6. 출간일 조건
    st.markdown("---")
    st.subheader("📅 출간일 조건")
    days_ago = st.selectbox("출간 시점 필터", options=[30, 60, 90, 180, 365, 0], index=1, format_func=lambda x: f"최근 {x}일 이내 출간" if x > 0 else "제한 없음 (전체)")
    min_pub_date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d") if days_ago > 0 else ""

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
    fetch_btn = st.button(f"🚀 {current_source_name} 도서 수집 및 분석", type="primary", use_container_width=True)

    # 캐시 초기화 버튼
    if st.button("🔄 캐시 초기화 및 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.session_state.processed_df = pd.DataFrame()
        st.session_state.raw_books = []
        st.success("캐시가 초기화되었습니다.")
        time.sleep(0.3)
        st.rerun()

# --- 메인 로직 처리 ---
if fetch_btn:
    api_key_to_check = yes24_key if is_yes24 else aladin_key
    if not api_key_to_check:
        st.error(f"⚠️ {current_source_name} API Key를 입력해주세요!")
    else:
        start_time = time.time()
        cat_disp_count = len(selected_category_names)
        with st.spinner(f"📡 {current_source_name} API {cat_disp_count}개 분야 병렬 수집 및 정보나루 소장 여부 확인 중..."):
            if is_yes24:
                raw_books = cached_fetch_yes24_books_multi(
                    yes24_key=yes24_key,
                    query_type=selected_query_type,
                    category_ids=tuple(selected_category_ids),
                    max_results=max_fetch_count
                )
            else:
                raw_books = cached_fetch_aladin_books_multi(
                    aladin_key=aladin_key,
                    query_type=selected_query_type,
                    category_ids=tuple(selected_category_ids),
                    max_results=max_fetch_count
                )
            st.session_state.raw_books = raw_books
            st.session_state.active_source = current_source_name
            
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
            cat_desc = ", ".join(selected_category_names[:2]) + (f" 외 {len(selected_category_names)-2}개" if len(selected_category_names) > 2 else "")
            st.toast(f"⚡ [{current_source_name} - {cat_desc}] 총 {len(df)}권 수서 후보 정제 완료! ({elapsed:.2f}초 소요)", icon="🚀")

# --- 메인 화면 렌더링 ---
df = st.session_state.processed_df

if df.empty:
    st.info(f"👈 왼쪽 사이드바에서 수서 데이터 소스(현재: **{current_source_name}**) 및 조건 선택 후 **[🚀 {current_source_name} 도서 수집 및 분석]** 버튼을 클릭하세요.")
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
            active_src = st.session_state.get("active_source", current_source_name)
            st.subheader(f"📚 선정 수서 목록 ({active_src} 서지 연동)")
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
            active_src = st.session_state.get("active_source", "도서관")
            st.download_button(
                label="📥 엑셀 발주서 다운로드",
                data=excel_bytes,
                file_name=f"{active_src}_신간_수서발주목록_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True
            )

        # 데이터 에디터 컬럼 설정
        col_config = {
            "선정": st.column_config.CheckboxColumn("발주선정", help="체크 시 발주 목록에 포함됩니다."),
            "소장여부": st.column_config.TextColumn("도서관 소장", help="정보나루 API 또는 업로드된 소장목록 기준"),
            "KDC분류": st.column_config.TextColumn("KDC 분야", width="medium"),
            "도서명": st.column_config.TextColumn("도서명", width="large"),
            "저자": st.column_config.TextColumn("저자", width="medium"),
            "출판사": st.column_config.TextColumn("출판사", width="medium"),
            "판매지수": st.column_config.NumberColumn("🔥 판매지수", format="%d", help="서점 공식 판매지수 (실시간 인기도 반영)", width="medium"),
            "정가": st.column_config.NumberColumn("정가(원)", format="%d원"),
            "추정실구입가": st.column_config.NumberColumn("실구입가(원)", format="%d원"),
            "출간일": st.column_config.TextColumn("출간일"),
            "ISBN13": st.column_config.TextColumn("ISBN13"),
        }
        if "예스24URL" in df.columns:
            col_config["예스24URL"] = st.column_config.LinkColumn("예스24 상세", display_text="보기")
        if "알라딘URL" in df.columns:
            col_config["알라딘URL"] = st.column_config.LinkColumn("알라딘 상세", display_text="보기")

        # 판매지수가 바로 보이도록 최적화된 컬럼 배치 순서 지정
        desired_order = [
            "선정", "소장여부", "KDC분류", "도서명", "저자", "출판사", 
            "판매지수", "정가", "추정실구입가", "출간일"
        ]
        if "예스24URL" in df.columns:
            desired_order.append("예스24URL")
        elif "알라딘URL" in df.columns:
            desired_order.append("알라딘URL")
        desired_order.append("ISBN13")

        final_column_order = [c for c in desired_order if c in df.columns]
        disabled_cols = [c for c in df.columns if c != "선정"]

        edited_df = st.data_editor(
            df,
            column_order=final_column_order,
            column_config=col_config,
            disabled=disabled_cols,
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
st.caption("💡 본 시스템은 YES24 Open API 및 도서관 정보나루 API 데이터를 활용하여 도서관 수서 업무를 지원합니다.")
