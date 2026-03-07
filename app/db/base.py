# app/db/base.py

# 20260305 박현식
# alembic이 모든 모델을 한 번에 인식할 수 있도록 모아주는 역할
# 여기서 Base를 직접 정의하거나, 공통 클래스를 가져와야 함

from sqlalchemy.ext.declarative import as_declarative, declared_attr
from typing import Any

@as_declarative()
class Base:
    id: Any
    __name__: str

    # 20260305 박현식
    # 클래스 이름을 소문자로 변환하여 자동으로 테이블 이름 생성
    @declared_attr
    def __tablename__(cls) -> str:
        return cls.__name__.lower()

# 20260305 박현식
# 마이그레이션 대상이 되는 모든 모델들을 여기에 임포트함
# (현재는 유저 모델만 존재)
from app.models.user import User