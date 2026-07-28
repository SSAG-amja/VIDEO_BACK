# 20260305 박현식
FROM python:3.11-slim

# 작업 디렉토리 설정 (루트)
WORKDIR /back

# 필수 라이브러리 설치
# 2026.07.28 김광원: LightFM의 Cython 확장을 빌드하려면 gcc가 필요해 build-essential 추가.
RUN apt-get update && apt-get install -y netcat-openbsd build-essential && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사
COPY . .

RUN chmod +x /back/app/seeder.sh