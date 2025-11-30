import asyncio
import os
import urllib.parse
from sqlalchemy import inspect
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
        # Split into scheme://user:pass and host:port/db
        # Find the LAST @ which separates auth from host
        last_at_index = url.rfind('@')
        if last_at_index != -1:
            auth_part = url[:last_at_index]
            host_part = url[last_at_index+1:]
            
            if ':' in auth_part:
                # scheme://user:pass
                # Find the first : after scheme://
                # scheme is usually postgresql+psycopg://
                scheme_end = auth_part.find('://')
                if scheme_end != -1:
                    scheme = auth_part[:scheme_end+3]
                    user_pass = auth_part[scheme_end+3:]
                    
                    if ':' in user_pass:
                        user, password = user_pass.split(':', 1)
                        # Encode password
                        password = urllib.parse.quote_plus(password)
                        return f"{scheme}{user}:{password}@{host_part}"
    return url

async def check_tables():
    # Only check DATABASE_URL as that is what the app uses
    env_var = "DATABASE_URL"
    raw_url = os.environ.get(env_var)
    if not raw_url:
        print(f"Error: {env_var} not found in environment")
        return
        
    print(f"\nChecking {env_var}...")
    
    # Use URL directly from environment
    db_url = raw_url
    
    # Mask password for logging
    masked_url = db_url
    if '@' in masked_url:
        try:
            scheme_part, host_part = masked_url.rsplit('@', 1)
            if ':' in scheme_part:
                scheme_user, _ = scheme_part.rsplit(':', 1)
                masked_url = f"{scheme_user}:***@{host_part}"
        except Exception:
            pass
            
    print(f"Connecting to: {masked_url}")
    
    engine = create_async_engine(db_url, echo=False)
    try:
        async with engine.connect() as conn:
            def get_tables(sync_conn):
                inspector = inspect(sync_conn)
                return inspector.get_table_names()
            
            tables = await conn.run_sync(get_tables)
            print(f"SUCCESS! Tables found in database using {env_var}:")
            for table in tables:
                print(f"- {table}")
            
            required_tables = {'users', 'members', 'sessions', 'attendance', 'communications', 'payments'}
            missing = [t for t in required_tables if t not in tables]
            
            if missing:
                print(f"\nWARNING: Potential missing tables: {missing}")
            else:
                print("\nAll core tables seem to be present.")
    except Exception as e:
        print(f"Error connecting with {env_var}: {e}")
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(check_tables())
