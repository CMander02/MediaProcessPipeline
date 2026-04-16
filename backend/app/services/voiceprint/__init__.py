"""Voiceprint (speaker embedding) service.

Independent SQLite-vec backed library for long-lived speaker identity,
decoupled from task lifecycle.
"""
from app.services.voiceprint.store import (
    VoiceprintStore,
    get_voiceprint_store,
    Person,
    MatchResult,
)

__all__ = ["VoiceprintStore", "get_voiceprint_store", "Person", "MatchResult"]
