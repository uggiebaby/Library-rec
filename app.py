import streamlit as st
import pandas as pd
import numpy as np
import os
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from utils.yes24_acq import Yes24AcquisitionManager
from utils.api_handler import LibraryAPIHandler

# 환경변수 로드 및 기본 설정
load_dotenv()
st.set_page_config(
    page_title="도서관 스마트 신간 수서 지원 시스템 v2.0 (YES24 & 정보나루 EUI)",
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

# Streamlit 다중 카테고리 데이터 캐시 함수 (YES24 전용)
@st.cache_data(ttl=3600, show_spinner=False)
def cached_fetch_yes24_books_multi(yes24_key: str, query_type: str, category_ids: tuple, max_results: int, cache_ver: str = "v2.2"):
    mgr = Yes24AcquisitionManager(yes24_key)
    return mgr.fetch_yes24_books_multi(query_type=query_type, category_ids=list(category_ids), max_results=max_results)

# 세션 상태 초기화
if "raw_books" not in st.session_state:
    st.session_state.raw_books = []
if "processed_df" not in st.session_state:
    st.session_state.processed_df = pd.DataFrame()
if "owned_isbns" not in st.session_state:
    st.session_state.owned_isbns = set()

# API 기본 키 및 설정 로드
default_yes24_key = os.getenv("YES24_API_KEY", "yk_live_c811cfa89b848ebcea3aa45131edec3fdc23f1aab3cd728f")
default_naru_key = os.getenv("INFO_NARU_KEY", "")
default_lib_code = os.getenv("DEFAULT_LIB_CODE", "141005")

# --- 🔒 비밀번호(PIN) 잠금 인증 시스템 ---
def check_password():
    correct_pin = os.getenv("APP_PASSWORD", "1234")
    
    if st.session_state.get("authenticated", False):
        return True

    st.markdown("<br><br>", unsafe_allow_html=True)
    col_l, col_m, col_r = st.columns([1, 1.2, 1])
    with col_m:
        st.markdown("""
        <div style="background:#FFFFFF; padding:2rem; border-radius:16px; border:1px solid #E2E8F0; box-shadow:0 10px 25px -5px rgba(0,0,0,0.1); text-align:center; margin-bottom:1.5rem;">
            <div style="font-size:3rem; margin-bottom:0.5rem;">🔒</div>
            <h2 style="margin:0 0 0.5rem 0; color:#0F172A; font-size:1.5rem; font-weight:700;">도서관 수서 시스템 인증</h2>
            <p style="color:#64748B; font-size:0.95rem; margin:0;">관계자 전용 시스템입니다. 사서 접속 비밀번호(PIN)를 입력해주세요.</p>
        </div>
        """, unsafe_allow_html=True)

        with st.form("pin_login_form"):
            pin_input = st.text_input("접속 비밀번호 (PIN)", type="password", placeholder="비밀번호 입력...", label_visibility="collapsed")
            submit_btn = st.form_submit_button("🚀 시스템 접속하기", use_container_width=True, type="primary")
            
            if submit_btn:
                if pin_input.strip() == str(correct_pin).strip():
                    st.session_state["authenticated"] = True
                    st.success("✅ 인증 성공! 시스템에 접속합니다...")
                    time.sleep(0.3)
                    st.rerun()
                else:
                    st.error("❌ 비밀번호가 올바르지 않습니다. 다시 입력해주세요.")
    return False

if not check_password():
    st.stop()

# Header
st.markdown("""
<div class="main-header">
    <h1>📖 YES24 & 도서관정보나루 v2.0 지능형 이용기대율(EUI) 수서 시스템</h1>
    <p>YES24 Open API 서지 데이터 및 도서관 정보나루 빅데이터(연령/성별 대출, 급상승 트렌드)를 결합하여 이용기대율(EUI) 기반 최적 수서를 지원합니다.</p>
</div>
""", unsafe_allow_html=True)

# --- 사이드바 설정 ---
with st.sidebar:
    st.title("⚙️ 수서 설정 대시보드 (v2.0)")
    
    # 1. API 인증 키 관리 (YES24 & 정보나루)
    with st.expander("🔑 API 인증 키 관리", expanded=False):
        yes24_key = st.text_input("YES24 Live API Key", value=default_yes24_key, type="password")
        naru_key = st.text_input("도서관 정보나루 Key", value=default_naru_key, type="password")
        
    # YES24 매니저 및 정보나루 핸들러 초기화
    manager = Yes24AcquisitionManager(yes24_key, naru_key)
    api_handler = LibraryAPIHandler(naru_key, "")
    
    available_categories = Yes24AcquisitionManager.YES24_CATEGORIES
    query_types = {
        "BestsellerRealtime": "⚡ 실시간 베스트셀러 (인기순위 100)",
        "Bestseller": "🔥 종합 베스트셀러",
        "BestsellerMonthly": "📅 월간 베스트셀러",
        "NewBooksRecent": "🆕 최신 등록 도서 (신간)"
    }
    default_checked_cats = ["소설/시/희곡", "에세이", "유아", "어린이", "인문"]

    # 2. v2.0 지능형 이용기대율(EUI) 수서 설정
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
            w_sales = st.slider("예스24 판매지수 가중치", 0.0, 1.0, 0.35, 0.05)
            w_loans = st.slider("전국 대출건수 가중치", 0.0, 1.0, 0.25, 0.05)
            w_target = st.slider("타겟층 대출가중치", 0.0, 1.0, 0.25, 0.05)
            w_trend = st.slider("급상승 트렌드가중치", 0.0, 1.0, 0.15, 0.05)

            eui_weights = {
                "sales": w_sales,
                "loans": w_loans,
                "target": w_target,
                "trend": w_trend
            }

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
    st.subheader("📚 수서 검색 대상 (YES24)")
    selected_query_type = st.selectbox(
        "수서 도서 수집 유형",
        options=list(query_types.keys()),
        format_func=lambda x: query_types[x]
    )
    
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
                        key=f"cat_yes24_{idx}"
                    )
                    if is_checked:
                        selected_category_names.append(cat_name)

        if not selected_category_names:
            st.warning("⚠️ 선택된 분야가 없어 '전체 분야'로 수집됩니다.")
            selected_category_names = ["전체 분야"]

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
    days_ago = st.selectbox("출간 시점 필터", options=[30, 60, 90, 180, 365, 0], index=4, format_func=lambda x: f"최근 {x}일 이내 출간" if x > 0 else "제한 없음 (전체)")
    min_pub_date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d") if days_ago > 0 else ""
    
    allow_steady_sellers = st.checkbox(
        "⭐ 판매지수/대출율 우수 도서는 365일 지나도 포함 (스테디셀러 허용)",
        value=True,
        help="출간일 필터가 지나도 대출 순위나 판매지수가 압도적으로 높은 고품질 도서는 수서 후보에서 탈락하지 않도록 보호합니다."
    )

    with st.expander("🚫 제외 키워드 & ISBN 관리", expanded=False):
        exclude_kw_str = st.text_area(
            "제외할 키워드 (쉼표 구분, 도서명 및 YES24 카테고리 분류에 동시 적용)",
            value=", ".join(manager.DEFAULT_EXCLUDE_KEYWORDS),
            height=100
        )
        exclude_keywords = [k.strip() for k in exclude_kw_str.split(",") if k.strip()]
        
        exclude_isbn_str = st.text_area("제외할 ISBN (줄바꿈 구분)", placeholder="이미 소장 중이거나 제외할 ISBN13...")
        exclude_isbns = set(manager.clean_isbn(i) for i in exclude_isbn_str.splitlines() if i.strip())

    st.markdown("---")
    fetch_btn = st.button("🚀 YES24 v2.0 지능형 도서 수집 및 EUI 분석", type="primary", use_container_width=True)

    if st.button("🔄 캐시 초기화 및 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.session_state.processed_df = pd.DataFrame()
        st.session_state.raw_books = []
        st.success("캐시가 초기화되었습니다.")
        time.sleep(0.3)
        st.rerun()

    st.markdown("---")
    if st.button("🔒 로그아웃 (화면 잠금)", use_container_width=True):
        st.session_state["authenticated"] = False
        st.rerun()

# --- 메인 로직 처리 ---
if fetch_btn:
    if not yes24_key:
        st.error("⚠️ YES24 API Key를 입력해주세요!")
    else:
        start_time = time.time()
        cat_disp_count = len(selected_category_names)
        
        with st.status("🚀 YES24 & 정보나루 v2.0 수서 파이프라인 가동 중...", expanded=True) as status:
            st.write(f"📡 1단계: YES24 API {cat_disp_count}개 분야 병렬 수집 중...")
            raw_books = cached_fetch_yes24_books_multi(
                yes24_key=yes24_key,
                query_type=selected_query_type,
                category_ids=tuple(selected_category_ids),
                max_results=max_fetch_count
            )
            st.session_state.raw_books = raw_books

            # 정보나루 타겟층 대출 및 트렌드 데이터 수집
            target_loan_map = {}
            trend_loan_map = {}
            if naru_key:
                st.write(f"👥 2단계: 도서관정보나루 '{target_age_label} / {target_gender_label}' 대출 빅데이터 연동...")
                target_code = LibraryAPIHandler.AGE_OPTIONS.get(target_age_label, "20")
                gender_code = LibraryAPIHandler.GENDER_OPTIONS.get(target_gender_label, "0")
                target_loan_map = api_handler.fetch_target_demographic_loans(age=target_code, gender=gender_code)

                if use_hot_trend:
                    st.write("🔥 3단계: 최근 공공도서관 대출 급상승(라이징) 트렌드 분석...")
                    trend_loan_map = api_handler.fetch_hot_trends()

            st.write("⚖️ 4단계: 이용기대율(EUI) 스코어링 및 소장 여부 병렬 검증...")
            target_label_str = f"{target_age_label[:3]}대 {target_gender_label[:1]}"
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
                target_label=target_label_str,
                allow_steady_sellers=allow_steady_sellers
            )

            # 예산 제한 적용
            st.write("💰 5단계: 예산 및 목표 권수 최적 캡 적용...")
            df = manager.apply_budget_cap(
                df,
                max_budget=total_budget_limit,
                max_books=max_books_target
            )
            
            st.session_state.processed_df = df
            status.update(label="✅ YES24 v2.0 수서 분석 완료!", state="complete", expanded=False)

        elapsed = time.time() - start_time
        cat_desc = ", ".join(selected_category_names[:2]) + (f" 외 {len(selected_category_names)-2}개" if len(selected_category_names) > 2 else "")
        st.toast(f"⚡ [YES24 - {cat_desc}] 총 {len(df)}권 v2.0 EUI 수서 정제 완료! ({elapsed:.2f}초)", icon="🚀")

