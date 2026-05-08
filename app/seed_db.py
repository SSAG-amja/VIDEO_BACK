import glob
import io
import logging
import time
import urllib.parse
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2 import sql

from app.core.config import settings


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


DATABASE_URL = settings.DATABASE_URL
parsed_url = urllib.parse.urlparse(DATABASE_URL)
DB_CONFIG = {
    "database": parsed_url.path[1:],
    "user": parsed_url.username,
    "password": parsed_url.password,
    "host": parsed_url.hostname,
    "port": parsed_url.port,
}


DATA_DIR = Path(__file__).resolve().parent / "data"


# 부모 테이블은 CSV의 원본 id를 그대로 DB id/tmdb_id에 넣는다.
# 이렇게 해야 후속 매핑 CSV의 movie_id / actor_id / genre_id 같은 값이
# 별도 변환 없이 바로 FK를 만족시킬 수 있다.
PARENT_TABLE_SPECS = [
    {
        "pattern": "genres*.csv",
        "table": "genres",
        "mapping": {
            "id": "source_id",
            "name": "name",
            "name_ko": "name_ko",
        },
        "target_columns": ["id", "tmdb_id", "name", "name_ko"],
    },
    {
        "pattern": "keyword_ids_*.csv",
        "table": "keywords",
        "mapping": {
            "id": "source_id",
            "name": "name",
        },
        "target_columns": ["id", "tmdb_id", "name"],
    },
    {
        "pattern": "otts*.csv",
        "table": "otts",
        "mapping": {
            "id": "source_id",
            "name": "name",
            "name_ko": "name_ko",
        },
        "target_columns": ["id", "tmdb_id", "name", "name_ko"],
    },
    {
        "pattern": "people*.csv",
        "table": "people",
        "mapping": {
            "id": "source_id",
            "name": "name",
        },
        "target_columns": ["id", "tmdb_id", "name", "name_ko"],
    },
    {
        "pattern": "movies*.csv",
        "table": "movies",
        "mapping": {
            "id": "source_id",
            "title": "title",
            "vote_average": "vote_average",
            "vote_count": "vote_count",
            "status": "status",
            "release_date": "release_date",
            "revenue": "revenue",
            "runtime": "runtime",
            "adult": "adult",
            "backdrop_path": "backdrop_path",
            "budget": "budget",
            "imdb_id": "imdb_id",
            "original_language": "original_language",
            "original_title": "original_title",
            "overview": "overview",
            "popularity": "popularity",
            "poster_path": "poster_path",
            "title_ko": "title_ko",
        },
        "target_columns": [
            "id",
            "tmdb_id",
            "title",
            "vote_average",
            "vote_count",
            "status",
            "release_date",
            "revenue",
            "runtime",
            "adult",
            "backdrop_path",
            "budget",
            "imdb_id",
            "original_language",
            "original_title",
            "overview",
            "popularity",
            "poster_path",
            "title_ko",
        ],
    },
]


