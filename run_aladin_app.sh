#!/bin/bash
echo "📖 알라딘 API 기반 도서관 신간 수서 지원 시스템 실행 중..."

# streamlit 명령어 위치 자동 감지
STREAMLIT_CMD=$(which streamlit 2>/dev/null || echo "/opt/anaconda3/bin/streamlit")

if [ -f "$STREAMLIT_CMD" ] || command -v streamlit &> /dev/null; then
    $STREAMLIT_CMD run app.py
else
    python3 -m streamlit run app.py
fi
