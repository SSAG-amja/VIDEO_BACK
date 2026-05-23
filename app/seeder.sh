#!/bin/bash
set -e

MARKER_FILE="/back/app/data/.init_completed"

if [ "$FORCE_INIT" = "true" ]; then
    echo "⚠️ [FORCE_INIT=true] 강제 초기화 명령이 감지되었습니다. 기존 마커 파일을 무시합니다."
    rm -f "$MARKER_FILE"
fi

if [ -f "$MARKER_FILE" ]; then
    echo "🔎 [CHECK] 초기화 마커 파일($MARKER_FILE)이 존재합니다. DB 스키마 상태를 확인합니다."
    set +e
    python - <<'PY'
import os
import sys
import time

import psycopg2

conninfo = (
    f"host={os.environ.get('DB_HOST', 'db')} "
    f"port={os.environ.get('DB_PORT', '5432')} "
    f"dbname={os.environ['DB_NAME']} "
    f"user={os.environ['DB_USER']} "
    f"password={os.environ['DB_PASSWORD']}"
)

for _ in range(30):
    try:
        with psycopg2.connect(conninfo) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name IN ('users', 'movies')
                    """
                )
                existing = {row[0] for row in cur.fetchall()}
        missing = {'users', 'movies'} - existing
        if missing:
            print(f"⚠️ [CHECK] 마커는 있지만 핵심 테이블이 없습니다: {sorted(missing)}")
            sys.exit(1)
        print("✅ [CHECK] 핵심 테이블(users, movies)이 존재합니다.")
        sys.exit(0)
    except Exception as exc:
        last_exc = exc
        time.sleep(1)

print(f"⚠️ [CHECK] DB 스키마 확인 실패: {last_exc}")
sys.exit(1)
PY
    schema_check_status=$?
    set -e
    if [ $schema_check_status -eq 0 ]; then
        echo "⏩ [SKIP] Alembic 마이그레이션과 데이터 시딩(Seeding)을 건너뜁니다."
        exit 0
    fi

    echo "♻️ [RESET] 마커 파일이 DB 상태와 맞지 않아 삭제 후 초기화를 다시 진행합니다."
    rm -f "$MARKER_FILE"
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
