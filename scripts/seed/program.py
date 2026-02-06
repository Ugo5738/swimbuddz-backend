import argparse
import asyncio
import json
import os
import sys
from uuid import uuid4

# Add backend root to path so we can import modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from libs.db.config import AsyncSessionLocal

# Import Models
from services.academy_service.models import (
    BillingType,
    CurriculumLesson,
    CurriculumWeek,
    LessonSkill,
    Milestone,
    MilestoneType,
    Program,
    ProgramCurriculum,
    ProgramLevel,
    RequiredEvidence,
    Skill,
)
from sqlalchemy.future import select


async def get_or_create_skill(session, skill_name, skills_library=None):
    """Finds a skill by name or creates it if it doesn't exist.

    If skills_library is provided, uses the detailed definition from there.
    Otherwise falls back to default category/description.
    """
    stmt = select(Skill).where(Skill.name == skill_name)
    result = await session.execute(stmt)
    skill = result.scalar_one_or_none()

    if not skill:
        # Look up skill details from library if available
        skill_data = None
        if skills_library:
            skill_data = next(
                (s for s in skills_library if s["name"] == skill_name), None
            )

        if skill_data:
            print(f"   + Creating skill from library: {skill_name}")
            skill = Skill(
                id=uuid4(),
                name=skill_name,
                category=skill_data.get("category", "general"),
                description=skill_data.get("description", ""),
            )
        else:
            print(f"   + Creating new skill (no library entry): {skill_name}")
            skill = Skill(
                id=uuid4(),
                name=skill_name,
                category="general",
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
    milestones_data = data.get("milestones", [])
    skills_library = data.get("skills_library", [])

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
                slug=program_data.get("slug"),
                description=program_data.get("description"),
                level=ProgramLevel(program_data.get("level", "beginner_1")),
                duration_weeks=program_data.get("duration_weeks", 12),
                default_capacity=program_data.get("default_capacity", 10),
                currency=program_data.get("currency", "NGN"),
                price_amount=program_data.get("price_amount", 0),
                billing_type=BillingType(program_data.get("billing_type", "one_time")),
                prep_materials=program_data.get("prep_materials"),
                # Populate curriculum_json for Admin UI display (UI expects 'week' key, seed has 'week_number')
                curriculum_json={
                    "weeks": [{**w, "week": w["week_number"]} for w in curriculum_data]
                },
                is_published=program_data.get("is_published", False),
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
                    order_index=week_data["week_number"],  # Use week_number as order
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
                        skill = await get_or_create_skill(
                            session, s_name, skills_library
                        )
                        link = LessonSkill(
                            id=uuid4(), lesson_id=lesson.id, skill_id=skill.id
                        )
                        session.add(link)

            # 4. Create Milestones
            if milestones_data:
                print(f"Creating {len(milestones_data)} milestones...")
                for m_data in milestones_data:
                    milestone = Milestone(
                        id=uuid4(),
                        program_id=program.id,
                        name=m_data["name"],
                        criteria=m_data.get("criteria"),
                        order_index=m_data.get("order_index", 0),
                        milestone_type=MilestoneType(
                            m_data.get("milestone_type", "skill")
                        ),
                        required_evidence=RequiredEvidence(
                            m_data.get("required_evidence", "none")
                        ),
                        rubric_json=m_data.get("rubric_json"),
                    )
                    session.add(milestone)
                    print(f"   + Milestone: {m_data['name']}")

            print(f"✅ Successfully seeded program: {program.name} (ID: {program.id})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="Path to JSON data file")
    args = parser.parse_args()

    asyncio.run(seed_program(args.file))
