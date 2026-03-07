

# 1. Tree Structure

```
.
├── app/
│   ├── api/
│   │   └── v1/
│   │       ├── endpoints/
│   │       │   ├── login.py      # 로그인 & OAuth2 토큰 발행
│   │       │   └── user.py       # 회원가입 & /me 내 정보 조회
│   │       └── routers.py        # 모든 엔드포인트를 'users' 태그로 통합
│   ├── core/
│   │   ├── config.py             # 환경변수 (SECRET_KEY, TOKEN_EXP 등)
│   │   └── security.py           # 암호화 및 JWT 생성 로직
│   ├── crud/                     # 데이터베이스 CRUD 함수
│   │   └── user.py               # 유저 생성 및 조회 (get_user_by_id 포함)
│   ├── db/
│   │   ├── alembic.ini           # 마이그레이션 설정
│   │   ├── base_class.py         # Table명 자동 생성 기능이 포함된 Base
│   │   ├── migration/            # DB 변경 이력 관리
│   │   │   └── versions/         # 마이그레이션 파일들 저장소
│   │   └── session.py            # DB 엔진 및 세션 관리 (get_db)
│   ├── main.py                   # FastAPI 엔트리 포인트
│   ├── models/                   # SQLAlchemy 모델 (DB 테이블)
│   │   ├── init.py           # 모델 통합 참조
│   │   └── user.py               # User 테이블 (nickname, onboarding 필드)
│   └── schemas/                  # Pydantic 데이터 검증 모델
│       ├── token.py              # Token 및 TokenData 규격
│       └── user.py               # UserCreate, UserResponse 규격
├── docker-compose.yml            # Docker 환경 설정
└── requirements.txt              # 파이썬 의존성 패키지 목록

```

# 2. 요구사항
- Docker Desktop 설치 필수 (Python/DB 개별 설치 불필요)

# 3. 실행 방법

## 1. 서버 실행 (Build & Run)

```
최초 실행 또는 라이브러리 추가 시: docker-compose up --build

평상시 실행: docker-compose up

백그라운드 실행: docker-compose up -d
```

## 2. 실행 확인
- Swagger UI (API 문서): http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 3. 로그 확인

```
전체 로그: docker-compose logs -f

웹 서버 로그만: docker-compose logs -f web

로그 종료: Ctrl + C
```

## 4. 서버 종료

```
잠시 멈춤: docker-compose stop

컨테이너 삭제: docker-compose down

데이터 초기화 종료: docker-compose down -v
```

# 4. 데이터베이스 관리 (Migration)

## 1. 마이그레이션 적용 (테이블 생성/변경)

```
Mac / Git Bash:
docker-compose exec -w /back/app/db web alembic revision --autogenerate -m "message"
docker-compose exec -w /back/app/db web alembic upgrade head

Windows (CMD/PS):
docker-compose exec -w //back/app/db web alembic revision --autogenerate -m "message"
docker-compose exec -w //back/app/db web alembic upgrade head
```

# 5. DB 직접 접속 (CLI)
- docker exec -it PINLM_DB psql -U gookbob -d pinlm_db