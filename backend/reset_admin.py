import os
from database import LocalSession, User, hash_password

def reset_password(email, new_password):
    db = LocalSession()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            print(f"User {email} not found.")
            return
        
        user.password_hash = hash_password(new_password)
        db.commit()
        print(f"Password for {email} reset successfully to '{new_password}'.")
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    import sys
    email = sys.argv[1] if len(sys.argv) > 1 else "admin@icp.ae"
    password = sys.argv[2] if len(sys.argv) > 2 else "admin123"
    reset_password(email, password)
