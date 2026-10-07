from .database import get_db_path
from .schema import init_db

def main():
    db_path = get_db_path()
    print(f"Initializing VibeGuard database at {db_path}...")
    init_db(db_path)
    print("Database initialized successfully.")

if __name__ == "__main__":
    main()
