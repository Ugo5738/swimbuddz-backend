import asyncio
import os
import urllib.parse
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

# Load .env.prod manually
def load_env_prod():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env.prod')
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

load_env_prod()

def get_safe_database_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL not found in .env.prod")
    
    # Handle password encoding if needed
    if '@' in url:
        last_at_index = url.rfind('@')
        if last_at_index != -1:
            auth_part = url[:last_at_index]
            host_part = url[last_at_index+1:]
            
            if ':' in auth_part:
                scheme_end = auth_part.find('://')
                if scheme_end != -1:
                    scheme = auth_part[:scheme_end+3]
                    user_pass = auth_part[scheme_end+3:]
                    
                    if ':' in user_pass:
                        user, password = user_pass.split(':', 1)
                        password = urllib.parse.quote_plus(password)
                        return f"{scheme}{user}:{password}@{host_part}"
    return url

async def nuke_tables():
    env_var = "DATABASE_URL"
    raw_url = os.environ.get(env_var)
    if not raw_url:
        print(f"Error: {env_var} not found in environment")
        return
        
    print(f"\nNuking database using {env_var}...")
    db_url = raw_url # Use raw URL as check_db_tables.py did, assuming it works
    
    # Mask password for logging
    masked_url = db_url
    if '@' in masked_url:
        try:
            scheme_part, host_part = masked_url.rsplit('@', 1)
            if ':' in scheme_part:
                scheme_user, _ = scheme_part.rsplit(':', 1)
                masked_url = f"{scheme_user}:***@{host_part}"
        except:
            pass
            
    print(f"Connecting to: {masked_url}")
    
    engine = create_async_engine(db_url, echo=False)
    try:
        async with engine.begin() as conn:
            def nuke(sync_conn):
                print("Dropping public schema...")
                sync_conn.execute(text("DROP SCHEMA public CASCADE;"))
                print("Recreating public schema...")
                sync_conn.execute(text("CREATE SCHEMA public;"))
                sync_conn.execute(text("GRANT ALL ON SCHEMA public TO public;"))
                print("Database nuked successfully.")
            
            await conn.run_sync(nuke)
            
    except Exception as e:
        print(f"Error nuking database: {e}")
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(nuke_tables())
