# 20260305 박현식
FROM python:3.11-slim

# 작업 디렉토리 설정 (루트)
WORKDIR /back

# 필수 라이브러리 설치
RUN apt-get update && apt-get install -y netcat-openbsd && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사
COPY . .

RUN chmod +x /back/app/seeder.sh