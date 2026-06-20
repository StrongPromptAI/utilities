"""Seed the four shows + the Sales Edition EP1-4 episode rows.

INSERT-ONLY: creates any missing show/episode row, never overwrites an existing
one — so it is safe to run on every container boot (the Docker CMD does) without
clobbering admin edits or rotating codes. To re-seed from scratch locally, delete
the DB first.
Run: `PODCAST_DB_PATH=./data/podcast.db uv run python seed.py`
Prints each show's feed URL (with code) at the end so you can subscribe.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone

from db import SessionLocal, init_db
from models import Episode, Podcast
from storage import list_audio

PUBLIC_BASE = os.environ.get("PODCAST_PUBLIC_BASE", "http://localhost:8103")

# slug, folder, title, access
SHOWS = [
    ("sales", "sales", "Healing Journey Podcast — Sales Edition", "private"),
    ("clinical", "clinical", "Healing Journey Podcast — Clinical Edition", "private"),
    ("tech", "tech", "Healing Journey Podcast — Tech Review", "private"),
    ("general", "general", "Healing Journey Podcast", "public"),
]

DESCRIPTIONS = {
    "sales": "Four episodes that frame the DME Layer 1 work for sales — the strategic "
    "why, the living-room what, the monetization how, and the competitive-landscape "
    "question. They build on each other; each stands on its own.",
    "clinical": "For select clinical stakeholders to shape how the Healing Journey and "
    "Eva work clinically.",
    "tech": "Internal spec and doctrine reviews — read aloud for listening on the go.",
    "general": "The Healing Journey podcast.",
}

# filename, order, title, description — Sales Edition, drafted from the marketing page.
SALES_EPISODES = [
    (
        "HealingJourneyPodcast_EP1.mp3", 1,
        "Force Therapeutics vs. the Healing Journey: A Strategic Analysis",
        "Two analysts debate whether the Healing Journey is a real competitive threat to "
        "Force Therapeutics, the 60+ health-system incumbent in post-surgical digital care. "
        "One builds the steel man for Force — enterprise lock-in, EHR integration, clinical "
        "validation, $26M raised; the other counter-positions StrongPrompt as a "
        "Christensen-style disruptor playing a different game for a different customer. The "
        "takeaway: they aren't really competing — they're playing different games that happen "
        "to share a patient. If you're trying to understand why Eva exists at all, start here.",
    ),
    (
        "HealingJourneyPodcast_EP2.mp3", 2,
        "Sara Delivers Nancy's Equipment",
        "A dramatized roleplay: an OrthoXpress DME rep (Sara) wraps forty-five minutes of "
        "equipment training with a total-knee patient (Nancy) a week before surgery — training "
        "Nancy barely retained — then introduces Eva. Nancy signs in and asks her first three "
        "real questions out loud: the cold-therapy unit, the walker, equipment pickup. The "
        "narrator bookends the scene with why the Force Therapeutics and DME-manufacturer worlds "
        "are structurally locked out of building this experience. The ground-level view of what "
        "the strategic advantage looks like when it meets a real patient at her kitchen table.",
    ),
    (
        "HealingJourneyPodcast_EP3.mp3", 3,
        "The Invitation: Why Being Asked Beats Being Found",
        "A tech enthusiast argues a chat service can't work for the TKR demographic — the "
        "average patient is 68 and calls the surgeon's office because that's what she knows. His "
        "co-host disagrees: she isn't making a technology decision, she's deciding whether to "
        "trust Sara. That distinction unlocks the full five-step mechanic — from Sara hitting "
        "record in Nancy's living room to the surgeon paying a monthly subscription — plus the "
        "Peterson sales framework the rep needs to close. Hear how the living-room handoff "
        "becomes a paying surgeon, and why it works best with the patient a skeptic assumes is "
        "hardest to reach.",
    ),
    (
        "HealingJourneyPodcast_EP4.mp3", 4,
        "Front Door, Back Door: Is Ask Hoag the Competition?",
        "Chris and guest Anna unpack Hoag Hospital's “Ask Hoag,” a HIPAA-compliant "
        "generative-AI intake system. It sounds like a competitor — same patient, same AI wave — "
        "but the episode argues it isn't: Ask Hoag is the front door into the institution; the "
        "Healing Journey is the back door that catches the patient in her living room after "
        "discharge. Three structural reasons keep hospital AI out of post-op recovery — "
        "practitioner ownership of the 90-day risk window, care-team fragmentation, and incentives "
        "tuned to institutional metrics over the surgeon's panel. Gives the rep language to "
        "position the Healing Journey as completing, not competing with, the hospital's AI.",
    ),
]


def seed() -> None:
    init_db()
    with SessionLocal() as s:
        # Shows — create if missing; leave existing ones untouched (admin owns them).
        for slug, folder, title, access in SHOWS:
            if s.get(Podcast, slug) is not None:
                continue
            s.add(Podcast(
                slug=slug, folder=folder, title=title, access=access,
                description=DESCRIPTIONS[slug], author="StrongPrompt",
                code=(secrets.token_urlsafe(32) if access == "private" else None),
            ))

        # Sales Edition episodes — create if missing; never overwrite admin edits.
        for filename, order, title, desc in SALES_EPISODES:
            exists = (
                s.query(Episode).filter_by(podcast_slug="sales", filename=filename).first()
            )
            if exists is None:
                s.add(Episode(
                    podcast_slug="sales", filename=filename,
                    title=title, description=desc, sort_order=order,
                ))

        s.commit()

        # Backfill an Episode row for every MP3 on the volume that lacks one, so the
        # admin always mirrors what's actually in each feed (feed = disk ⟕ rows). The
        # migrated episodes arrived without rows — without this they play in the feed
        # but are invisible/undeletable in the admin. Insert-only: never touches an
        # existing row, so it's safe on every boot and a delete stays deleted.
        for show in s.query(Podcast).all():
            have = {
                e.filename
                for e in s.query(Episode).filter_by(podcast_slug=show.slug).all()
            }
            for af in list_audio(show.folder):
                if af.name not in have:
                    s.add(Episode(
                        podcast_slug=show.slug, filename=af.name,
                        published_at=datetime.fromtimestamp(af.mtime, tz=timezone.utc),
                    ))
        s.commit()

        print("\nSeeded shows:")
        for show in s.query(Podcast).order_by(Podcast.slug).all():
            if show.access == "public":
                url = f"{PUBLIC_BASE}/{show.slug}/feed.xml"
            else:
                url = f"{PUBLIC_BASE}/{show.slug}/{show.code}/feed.xml"
            n = s.query(Episode).filter_by(podcast_slug=show.slug).count()
            print(f"  [{show.access:7}] {show.title}")
            print(f"            {n} episode row(s) · {url}")


if __name__ == "__main__":
    seed()