# 매핑 테이블은 부모 테이블 적재가 끝난 뒤에만 넣는다.
# 일부 CSV에는 PK 중복이나 부모 미존재 FK가 섞여 있으므로
# 적재 전에 중복 제거 + FK 존재 검증을 수행한다.
MAPPING_TABLE_SPECS = [
    {
        "pattern": "movie_actor_mapping*.csv",
        "table": "movie_actors",
        "mapping": {
            "movie_id": "movie_id",
            "actor_id": "actor_id",
            "cast": "cast_name",
        },
        "target_columns": ["movie_id", "actor_id", "cast_name"],
        "pk_columns": ["movie_id", "actor_id"],
        "fk_refs": [
            {"column": "movie_id", "ref_table": "movies", "ref_column": "id"},
            {"column": "actor_id", "ref_table": "people", "ref_column": "id"},
        ],
    },
    {
        "pattern": "movie_director_mapping*.csv",
        "table": "movie_directors",
        "mapping": {
            "movie_id": "movie_id",
            "director_id": "director_id",
        },
        "target_columns": ["movie_id", "director_id"],
        "pk_columns": ["movie_id", "director_id"],
        "fk_refs": [
            {"column": "movie_id", "ref_table": "movies", "ref_column": "id"},
            {"column": "director_id", "ref_table": "people", "ref_column": "id"},
        ],
    },
    {
        "pattern": "movie_genre_mapping*.csv",
        "table": "movie_genres",
        "mapping": {
            "movie_id": "movie_id",
            "genre_id": "genre_id",
        },
        "target_columns": ["movie_id", "genre_id"],
        "pk_columns": ["movie_id", "genre_id"],
        "fk_refs": [
            {"column": "movie_id", "ref_table": "movies", "ref_column": "id"},
            {"column": "genre_id", "ref_table": "genres", "ref_column": "id"},
        ],
    },
    {
        "pattern": "movie_keywords_mapping*.csv",
        "table": "movie_keywords",
        "mapping": {
            "movie_id": "movie_id",
            "keyword_id": "keyword_id",
        },
        "target_columns": ["movie_id", "keyword_id"],
        "pk_columns": ["movie_id", "keyword_id"],
        "fk_refs": [
            {"column": "movie_id", "ref_table": "movies", "ref_column": "id"},
            {"column": "keyword_id", "ref_table": "keywords", "ref_column": "id"},
        ],
    },
    {
        "pattern": "movie_ott_mapping*.csv",
        "table": "movie_otts",
        "mapping": {
            "movie_id": "movie_id",
            "ott_id": "ott_id",
            "is_streaming": "is_streaming",
            "is_rent": "is_rent",
            "is_buy": "is_buy",
        },
        "target_columns": ["movie_id", "ott_id", "is_streaming", "is_rent", "is_buy"],
        "pk_columns": ["movie_id", "ott_id"],
        "fk_refs": [
            {"column": "movie_id", "ref_table": "movies", "ref_column": "id"},
            {"column": "ott_id", "ref_table": "otts", "ref_column": "id"},
        ],
    },
]


# 적재 순서는 FK 제약을 기준으로 역순 truncate / 정순 load를 맞춘다.
ALL_TABLES_IN_LOAD_ORDER = [
    "movie_actors",
    "movie_directors",
    "movie_genres",
    "movie_keywords",
    "movie_otts",
    "people",
    "genres",
    "keywords",
    "otts",
    "movies",
]


def load_csv(file_path):
    """BOM이 섞인 CSV까지 안전하게 읽는다."""
    return pd.read_csv(file_path, encoding="utf-8-sig")


def find_latest_file(pattern):
    """패턴과 일치하는 파일 중 정렬상 마지막 파일을 사용한다."""
    matched_files = sorted(glob.glob(str(DATA_DIR / pattern)))
    if not matched_files:
        return None
    return matched_files[-1]


def normalize_dataframe(df):
    """pandas의 NaN/NaT를 PostgreSQL COPY에서 다룰 수 있게 None으로 통일한다."""
    df = df.copy()
    df = df.where(pd.notnull(df), None)
    return df


def drop_invalid_rows(df, required_columns, table_name, file_path):
    """필수 컬럼이 비어 있는 행은 미리 제거해 NOT NULL 오류를 막는다."""
    if not required_columns:
        return df

    invalid_mask = pd.Series(False, index=df.index)
    for col in required_columns:
        invalid_mask |= df[col].isna()

    dropped_count = int(invalid_mask.sum())
    if dropped_count:
        logger.warning(
            f"⚠️ {table_name}: 필수 컬럼 누락 행 {dropped_count}건을 제외합니다. "
            f"(source={file_path}, columns={required_columns})"
        )
        df = df.loc[~invalid_mask].copy()

    return df


def prepare_parent_dataframe(spec, file_path):
    """
    부모 테이블 적재용 DataFrame을 준비한다.

    규칙:
    - CSV 원본 id를 DB id/tmdb_id에 그대로 복제
    - people.csv는 name_ko가 없으므로 name으로 대체
    - 필수 컬럼 누락 행은 사전에 제거
    """
    raw_df = load_csv(file_path)
    df = raw_df.rename(columns=spec["mapping"])[list(spec["mapping"].values())]
    df = normalize_dataframe(df)

    df["id"] = df["source_id"]
    df["tmdb_id"] = df["source_id"]

    if spec["table"] == "people":
        df["name_ko"] = df["name"]
        required_columns = ["id", "tmdb_id", "name", "name_ko"]
    elif spec["table"] == "movies":
        required_columns = ["id", "tmdb_id"]
    else:
        required_columns = [col for col in spec["target_columns"] if col not in {"tmdb_id"}]

    df = drop_invalid_rows(df, required_columns, spec["table"], file_path)
    if df.empty:
        logger.warning(f"⚠️ {spec['table']}: 유효한 행이 없어 적재를 건너뜁니다. (source={file_path})")
        return None

    return df[spec["target_columns"]]


