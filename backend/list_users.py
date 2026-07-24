"""
User Database Directory Listing Script.

This command-line utility connects to the application database, queries all registered 
users (both admin and standard roles), and prints their basic information as a formatted 
table to standard output.
"""

import os
from database import LocalSession, User

def list_users():
    """
    Retrieves all users from the database and prints them in a tabular format.

    Queries the `User` model, fetches all rows, iterates through them, and prints 
    information including ID, Email, Name, Role, and Active status.

    Args:
        None

    Returns:
        None: This function prints to stdout and returns nothing.

    Example Printed Output:
        ```text
        ID    | Email                          | Name                 | Role       | Active  
        --------------------------------------------------------------------------------
        1     | admin@icp.ae                   | Admin                | admin      | 1       
        2     | user@icp.ae                    | Syed Faraz           | user       | 1       
        ```
    """
    db = LocalSession()
    try:
        users = db.query(User).all()
        if not users:
            print("No users found in database.")
            return
        
        print(f"{'ID':<5} | {'Email':<30} | {'Name':<20} | {'Role':<10} | {'Active':<8}")
        print("-" * 80)
        for u in users:
            print(f"{u.id:<5} | {u.email:<30} | {u.name:<20} | {u.role:<10} | {u.is_active:<8}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    list_users()

