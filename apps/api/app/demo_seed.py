from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.models import Entry, EntryTag, Tag, Transcript, User
from app.settings import Settings


@dataclass(frozen=True)
class DemoDayTemplate:
    title: str
    entry_type: str
    transcript_text: str
    tags: tuple[str, ...]


DEMO_DAY_TEMPLATES: tuple[DemoDayTemplate, ...] = (
    DemoDayTemplate(
        title="Shipped onboarding flow improvements",
        entry_type="win",
        transcript_text=(
            "Today I shipped the onboarding flow update, reduced friction on signup, and documented the rollout steps. "
            "I also paired with design to improve the first-run CTA and confirmed the deployment stayed stable."
        ),
        tags=("shipping", "onboarding", "teamwork"),
    ),
    DemoDayTemplate(
        title="Resolved production incident and follow-up",
        entry_type="blocker",
        transcript_text=(
            "I handled a production incident during the morning deploy, traced the root cause to a stale cache path, "
            "and shipped a patch with monitoring alerts. The challenge was coordinating rollback timing across services."
        ),
        tags=("incident", "debugging", "operations"),
    ),
    DemoDayTemplate(
        title="Improved search relevance with transcript ranking",
        entry_type="learning",
        transcript_text=(
            "I iterated on transcript search relevance, compared ranking heuristics, and validated better result ordering "
            "for recurring work questions. I learned where snippets were too broad and tightened offset selection."
        ),
        tags=("search", "learning", "quality"),
    ),
    DemoDayTemplate(
        title="Customer feedback synthesis and planning",
        entry_type="task",
        transcript_text=(
            "I reviewed customer feedback themes, summarized repeated challenges around response time, and prepared a "
            "next-sprint plan. Success came from prioritizing fixes with clear business impact."
        ),
        tags=("customer", "planning", "prioritization"),
    ),
    DemoDayTemplate(
        title="Automation and developer experience cleanup",
        entry_type="idea",
        transcript_text=(
            "I proposed automation for local development setup, removed manual startup steps, and wrote a helper script "
            "to reduce onboarding time for new contributors. The improvement should cut setup errors significantly."
        ),
        tags=("automation", "devex", "onboarding"),
    ),
)


def _normalize_tag_name(value: str) -> str:
    return value.strip().lower()


def _ensure_tags(session: Session, user_id: object, names: Iterable[str]) -> dict[str, Tag]:
    normalized_names = [_normalize_tag_name(name) for name in names if name.strip()]
    if not normalized_names:
        return {}

    existing = session.execute(
        select(Tag).where(Tag.user_id == user_id, Tag.normalized_name.in_(normalized_names))
    ).scalars().all()
    by_normalized = {tag.normalized_name: tag for tag in existing}

    for normalized in normalized_names:
        if normalized in by_normalized:
            continue
        created = Tag(user_id=user_id, name=normalized, normalized_name=normalized)
        session.add(created)
        session.flush()
        by_normalized[normalized] = created

    return by_normalized


def _build_demo_title(day: date) -> str:
    return f"Demo Work Log {day.isoformat()}"


def seed_demo_account_data(session: Session, settings: Settings) -> None:
    if not settings.demo_seed_enabled:
        return

    email = settings.demo_seed_email.strip().lower()
    password = settings.demo_seed_password
    today = datetime.utcnow().date()
    days = max(1, settings.demo_seed_days)

    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, password_hash=hash_password(password))
        session.add(user)
        session.flush()
    else:
        # Keep demo credentials deterministic for local demos.
        user.password_hash = hash_password(password)

    existing_entries = session.execute(select(Entry).where(Entry.user_id == user.id)).scalars().all()
    existing_days = {
        (entry.occurred_at.date() if entry.occurred_at is not None else entry.created_at.date())
        for entry in existing_entries
        if entry.title and entry.title.startswith("Demo Work Log ")
    }

    all_tag_names = sorted(
        {
            tag
            for template in DEMO_DAY_TEMPLATES
            for tag in template.tags
        }
    )
    tags_by_name = _ensure_tags(session, user.id, all_tag_names)

    for offset in range(days):
        day = today - timedelta(days=offset)
        if day in existing_days:
            continue

        template = DEMO_DAY_TEMPLATES[offset % len(DEMO_DAY_TEMPLATES)]
        occurred_at = datetime.combine(day, datetime.min.time()) + timedelta(hours=17, minutes=30)
        created_at = occurred_at + timedelta(minutes=5)

        entry = Entry(
            user_id=user.id,
            title=_build_demo_title(day),
            status="ready",
            entry_type=template.entry_type,
            context="work",
            occurred_at=occurred_at,
            created_at=created_at,
            updated_at=created_at,
        )
        session.add(entry)
        session.flush()

        transcript = Transcript(
            entry_id=entry.id,
            version=1,
            is_current=True,
            transcript_text=template.transcript_text,
            language_code="en",
            source="demo_seed",
            created_at=created_at,
        )
        session.add(transcript)

        for tag_name in template.tags:
            normalized = _normalize_tag_name(tag_name)
            tag = tags_by_name.get(normalized)
            if tag is None:
                continue
            session.add(EntryTag(entry_id=entry.id, tag_id=tag.id, created_at=created_at))

    session.commit()
