class DiffCalculator:
    # 26.05.17 김광원
    # DB 집합과 API 집합을 비교해 추가/삭제 대상을 분리한다.
    @staticmethod
    def get_delta(db_set: set, api_set: set) -> tuple[list, list]:
        return list(api_set - db_set), list(db_set - api_set)
