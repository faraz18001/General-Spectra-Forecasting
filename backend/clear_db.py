import os
from database import engine, Base, init_db
from sqlalchemy.orm import sessionmaker
from database import User, hash_password

def clear_database():
    print("WARNING: This will drop all tables and recreate them.")
    print(f"Connecting to database at {engine.url}")
    
    # Drop all tables defined in Base
    print("Dropping all tables...")
    Base.metadata.drop_all(bind=engine)
    print("All tables dropped successfully.")
    
    # Recreate all tables
    print("Recreating tables...")
    init_db()
    
    # Recreate the default admin user since we just dropped it
    # We should ensure there is at least one admin to log in with
    db = sessionmaker(bind=engine)()
    try:
        print("Recreating default admin user (admin@icp.ae)...")
        password_hash = hash_password("admin123")
        admin = User(email="admin@icp.ae", name="Admin", password_hash=password_hash, role="admin", is_active=True)
        db.add(admin)
        db.commit()
        print("Default admin created successfully.")
    except Exception as e:
        db.rollback()
        print(f"Failed to create admin: {e}")
    finally:
        db.close()
        
    print("Database has been completely reset.")

if __name__ == "__main__":
    reply = input("Are you sure you want to completely empty the database? Type 'yes' to proceed: ")
    if reply.lower() == 'yes':
        clear_database()
    else:
        print("Aborted.")
