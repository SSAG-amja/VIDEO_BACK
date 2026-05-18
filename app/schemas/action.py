from typing import Literal

from pydantic import BaseModel, model_validator


class InteractionUpdateRequest(BaseModel):
    action_type: Literal["pin", "passed", "watched", "saved"]
    playlist_id: int | None = None

    # 2026.05.13 박현식
    # saved 액션에는 저장 대상 playlist_id가 반드시 포함되도록 검증한다.
    @model_validator(mode="after")
    def validate_saved_playlist(self):
        if self.action_type == "saved" and self.playlist_id is None:
            raise ValueError("saved action requires playlist_id.")
        return self


class InteractionState(BaseModel):
    movie_id: int | None
    is_pinned: bool
    is_passed: bool
    is_watched: bool
    is_saved: bool


class InteractionUpdateResponse(BaseModel):
    data: InteractionState
