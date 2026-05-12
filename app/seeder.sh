#!/bin/bash
set -e

MARKER_FILE="/back/app/data/.init_completed"

if [ "$FORCE_INIT" = "true" ]; then
    echo "⚠️ [FORCE_INIT=true] 강제 초기화 명령이 감지되었습니다. 기존 마커 파일을 무시합니다."
    rm -f "$MARKER_FILE"
fi

if [ -f "$MARKER_FILE" ]; then
    echo "⏩ [SKIP] 초기화 마커 파일($MARKER_FILE)이 존재합니다."
    echo "⏩ [SKIP] Alembic 마이그레이션과 데이터 시딩(Seeding)을 건너뜁니다."
    exit 0
fi

echo "⏳ [1/4] PostgreSQL ${DB_PORT:-5432} 포트 응답 대기 중..."
while ! nc -z db ${DB_PORT:-5432}; do
  sleep 1
done
echo "✅ [1/4] PostgreSQL 연결 확인 완료!"

echo "🛠️ [2/4] Alembic 데이터베이스 마이그레이션 실행 중..."
cd /back
alembic -c /back/alembic.ini upgrade head
echo "✅ [2/4] 마이그레이션 완료!"

echo "🚀 [3/4] 데이터 시딩(Seeding) 스크립트를 시작합니다..."
python -m app.seed_db
echo "✅ [3/4] 데이터 시딩 완료!"

touch "$MARKER_FILE"

echo "🏁 [4/4] 데이터베이스 초기화 작업이 완벽하게 종료되었습니다. 마커 파일 생성 완료."
