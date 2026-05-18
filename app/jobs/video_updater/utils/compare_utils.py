import re


# 26.05.17 김광원
# 외부 데이터와 DB 문자열을 보수적으로 비교하기 위해 정규화한다.
def normalize_compare_text(value) -> str:
    if value is None:
        return ""

    normalized = str(value).casefold()
    normalized = re.sub(r"\s*,\s*", ",", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


# 26.05.17 김광원
# 정규화된 문자열 기준으로 중복 없는 row 조회 맵을 만든다.
def build_normalized_lookup(rows, attribute: str):
    lookup = {}

    for row in rows:
        value = getattr(row, attribute)
        if value is None:
            continue

        key = normalize_compare_text(value)
        if key in lookup:
            lookup[key] = None
            continue

        lookup[key] = row

    return lookup
