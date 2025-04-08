import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db import get_db_connection

def remove_exam_id_column():
    conn = get_db_connection()
    if not conn:
        print("Database connection error")
        return
    
    cursor = conn.cursor()
    try:
        # Check if exam_id column exists
        cursor.execute("SHOW COLUMNS FROM subjects LIKE 'exam_id'")
        if cursor.fetchone():
            # Remove foreign key constraint if it exists
            cursor.execute('''
                SELECT CONSTRAINT_NAME
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_NAME = 'subjects'
                AND COLUMN_NAME = 'exam_id'
                AND REFERENCED_TABLE_NAME IS NOT NULL
            ''')
            constraint = cursor.fetchone()
            if constraint:
                cursor.execute(f'ALTER TABLE subjects DROP FOREIGN KEY {constraint[0]}')
            
            # Drop the exam_id column
            cursor.execute('ALTER TABLE subjects DROP COLUMN exam_id')
            conn.commit()
            print("Removed exam_id column from subjects table")
        else:
            print("exam_id column does not exist")
    except Exception as e:
        print(f"Error: {str(e)}")
        conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

if __name__ == "__main__":
    remove_exam_id_column() 