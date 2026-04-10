import os
import ast
import logging
import pandas as pd
from datetime import datetime
from sqlalchemy.orm import Session
from app.models import Movie, Genre, Actor, Keyword
# OTT 관련 모델(Ott, MovieOtt) 임포트 제거됨

logger = logging.getLogger("uvicorn")


# 260410 김광원 
# 해당 클래스에서 초기 데이터 설정 일단 보류
# class MovieDataSeeder:
#     """초기 영화 메타데이터(영화, 장르, 배우, 키워드)를 DB에 적재하는 파이프라인 클래스"""
    
#     GENRE_NAMES = [
#         'Action', 'Adventure', 'Animation', 'Comedy', 'Crime', 'Documentary', 
#         'Drama', 'Family', 'Fantasy', 'History', 'Horror', 'Music', 'Mystery', 
#         'Romance', 'Science Fiction', 'TV Movie', 'Thriller', 'War', 'Western'
#     ]

#     def __init__(self, db: Session, csv_path: str):
#         self.db = db
#         self.csv_path = csv_path
#         # OTT 캐시 제거, 순수 영화 메타데이터만 캐싱
#         self.cache = {
#             'genres': {}, 'actors': {}, 'keywords': {}
#         }

#     def execute(self):
#         """데이터 적재 파이프라인을 실행하는 단일 진입점"""
#         if not os.path.exists(self.csv_path):
#             logger.error(f"❌ CSV 파일을 찾을 수 없습니다: {self.csv_path}")
#             return

#         logger.info("🚀 영화 메타데이터 적재 파이프라인 시작...")
        
#         try:
#             self._load_initial_caches()
#             self._seed_base_data()
#             self._seed_movies_from_csv()
#             self.db.commit()
#             logger.info("✅ 영화 기본 데이터(장르, 배우, 키워드 포함) 적재 완료!")
            
#         except Exception as e:
#             self.db.rollback()
#             logger.error(f"❌ 데이터베이스 저장 중 오류 발생: {e}")

#     # ==========================================
#     # Private Methods (내부 로직)
#     # ==========================================
    
#     def _load_initial_caches(self):
#         """DB에 이미 있는 데이터를 캐싱하여 중복 조회를 방지합니다."""
#         self.cache['genres'] = {g.name: g for g in self.db.query(Genre).all()}
#         self.cache['actors'] = {a.name: a for a in self.db.query(Actor).all()}
#         self.cache['keywords'] = {k.name: k for k in self.db.query(Keyword).all()}

#     def _seed_base_data(self):
#         """고정 데이터(장르)를 세팅합니다."""
#         for name in self.GENRE_NAMES:
#             if name not in self.cache['genres']:
#                 genre = Genre(name=name)
#                 self.db.add(genre)
#                 self.cache['genres'][name] = genre
                
#         self.db.flush() # ID 발급을 위해 1차 저장

#     def _seed_movies_from_csv(self):
#         """CSV 파일을 읽어 영화 데이터를 적재합니다."""
#         df = pd.read_csv(self.csv_path)
        
#         for _, row in df.iterrows():
#             tmdb_id = self._clean_val(row['id'])
            
#             # 이미 존재하는 영화는 패스
#             if self.db.query(Movie).filter(Movie.tmdb_id == tmdb_id).first():
#                 continue

#             movie = self._create_movie_entity(row, tmdb_id)
#             self._attach_relations(movie, row)
#             self.db.add(movie)

#     def _create_movie_entity(self, row, tmdb_id) -> Movie:
#         """단일 영화 객체를 조립합니다."""
#         return Movie(
#             tmdb_id=tmdb_id,
#             imdb_id=self._clean_val(row['imdb_id']),
#             title=self._clean_val(row['title']),
#             original_title=self._clean_val(row['original_title']),
#             original_language=self._clean_val(row['original_language']),
#             overview=self._clean_val(row['overview']),
#             director=self._clean_val(row['director']),
#             popularity=self._clean_val(row['popularity']),
#             vote_average=self._clean_val(row['vote_average']),
#             vote_count=self._clean_val(row['vote_count']),
#             release_date=self._parse_date(row['release_date']),
#             runtime=self._clean_val(row['runtime']),
#             budget=self._clean_val(row['budget']),
#             revenue=self._clean_val(row['revenue']),
#             adult=bool(row['adult']) if not pd.isna(row['adult']) else False,
#             status=self._clean_val(row['status']),
#             poster_path=self._clean_val(row['poster_path']),
#             backdrop_path=self._clean_val(row['backdrop_path'])
#         )

#     def _attach_relations(self, movie: Movie, row):
#         """영화와 연관된 장르, 배우, 키워드 관계를 맺어줍니다. (OTT 제외)"""
#         # 1. 장르
#         for g_name in self._parse_list(row['genres']):
#             if g_name in self.cache['genres']:
#                 movie.genres.append(self.cache['genres'][g_name])

#         # 2. 배우 (Get or Create)
#         for a_name in self._parse_list(row['actor']):
#             if a_name not in self.cache['actors']:
#                 actor = Actor(name=a_name)
#                 self.db.add(actor)
#                 self.cache['actors'][a_name] = actor
#             movie.actors.append(self.cache['actors'][a_name])

#         # 3. 키워드 (Get or Create)
#         for k_name in self._parse_list(row['keywords']):
#             if k_name not in self.cache['keywords']:
#                 keyword = Keyword(name=k_name)
#                 self.db.add(keyword)
#                 self.cache['keywords'][k_name] = keyword
#             movie.keywords.append(self.cache['keywords'][k_name])

#     # ==========================================
#     # Static Helpers (유틸리티)
#     # ==========================================
    
#     @staticmethod
#     def _clean_val(val):
#         return None if pd.isna(val) else val

#     @staticmethod
#     def _parse_date(val):
#         if pd.isna(val): return None
#         try:
#             return datetime.strptime(str(val).strip(), '%Y-%m-%d').date()
#         except ValueError:
#             return None

#     @staticmethod
#     def _parse_list(val) -> list:
#         if pd.isna(val): return []
#         if isinstance(val, str):
#             try:
#                 parsed = ast.literal_eval(val)
#                 if isinstance(parsed, list): return [str(i).strip() for i in parsed]
#             except (ValueError, SyntaxError):
#                 return [i.strip() for i in val.split(',')]
#         return val if isinstance(val, list) else []