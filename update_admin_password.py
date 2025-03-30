from utils.db import get_db_connection
from werkzeug.security import generate_password_hash
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def update_admin_password():
    conn = get_db_connection()
    if conn is None:
        logger.error("Failed to connect to database")
        return
    
    cursor = conn.cursor(dictionary=True)
    try:
        # Generate new password hash with correct format
        new_password = "admin123"  # Default admin password
        password_hash = generate_password_hash(new_password, method='pbkdf2:sha256:600000')
        
        # Update admin user's password hash
        cursor.execute('''
            UPDATE users 
            SET password_hash = %s 
            WHERE username = %s
        ''', (password_hash, 'admin'))
        
        conn.commit()
        logger.info("Admin password updated successfully")
        
    except Exception as e:
        logger.error(f"Error updating admin password: {str(e)}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    update_admin_password() 