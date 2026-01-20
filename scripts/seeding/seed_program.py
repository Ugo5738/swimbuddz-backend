import asyncio
import json
import argparse
import sys
import os
from uuid import uuid4

# Add backend root to path so we can import modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from libs.db.config import AsyncSessionLocal
from sqlalchemy.future import select

# Import Models
from services.academy_service.models import (
    Program,
    ProgramCurriculum,
    CurriculumWeek,
    CurriculumLesson,
    Skill,
    LessonSkill,
    ProgramLevel,
    BillingType,
)


async def get_or_create_skill(session, skill_name):
    """Finds a skill by name or creates it if it doesn't exist."""
    stmt = select(Skill).where(Skill.name == skill_name)
    result = await session.execute(stmt)
    skill = result.scalar_one_or_none()

    if not skill:
        print(f"   + Creating new skill: {skill_name}")
        skill = Skill(
            id=uuid4(),
            name=skill_name,
            category="general",  # Default category
            description="Auto-created skill",
        )
        session.add(skill)
        await session.flush()
    return skill


async def seed_program(json_file_path):
    print(f"Reading {json_file_path}...")
    try:
        with open(json_file_path, "r") as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ Failed to read JSON file: {e}")
        return

    program_data = data.get("program")
    curriculum_data = data.get("curriculum", [])

    if not program_data:
        print("❌ Error: JSON must contain 'program' key.")
        return

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # 1. Create Program
            print(f"Creating Program: {program_data['name']}...")
            program = Program(
                id=uuid4(),
                name=program_data["name"],
                description=program_data.get("description"),
                level=ProgramLevel(program_data.get("level", "beginner")),
                duration_weeks=program_data.get("duration_weeks", 12),
                default_capacity=program_data.get("default_capacity", 10),
                currency=program_data.get("currency", "NGN"),
                price_amount=program_data.get("price_amount", 0),
                billing_type=BillingType(program_data.get("billing_type", "one_time")),
                is_published=False,
            )
            session.add(program)
            await session.flush()

            # 2. Create ProgramCurriculum (The container)
            print("Creating Curriculum container...")
            curriculum = ProgramCurriculum(
                id=uuid4(), program_id=program.id, version=1, is_active=True
            )
            session.add(curriculum)
            await session.flush()

            # 3. Create Weeks & Lessons
            for week_data in curriculum_data:
                print(
                    f" - Processing Week {week_data['week_number']}: {week_data['theme']}"
                )
                week = CurriculumWeek(
                    id=uuid4(),
                    curriculum_id=curriculum.id,
                    week_number=week_data["week_number"],
                    theme=week_data["theme"],
                    objectives=week_data.get("objectives"),
                )
                session.add(week)
                await session.flush()

                # Process Lessons
                for lesson_data in week_data.get("lessons", []):
                    lesson = CurriculumLesson(
                        id=uuid4(),
                        week_id=week.id,
                        title=lesson_data["title"],
                        description=lesson_data.get("description"),
                        duration_minutes=lesson_data.get("duration_minutes", 45),
                        video_media_id=None,  # Media linking would require complex lookup, skipping for seed
                    )
                    session.add(lesson)
                    await session.flush()

                    # Link Skills
                    skill_names = lesson_data.get("skills", [])
                    for s_name in skill_names:
                        skill = await get_or_create_skill(session, s_name)
                        link = LessonSkill(
                            id=uuid4(), lesson_id=lesson.id, skill_id=skill.id
                        )
                        session.add(link)

            print(f"✅ Successfully seeded program: {program.name} (ID: {program.id})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="Path to JSON data file")
    args = parser.parse_args()

    asyncio.run(seed_program(args.file))
