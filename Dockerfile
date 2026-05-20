# 20260305 박현식
FROM python:3.11-slim

# 작업 디렉토리 설정
WORKDIR /back

# 필수 라이브러리 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사
COPY . .

# 서버 실행
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]