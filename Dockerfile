# LightFM is distributed as source for Python 3.11. Build the V3/V4 dependency
# wheels in the full image, then keep compilers out of runtime.
FROM python:3.11 AS recsys-wheel-builder

WORKDIR /wheels

COPY requirements-recsys-v3.txt requirements-recsys-v4.txt ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels \
    -r requirements-recsys-v3.txt \
    -r requirements-recsys-v4.txt


# 20260305 박현식
FROM python:3.11-slim

# 작업 디렉토리 설정 (루트)
WORKDIR /back

# 필수 라이브러리 설치
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-recsys-v3.txt requirements-recsys-v4.txt ./
COPY --from=recsys-wheel-builder /wheels /wheels
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --no-index --find-links=/wheels \
        -r requirements-recsys-v3.txt \
        -r requirements-recsys-v4.txt \
    && rm -rf /wheels

# 소스 코드 복사
COPY . .

RUN chmod +x /back/app/seeder.sh
