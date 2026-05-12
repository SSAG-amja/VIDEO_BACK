import asyncio

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import settings
from app.models import mapping as mapping_model
from app.models import movie as movie_model
from app.models import people as people_model

router = APIRouter()

BASE_URL = "https://api.themoviedb.org/3"


def _ott_type(movie_ott: mapping_model.MovieOtt) -> str:
    if movie_ott.is_streaming:
        return "streaming"
    if movie_ott.is_rent:
        return "rent"
    if movie_ott.is_buy:
        return "buy"
    return "unknown"


def _has_korean(text: str | None) -> bool:
    return bool(text and any("\uac00" <= char <= "\ud7a3" for char in text))


def _korean_alias(person_payload: dict) -> str | None:
    known_names = [person_payload.get("name"), *person_payload.get("also_known_as", [])]
    return next((name for name in known_names if _has_korean(name)), None)


def _tmdb_ott_type(provider_group: str) -> str:
    if provider_group == "flatrate":
        return "streaming"
    return provider_group


def _merge_ott_type(current_type: str | None, new_type: str) -> str:
    priority = {"streaming": 3, "rent": 2, "buy": 1, "unknown": 0, None: 0}
    if priority.get(new_type, 0) > priority.get(current_type, 0):
        return new_type
    return current_type or new_type


def _extract_tmdb_provider_otts(providers_payload: dict) -> dict[int, dict]:
    provider_otts = {}
    kr_providers = providers_payload.get("results", {}).get("KR", {})
    for provider_group in ("flatrate", "rent", "buy"):
        provider_type = _tmdb_ott_type(provider_group)
        for provider in kr_providers.get(provider_group, []):
            provider_id = provider.get("provider_id")
            if not provider_id:
                continue

            existing = provider_otts.get(provider_id, {})
            provider_otts[provider_id] = {
                "ott_id": provider_id,
                "provider_id": provider_id,
                "ott_name": existing.get("ott_name") or provider.get("provider_name"),
                "provider_name": existing.get("provider_name") or provider.get("provider_name"),
                "type": _merge_ott_type(existing.get("type"), provider_type),
                "logo_path": existing.get("logo_path") or provider.get("logo_path"),
            }
    return provider_otts


async def _fetch_tmdb_detail_fallback(movie_id: int) -> dict:
    async with httpx.AsyncClient() as client:
        detail_url = f"{BASE_URL}/movie/{movie_id}?api_key={settings.TMDB_API_KEY}&language=ko-KR&append_to_response=videos,credits"
        providers_url = f"{BASE_URL}/movie/{movie_id}/watch/providers?api_key={settings.TMDB_API_KEY}"
        res, providers_res = await asyncio.gather(
            client.get(detail_url),
            client.get(providers_url),
        )

        tmdb_providers = {}
        if providers_res.status_code == 200:
            tmdb_providers = _extract_tmdb_provider_otts(providers_res.json())

        if res.status_code != 200:
            return {"tmdb_providers": tmdb_providers}

        data = res.json()
        videos = data.get("videos", {}).get("results", [])
        youtube_id = next((v["key"] for v in videos if v.get("site") == "YouTube" and v.get("type") == "Trailer"), None)

        if not youtube_id:
            en_url = f"{BASE_URL}/movie/{movie_id}/videos?api_key={settings.TMDB_API_KEY}"
            en_res = await client.get(en_url)
            en_videos = en_res.json().get("results", []) if en_res.status_code == 200 else []
            youtube_id = next((v["key"] for v in en_videos if v.get("site") == "YouTube" and v.get("type") == "Trailer"), None)

        cast_data = data.get("credits", {}).get("cast", [])[:10]
        cast = [
            {
                "id": person["id"],
                "name": person.get("name", ""),
                "role": person.get("character", ""),
                "image": f"https://image.tmdb.org/t/p/w200{person['profile_path']}" if person.get("profile_path") else "https://via.placeholder.com/200x300?text=No+Image",
            }
            for person in cast_data
        ]

        return {
            "overview": data.get("overview"),
            "runtime": data.get("runtime"),
            "youtubeId": youtube_id,
            "trailer_url": f"https://www.youtube.com/embed/{youtube_id}" if youtube_id else None,
            "cast": cast,
            "tmdb_providers": tmdb_providers,
        }