def prepare_mapping_dataframe(spec, file_path):
    """
    매핑 테이블 적재용 DataFrame을 준비한다.

    규칙:
    - 필수 FK 컬럼이 비어 있으면 제거
    - 복합 PK 중복은 첫 행만 남기고 제거
    """
    raw_df = load_csv(file_path)
    df = raw_df.rename(columns=spec["mapping"])[list(spec["mapping"].values())]
    df = normalize_dataframe(df)
    required_columns = [col for col in spec["target_columns"] if col not in {"cast_name"}]
    df = drop_invalid_rows(df, required_columns, spec["table"], file_path)

    pk_columns = spec.get("pk_columns", [])
    if pk_columns:
        before_count = len(df)
        df = df.drop_duplicates(subset=pk_columns, keep="first")
        dropped_duplicates = before_count - len(df)
        if dropped_duplicates:
            logger.warning(
                f"⚠️ {spec['table']}: PK 중복 행 {dropped_duplicates}건을 제외합니다. "
                f"(source={file_path}, columns={pk_columns})"
            )

    if df.empty:
        logger.warning(f"⚠️ {spec['table']}: 유효한 행이 없어 적재를 건너뜁니다. (source={file_path})")
        return None

    return df[spec["target_columns"]]


def copy_to_temp_table(cursor, table_name, df):
    """
    DataFrame을 바로 대상 테이블에 넣지 않고 임시 테이블로 먼저 COPY한다.

    이유:
    - 부모 테이블은 OVERRIDING SYSTEM VALUE 삽입이 필요함
    - 매핑 테이블은 임시 테이블에서 부모 FK 존재 여부를 JOIN으로 검증해야 함
    """
    temp_table = f"temp_{table_name}"
    cursor.execute(f"DROP TABLE IF EXISTS {temp_table};")
    cursor.execute(f"CREATE TEMP TABLE {temp_table} AS SELECT * FROM {table_name} LIMIT 0;")

    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False, header=False, na_rep="")
    csv_buffer.seek(0)

    columns = list(df.columns)
    col_str = ", ".join(columns)
    cursor.copy_expert(f"COPY {temp_table} ({col_str}) FROM STDIN WITH CSV NULL ''", csv_buffer)
    return temp_table, columns


def load_parent_table(cursor, spec, file_path):
    """부모 테이블 적재: temp COPY -> 실제 테이블로 원본 id 유지 INSERT"""
    df = prepare_parent_dataframe(spec, file_path)
    if df is None:
        return

    table_name = spec["table"]
    temp_table, columns = copy_to_temp_table(cursor, table_name, df)
    insert_columns = sql.SQL(", ").join(map(sql.Identifier, columns))
    cursor.execute(
        sql.SQL(
            """
            INSERT INTO {target} ({cols})
            OVERRIDING SYSTEM VALUE
            SELECT {cols} FROM {temp};
            """
        ).format(
            target=sql.Identifier(table_name),
            cols=insert_columns,
            temp=sql.Identifier(temp_table),
        )
    )
    cursor.execute(f"DROP TABLE {temp_table};")
    logger.info(f"✔️ {table_name} 테이블 적재 성공")


