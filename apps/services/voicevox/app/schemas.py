from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class AudioQueryRequest(BaseModel):
    text: str
    speaker: int


class SynthesizeRequest(BaseModel):
    text: str
    speaker: int
    speed_scale: Optional[float] = Field(default=None, ge=0.5, le=2.0)
    pitch_scale: Optional[float] = Field(default=None, ge=-0.15, le=0.15)
    intonation_scale: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    volume_scale: Optional[float] = Field(default=None, ge=0.0, le=2.0)