async def _fetch_tmdb_korean_person_names(person_ids: list[int]) -> dict[int, str]:
    if not person_ids:
        return {}

    async with httpx.AsyncClient() as client:
        tasks = [
            client.get(f"{BASE_URL}/person/{person_id}?api_key={settings.TMDB_API_KEY}&language=ko-KR")
            for person_id in person_ids
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

    names = {}
    for person_id, response in zip(person_ids, responses):
        if isinstance(response, Exception) or response.status_code != 200:
            continue
        alias = _korean_alias(response.json())
        if alias:
            names[person_id] = alias
    return names


async def _build_movie_detail(db: Session, movie_id: int) -> dict:
    movie = db.scalar(select(movie_model.Movie).where(movie_model.Movie.tmdb_id == movie_id))
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    fallback = await _fetch_tmdb_detail_fallback(movie_id)

    actor_rows = db.execute(
        select(people_model.People, mapping_model.MovieActor.cast_name)
        .join(mapping_model.MovieActor, mapping_model.MovieActor.actor_id == people_model.People.id)
        .where(mapping_model.MovieActor.movie_id == movie.id)
        .limit(10)
    ).all()
    actors = [
        {
            "actor_name": person.name_ko or person.name,
            "actor_profile": None,
            "id": person.tmdb_id,
            "name": person.name_ko or person.name,
            "role": cast_name or "",
            "image": "https://via.placeholder.com/200x300?text=No+Image",
        }
        for person, cast_name in actor_rows
    ]

    if fallback.get("cast"):
        cast_by_id = {person.get("id"): person for person in fallback["cast"]}
        actors = [
            {
                **actor,
                "image": cast_by_id.get(actor["id"], {}).get("image", actor["image"]),
            }
            for actor in actors
        ] or fallback["cast"]

    missing_korean_name_ids = [
        actor["id"]
        for actor in actors
        if actor.get("id") and not _has_korean(actor.get("name"))
    ]
    korean_person_names = await _fetch_tmdb_korean_person_names(missing_korean_name_ids)
    if korean_person_names:
        actors = [
            {
                **actor,
                "actor_name": korean_person_names.get(actor.get("id"), actor.get("actor_name")),
                "name": korean_person_names.get(actor.get("id"), actor.get("name")),
            }
            for actor in actors
        ]

    ott_rows = db.execute(
        select(mapping_model.MovieOtt)
        .where(mapping_model.MovieOtt.movie_id == movie.id)
    ).scalars().all()
    otts_by_provider_id = {}
    tmdb_providers = fallback.get("tmdb_providers", {})
    for movie_ott in ott_rows:
        provider_id = movie_ott.ott.tmdb_id
        tmdb_provider = tmdb_providers.get(provider_id, {})
        otts_by_provider_id[provider_id] = {
            "ott_id": movie_ott.ott.tmdb_id,
            "provider_id": movie_ott.ott.tmdb_id,
            "ott_name": movie_ott.ott.name_ko or movie_ott.ott.name,
            "provider_name": movie_ott.ott.name_ko or movie_ott.ott.name,
            "type": _merge_ott_type(_ott_type(movie_ott), tmdb_provider.get("type")),
            "logo_path": tmdb_provider.get("logo_path"),
        }

    for provider_id, tmdb_provider in tmdb_providers.items():
        if provider_id in otts_by_provider_id:
            continue
        otts_by_provider_id[provider_id] = tmdb_provider

    otts = list(otts_by_provider_id.values())

    release_year = movie.release_date.year if movie.release_date else "미상"
    genres = [{"genre_id": genre.tmdb_id, "name": genre.name_ko or genre.name} for genre in movie.genres]
    genre_names = ", ".join([genre["name"] for genre in genres])
    director = ", ".join([person.name_ko or person.name for person in movie.directors])
    runtime = movie.runtime or fallback.get("runtime") or 0
    title = movie.title_ko or movie.title or movie.original_title

    overview = movie.overview
    if not _has_korean(overview) and fallback.get("overview"):
        overview = fallback.get("overview")

    return {
        "movie_id": movie.tmdb_id,
        "movie_title": title,
        "id": movie.tmdb_id,
        "title": title,
        "poster_path": movie.poster_path,
        "posterPath": movie.poster_path or "",
        "backdrop_path": movie.backdrop_path,
        "overview": overview or "시놉시스 정보가 없습니다.",
        "runtime": runtime,
        "vote_average": movie.vote_average or 0,
        "rating": round(float(movie.vote_average or 0), 1),
        "director": director,
        "genres": genres,
        "actors": actors,
        "cast": actors,
        "otts": otts,
        "providers": otts,
        "youtubeId": fallback.get("youtubeId"),
        "trailer_url": fallback.get("trailer_url"),
        "info": f"{release_year} | {genre_names} | {runtime}분",
        "tags": [f"#{genre['name']}" for genre in genres[:3]] or ["#추천영화"],
        "user_interaction": {
            "is_pinned": False,
            "is_watched": False,
            "is_passed": False,
            "is_saved": False,
        },
    }


@router.get("/shorts")
async def get_shorts_movies(page: int = Query(1, description="Page number")):
    async with httpx.AsyncClient() as client:
        movies_url = f"{BASE_URL}/movie/popular?api_key={settings.TMDB_API_KEY}&language=ko-KR&page={page}"
        movies_res = await client.get(movies_url)

        if movies_res.status_code != 200:
            raise HTTPException(status_code=500, detail="TMDB API 호출 실패")

        movies = movies_res.json().get("results", [])

        async def fetch_movie_full_data(movie_id: int):
            detail_url = f"{BASE_URL}/movie/{movie_id}?api_key={settings.TMDB_API_KEY}&language=ko-KR&append_to_response=videos,credits"
            res = await client.get(detail_url)

            if res.status_code != 200:
                return None

            data = res.json()
            videos = data.get("videos", {}).get("results", [])
            youtube_id = next((v["key"] for v in videos if v.get("site") == "YouTube" and v.get("type") == "Trailer"), None)

            if not youtube_id:
                en_url = f"{BASE_URL}/movie/{movie_id}/videos?api_key={settings.TMDB_API_KEY}"
                en_res = await client.get(en_url)
                en_videos = en_res.json().get("results", []) if en_res.status_code == 200 else []
                youtube_id = next((v["key"] for v in en_videos if v.get("site") == "YouTube" and v.get("type") == "Trailer"), None)

            cast_data = data.get("credits", {}).get("cast", [])[:10]
            formatted_cast = [
                {
                    "id": person["id"],
                    "name": person.get("name", ""),
                    "role": person.get("character", ""),
                    "image": f"https://image.tmdb.org/t/p/w200{person['profile_path']}" if person.get("profile_path") else "https://via.placeholder.com/200x300?text=No+Image",
                }
                for person in cast_data
            ]

            release_date = data.get("release_date", "")
            release_year = release_date[:4] if release_date else "미상"
            genres = ", ".join([g["name"] for g in data.get("genres", [])])
            runtime = data.get("runtime", 0)
            rating = round(data.get("vote_average", 0), 1)
            real_tags = [f"#{g['name']}" for g in data.get("genres", [])[:3]]

            return {
                "id": data["id"],
                "title": data["title"],
                "overview": data.get("overview", "시놉시스 정보가 없습니다."),
                "posterPath": data.get("poster_path", ""),
                "youtubeId": youtube_id,
                "info": f"{release_year} | {genres} | {runtime}분",
                "rating": rating,
                "runtime": runtime,
                "tags": real_tags if real_tags else ["#추천영화"],
                "cast": formatted_cast,
            }

        tasks = [fetch_movie_full_data(movie["id"]) for movie in movies]
        full_movies_data = await asyncio.gather(*tasks)
        final_movies = [m for m in full_movies_data if m is not None and m.get("youtubeId")]

        return {"movies": final_movies}


@router.get("/{movie_id}")
async def get_movie_detail(movie_id: int, db: Session = Depends(deps.get_db)):
    return await _build_movie_detail(db, movie_id)