def load_mapping_table(cursor, spec, file_path):
    """
    매핑 테이블 적재: temp COPY -> 부모 테이블과 JOIN 후 실제 테이블 INSERT

    직접 COPY하지 않는 이유:
    - CSV 내부 중복 제거 후에도 부모 테이블에 없는 FK가 있을 수 있음
    - JOIN으로 유효한 FK만 남겨 FK violation 없이 적재 가능
    """
    df = prepare_mapping_dataframe(spec, file_path)
    if df is None:
        return

    table_name = spec["table"]
    temp_table, columns = copy_to_temp_table(cursor, table_name, df)

    select_columns = sql.SQL(", ").join(
        sql.SQL("src.{col}").format(col=sql.Identifier(col)) for col in columns
    )
    from_clause = sql.SQL("{temp} src").format(temp=sql.Identifier(temp_table))

    for idx, ref in enumerate(spec.get("fk_refs", []), start=1):
        alias = sql.Identifier(f"ref_{idx}")
        from_clause += sql.SQL(" JOIN {ref_table} {alias} ON src.{src_col} = {alias}.{ref_col}").format(
            ref_table=sql.Identifier(ref["ref_table"]),
            alias=alias,
            src_col=sql.Identifier(ref["column"]),
            ref_col=sql.Identifier(ref["ref_column"]),
        )

    valid_rows_query = sql.SQL(
        """
        SELECT {select_cols}
        FROM {from_clause}
        """
    ).format(
        select_cols=select_columns,
        from_clause=from_clause,
    )

    insert_query = sql.SQL(
        """
        INSERT INTO {target} ({cols})
        {valid_rows_query};
        """
    ).format(
        target=sql.Identifier(table_name),
        cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
        valid_rows_query=valid_rows_query,
    )

    if spec.get("fk_refs"):
        cursor.execute(sql.SQL("SELECT COUNT(*) FROM {temp};").format(temp=sql.Identifier(temp_table)))
        before_count = cursor.fetchone()[0]
        count_query = sql.SQL("SELECT COUNT(*) FROM ({query}) filtered;").format(query=valid_rows_query)
        cursor.execute(count_query)
        after_count = cursor.fetchone()[0]
        dropped_fk = before_count - after_count
        if dropped_fk:
            logger.warning(
                f"⚠️ {table_name}: 부모 테이블에 없는 FK 행 {dropped_fk}건을 제외합니다. "
                f"(source={file_path})"
            )

    cursor.execute(insert_query)
    cursor.execute(f"DROP TABLE {temp_table};")
    logger.info(f"✔️ {table_name} 테이블 적재 성공")


def reset_sequences(cursor):
    """원본 id를 수동 주입했으므로 sequence를 현재 max(id)까지 다시 맞춘다."""
    for table_name in ["genres", "keywords", "otts", "people", "movies"]:
        cursor.execute(
            sql.SQL(
                """
                SELECT setval(
                    pg_get_serial_sequence(%s, 'id'),
                    COALESCE((SELECT MAX(id) FROM {table}), 1),
                    true
                );
                """
            ).format(table=sql.Identifier(table_name)),
            (table_name,),
        )


def truncate_seed_tables(cursor):
    """초기 적재 모드이므로 대상 테이블을 전부 비우고 ID sequence도 초기화한다."""
    table_list = sql.SQL(", ").join(map(sql.Identifier, ALL_TABLES_IN_LOAD_ORDER))
    cursor.execute(sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE;").format(table_list))


def run_seeder():
    """
    전체 초기 적재 진입점.

    순서:
    1. DB 연결 대기
    2. 대상 테이블 전체 초기화
    3. 부모 테이블 적재
    4. sequence 복구
    5. 매핑 테이블 적재
    6. 전체 commit
    """
    max_retries = 15
    for i in range(max_retries):
        try:
            conn = psycopg2.connect(**DB_CONFIG)
            logger.info("✅ 데이터베이스 연결 성공!")
            break
        except psycopg2.OperationalError:
            logger.warning(f"⏳ 데이터베이스 부팅 대기 중... ({i + 1}/{max_retries})")
            time.sleep(2)
    else:
        raise Exception("❌ 데이터베이스 연결 시간 초과!")

    conn.autocommit = False
    cursor = conn.cursor()

    try:
        cursor.execute("SET synchronous_commit TO OFF;")
        logger.info("🚀 데이터 동기화 파이프라인 시작 (초기 전체 적재 모드)")

        truncate_seed_tables(cursor)

        for spec in PARENT_TABLE_SPECS:
            latest_file = find_latest_file(spec["pattern"])
            if not latest_file:
                logger.warning(f"⚠️ 매칭되는 파일이 없습니다 (스킵): {spec['pattern']}")
                continue
            logger.info(f"📄 처리 중: {latest_file} -> {spec['table']}")
            load_parent_table(cursor, spec, latest_file)

        reset_sequences(cursor)

        for spec in MAPPING_TABLE_SPECS:
            latest_file = find_latest_file(spec["pattern"])
            if not latest_file:
                logger.warning(f"⚠️ 매칭되는 파일이 없습니다 (스킵): {spec['pattern']}")
                continue
            logger.info(f"📄 처리 중: {latest_file} -> {spec['table']}")
            load_mapping_table(cursor, spec, latest_file)

        conn.commit()
        logger.info("🎉 모든 데이터 동기화 파이프라인이 성공적으로 종료되었습니다.")

    except Exception as e:
        conn.rollback()
        logger.error(f"💥 동기화 중 오류가 발생하여 전체 롤백 처리되었습니다: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    run_seeder()