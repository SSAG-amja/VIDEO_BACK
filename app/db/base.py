# app/db/base.py
from sqlalchemy.ext.declarative import as_declarative
from typing import Any

# 260409 김광원
# 테이블 이름 자동생성 지움 
@as_declarative()
class Base:
    id: Any
    __name__: str