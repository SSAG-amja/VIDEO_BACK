from fastapi import APIRouter
from app.api.v1.endpoints import auth, movie_load, explore, user, pinned, passed, watched, interactions, playlist_items, playlists

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(user.router, prefix="/user", tags=["user"])
api_router.include_router(movie_load.router, prefix="/movie_load", tags=["movie"])
api_router.include_router(explore.router, prefix="/explore", tags=["Explore"])
api_router.include_router(pinned.router, prefix="/pinned", tags=["Pinned"])
api_router.include_router(passed.router, prefix="/passed", tags=["Passed"])
api_router.include_router(watched.router, prefix="/watched", tags=["Watched"])
api_router.include_router(interactions.router, prefix="/interactions", tags=["Interactions"])
api_router.include_router(playlists.router, prefix="/playlist", tags=["Playlist"])
api_router.include_router(playlist_items.router, prefix="/playlist", tags=["PlaylistItem"])
