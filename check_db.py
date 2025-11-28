"""Check database statement timeout and other settings"""
import asyncio
from sqlalchemy import text
from libs.db.config import engine

async def check_db_settings():
    async with engine.begin() as conn:
        result = await conn.execute(text("SHOW statement_timeout;"))
        timeout = result.scalar()
        print(f"Current statement_timeout: {timeout}")
        
        result = await conn.execute(text("SELECT COUNT(*) FROM members;"))
        count = result.scalar()
        print(f"Members in database: {count}")
        
        result = await conn.execute(text("""
            SELECT tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
            FROM pg_tables 
            WHERE schemaname = 'public'
            ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
            LIMIT 5;
        """))
        print("\nLargest tables:")
        for row in result:
            print(f"  {row[0]}: {row[1]}")

if __name__ == "__main__":
    asyncio.run(check_db_settings())
