import asyncio
import os
import sys

# Add the project root to the python path
sys.path.append(os.getcwd())

from libs.db.session import AsyncSessionLocal
from services.academy_service.models import (
    Cohort,
    CurriculumLesson,
    CurriculumWeek,
    LessonSkill,
    Milestone,
    Program,
    ProgramCurriculum,
)
from sqlalchemy import select
from sqlalchemy.orm import joinedload


async def clean_program():
    slug = "beginner-freestyle-50m"
    async with AsyncSessionLocal() as session:
        print(f"Finding program with slug: {slug}")
        # Find program
        stmt = select(Program).where(Program.slug == slug)
        result = await session.execute(stmt)
        program = result.scalar_one_or_none()

        if not program:
            print("Program not found. Nothing to clean.")
            return

        print(f"Found program: {program.name} ({program.id})")

        # 1. Delete Dependencies first (manual cascade since not configured in ORM/DB likely)

        # Delete Milestones
        print("Deleting milestones...")
        stmt = select(Milestone).where(Milestone.program_id == program.id)
        result = await session.execute(stmt)
        milestones = result.scalars().all()
        for m in milestones:
            await session.delete(m)

        # Delete Cohorts
        print("Deleting cohorts...")
        stmt = select(Cohort).where(Cohort.program_id == program.id)
        result = await session.execute(stmt)
        cohorts = result.scalars().all()
        for c in cohorts:
            await session.delete(c)

        # Delete Curriculum (Complex hierarchy)
        print("Deleting curriculum...")
        stmt = select(ProgramCurriculum).where(
            ProgramCurriculum.program_id == program.id
        )
        result = await session.execute(stmt)
        curricula = result.scalars().all()

        for curr in curricula:
            # Get Weeks
            stmt = select(CurriculumWeek).where(CurriculumWeek.curriculum_id == curr.id)
            result = await session.execute(stmt)
            weeks = result.scalars().all()

            for week in weeks:
                # Get Lessons
                stmt = select(CurriculumLesson).where(
                    CurriculumLesson.week_id == week.id
                )
                result = await session.execute(stmt)
                lessons = result.scalars().all()

                for lesson in lessons:
                    # Delete Lesson Skills
                    stmt = select(LessonSkill).where(LessonSkill.lesson_id == lesson.id)
                    result = await session.execute(stmt)
                    lesson_skills = result.scalars().all()
                    for ls in lesson_skills:
                        await session.delete(ls)

                    await session.delete(lesson)

                await session.delete(week)

            await session.delete(curr)

        # Finally Delete Program
        print("Deleting program...")
        await session.delete(program)

        await session.commit()
        print("Program and all related data successfully deleted.")


if __name__ == "__main__":
    asyncio.run(clean_program())
