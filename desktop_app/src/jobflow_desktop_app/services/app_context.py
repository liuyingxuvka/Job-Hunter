from __future__ import annotations

from dataclasses import dataclass

from ..db.connection import Database
from ..db.repositories.candidates import CandidateRepository
from ..db.repositories.overview import OverviewRepository
from ..db.repositories.profiles import SearchProfileRepository
from ..db.repositories.settings import AppSettingsRepository
from ..paths import AppPaths


@dataclass(frozen=True)
class AppContext:
    paths: AppPaths
    database: Database
    candidates: CandidateRepository
    profiles: SearchProfileRepository
    settings: AppSettingsRepository
    overview: OverviewRepository
