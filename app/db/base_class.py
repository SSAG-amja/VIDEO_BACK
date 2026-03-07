# app/db/base_class.py
from sqlalchemy.ext.declarative import as_declarative, declared_attr

@as_declarative()
class Base:
    id: any
    __name__: str

    # 20260305 박현식: 클래스 이름을 소문자로 바꿔 테이블 이름으로 자동 지정
    @declared_attr
    def __tablename__(cls) -> str:
        return cls.__name__.lower()