# --- 메인 화면 렌더링 ---
df = st.session_state.processed_df

if df.empty:
    st.info("👈 왼쪽 사이드바에서 수서 조건 및 EUI 설정을 확인한 후 **[🚀 YES24 v2.0 지능형 도서 수집 및 EUI 분석]** 버튼을 클릭하세요.")
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
    avg_eui = selected_df["EUI점수"].mean() if ("EUI점수" in selected_df.columns and not selected_df.empty) else 0.0

    m_col1, m_col2, m_col3, m_col4, m_col5, m_col6 = st.columns(6)
    with m_col1:
        st.markdown(f'<div class="metric-card"><div class="metric-label">총 후보 도서</div><div class="metric-value">{total_candidates:,}권</div></div>', unsafe_allow_html=True)
    with m_col2:
        st.markdown(f'<div class="metric-card"><div class="metric-label">최종 수서 선정</div><div class="metric-value" style="color:#2563EB;">{selected_count:,}권</div></div>', unsafe_allow_html=True)
    with m_col3:
        st.markdown(f'<div class="metric-card"><div class="metric-label">평균 이용기대율(EUI)</div><div class="metric-value" style="color:#7C3AED;">{avg_eui:.1f}점</div></div>', unsafe_allow_html=True)
    with m_col4:
        st.markdown(f'<div class="metric-card"><div class="metric-label">이미 소장 중</div><div class="metric-value" style="color:#D97706;">{owned_count:,}권</div></div>', unsafe_allow_html=True)
    with m_col5:
        st.markdown(f'<div class="metric-card"><div class="metric-label">예상 실구입가 ({100-discount_rate_pct}%)</div><div class="metric-value" style="color:#059669;">{total_est_price:,}원</div></div>', unsafe_allow_html=True)
    with m_col6:
        color = "#DC2626" if budget_usage > 100 else "#2563EB"
        st.markdown(f'<div class="metric-card"><div class="metric-label">예산 집행률</div><div class="metric-value" style="color:{color};">{budget_usage:.1f}%</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # 2. 통계 시각화 및 액션 탭
    tab_list, tab_stats = st.tabs(["📑 수서 도서 목록 및 발주 관리", "📊 KDC 분야별 분포 및 EUI 분석"])

    with tab_list:
        top_bar_col1, top_bar_col2, top_bar_col3 = st.columns([2.5, 1.2, 1.3])
        with top_bar_col1:
            st.subheader("📚 선정 수서 목록 (YES24 & EUI v2.0 연동)")
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
                file_name=f"YES24_v2.0_수서발주목록_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True
            )

        # 데이터 에디터 컬럼 설정
        col_config = {
            "선정": st.column_config.CheckboxColumn("발주선정", help="체크 시 발주 목록에 포함됩니다."),
            "EUI점수": st.column_config.NumberColumn("이용기대율(EUI)", format="%.1f점", help="서점 판매지수, 전국 대출건수, 타겟층 대출, 급상승 트렌드를 종합 연산한 기대율 점수"),
            "추천사유": st.column_config.TextColumn("추천 사유 뱃지", width="medium"),
            "소장여부": st.column_config.TextColumn("도서관 소장", help="정보나루 API 또는 업로드된 소장목록 기준"),
            "KDC분류": st.column_config.TextColumn("KDC 분야", width="medium"),
            "YES24분류": st.column_config.TextColumn("YES24 카테고리", width="medium", help="YES24 원본 분야 분류"),
            "도서명": st.column_config.TextColumn("도서명", width="large"),
            "저자": st.column_config.TextColumn("저자", width="medium"),
            "출판사": st.column_config.TextColumn("출판사", width="medium"),
            "판매지수": st.column_config.NumberColumn("🔥 판매지수", format="%d", help="예스24 공식 판매지수 (실시간 인기도 반영)", width="medium"),
            "정가": st.column_config.NumberColumn("정가(원)", format="%d원"),
            "추정실구입가": st.column_config.NumberColumn("실구입가(원)", format="%d원"),
            "출간일": st.column_config.TextColumn("출간일"),
            "예스24URL": st.column_config.LinkColumn("예스24 상세", display_text="보기"),
            "ISBN13": st.column_config.TextColumn("ISBN13"),
        }

        desired_order = [
            "선정", "EUI점수", "추천사유", "소장여부", "KDC분류", "YES24분류", "도서명", "저자", "출판사", 
            "판매지수", "정가", "추정실구입가", "출간일", "예스24URL", "ISBN13"
        ]
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
st.caption("💡 본 시스템은 YES24 Open API 및 도서관 정보나루 빅데이터 API를 활용한 v2.0 지능형 수서 솔루션입니다.")
