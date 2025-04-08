import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db import get_db_connection

def add_degree_type_column():
    conn = get_db_connection()
    if not conn:
        print("Database connection error")
        return
    
    cursor = conn.cursor()
    try:
        # Check if degree_type column exists
        cursor.execute("SHOW COLUMNS FROM subjects LIKE 'degree_type'")
        if not cursor.fetchone():
            # Add degree_type column
            cursor.execute('''
                ALTER TABLE subjects 
                ADD COLUMN degree_type ENUM('Dpharm', 'Bpharm') NOT NULL DEFAULT 'Dpharm'
            ''')
            conn.commit()
            print("Added degree_type column to subjects table")
        else:
            print("degree_type column already exists")
    except Exception as e:
        print(f"Error: {str(e)}")
        conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

if __name__ == "__main__":
    add_degree_type_column() 