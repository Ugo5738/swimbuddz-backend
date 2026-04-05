"""
Safe text-only update for an existing program from seed JSON.
Updates ONLY: lesson titles, lesson descriptions, skill descriptions/categories,
and the curriculum_json blob. Does NOT touch IDs, relationships, or milestones.

Usage:
  python scripts/update_program_text.py --file scripts/seed-data/freestyle_beginner.json --dry-run
  python scripts/update_program_text.py --file scripts/seed-data/freestyle_beginner.json
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from libs.db.config import AsyncSessionLocal
from services.academy_service.models import (
    CurriculumLesson,
    CurriculumWeek,
    LessonSkill,
    Program,
    ProgramCurriculum,
    Skill,
)


async def update_program_text(json_file_path, dry_run=True):
    print(
        f"{'🔍 DRY RUN' if dry_run else '🚀 LIVE UPDATE'} — Reading {json_file_path}..."
    )

    with open(json_file_path, "r") as f:
        data = json.load(f)

    program_data = data["program"]
    curriculum_data = data.get("curriculum", [])
    skills_library = data.get("skills_library", [])
    slug = program_data["slug"]

    changes = []

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # 1. Find existing program
            stmt = select(Program).where(Program.slug == slug)
            result = await session.execute(stmt)
            program = result.scalar_one_or_none()

            if not program:
                print(f"❌ No program found with slug '{slug}'")
                return

            print(f"Found program: {program.name} (ID: {program.id})")

            # 2. Update curriculum_json blob on Program
            new_curriculum_json = {
                "weeks": [{**w, "week": w["week_number"]} for w in curriculum_data]
            }
            if program.curriculum_json != new_curriculum_json:
                changes.append("Program.curriculum_json — updated (full blob)")
                if not dry_run:
                    program.curriculum_json = new_curriculum_json

            # 3. Load normalized curriculum with weeks → lessons → skills
            stmt = (
                select(ProgramCurriculum)
                .where(ProgramCurriculum.program_id == program.id)
                .where(ProgramCurriculum.is_active.is_(True))
                .options(
                    selectinload(ProgramCurriculum.weeks)
                    .selectinload(CurriculumWeek.lessons)
                    .selectinload(CurriculumLesson.skills)
                    .selectinload(LessonSkill.skill)
                )
            )
            result = await session.execute(stmt)
            curriculum = result.scalar_one_or_none()

            if not curriculum:
                print("❌ No active curriculum found")
                return

            print(f"Active curriculum v{curriculum.version}")

            # 4. Update weeks & lessons
            db_weeks = {w.week_number: w for w in curriculum.weeks}

            for seed_week in curriculum_data:
                wn = seed_week["week_number"]
                db_week = db_weeks.get(wn)
                if not db_week:
                    print(f"  ⚠️  Week {wn} not found in DB — skipping")
                    continue

                # Check theme & objectives (should be same but let's be thorough)
                if db_week.theme != seed_week["theme"]:
                    changes.append(
                        f"  Week {wn} theme: '{db_week.theme}' → '{seed_week['theme']}'"
                    )
                    if not dry_run:
                        db_week.theme = seed_week["theme"]

                if db_week.objectives != seed_week.get("objectives"):
                    changes.append(f"  Week {wn} objectives: changed")
                    if not dry_run:
                        db_week.objectives = seed_week.get("objectives")

                # Match lessons by position (each week has 1 lesson)
                seed_lessons = seed_week.get("lessons", [])
                db_lessons = sorted(
                    db_week.lessons, key=lambda lesson: lesson.order_index
                )

                for i, seed_lesson in enumerate(seed_lessons):
                    if i >= len(db_lessons):
                        print(
                            f"  ⚠️  Week {wn} has more seed lessons than DB — skipping extra"
                        )
                        break

                    db_lesson = db_lessons[i]

                    if db_lesson.title != seed_lesson["title"]:
                        changes.append(
                            f"  Week {wn} lesson title: '{db_lesson.title}' → '{seed_lesson['title']}'"
                        )
                        if not dry_run:
                            db_lesson.title = seed_lesson["title"]

                    if db_lesson.description != seed_lesson.get("description"):
                        # Truncate for display
                        old_preview = (db_lesson.description or "")[:60]
                        new_preview = (seed_lesson.get("description") or "")[:60]
                        changes.append(
                            f"  Week {wn} lesson description: '{old_preview}...' → '{new_preview}...'"
                        )
                        if not dry_run:
                            db_lesson.description = seed_lesson.get("description")

            # 5. Update skill descriptions and categories
            print("\nChecking skills...")
            skills_by_name = {s["name"]: s for s in skills_library}

            stmt = select(Skill)
            result = await session.execute(stmt)
            db_skills = result.scalars().all()

            for db_skill in db_skills:
                seed_skill = skills_by_name.get(db_skill.name)
                if not seed_skill:
                    continue

                if db_skill.description != seed_skill.get("description"):
                    old_preview = (db_skill.description or "")[:50]
                    new_preview = (seed_skill.get("description") or "")[:50]
                    changes.append(
                        f"  Skill '{db_skill.name}' description: '{old_preview}...' → '{new_preview}...'"
                    )
                    if not dry_run:
                        db_skill.description = seed_skill.get("description")

                if db_skill.category != seed_skill.get("category"):
                    changes.append(
                        f"  Skill '{db_skill.name}' category: '{db_skill.category}' → '{seed_skill['category']}'"
                    )
                    if not dry_run:
                        db_skill.category = seed_skill["category"]

            # Summary
            print(f"\n{'=' * 60}")
            if changes:
                print(f"📝 {len(changes)} change(s) found:")
                for c in changes:
                    print(f"  {c}")
            else:
                print("✅ No differences found — DB matches seed data.")

            if dry_run:
                print(
                    "\n🔍 DRY RUN — no changes written. Run without --dry-run to apply."
                )
                # Rollback by not committing (session.begin() will auto-rollback)
                await session.rollback()
            else:
                print(f"\n✅ {len(changes)} change(s) committed to database.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="Path to JSON data file")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview changes without writing"
    )
    args = parser.parse_args()

    asyncio.run(update_program_text(args.file, dry_run=args.dry_run))
