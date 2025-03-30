import mysql.connector
from werkzeug.security import generate_password_hash
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'newpass123',  # Add your MySQL root password if needed
    'charset': 'utf8mb4',
    'collation': 'utf8mb4_unicode_ci'
}

def init_db():
    # Connect without database first
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Create database if it doesn't exist
        cursor.execute("CREATE DATABASE IF NOT EXISTS pharmacy_app")
        cursor.execute("USE pharmacy_app")
        
        # Create users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                email VARCHAR(120) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                role ENUM('superadmin', 'instituteadmin', 'individual', 'student') NOT NULL,
                user_type ENUM('individual', 'institutional') NOT NULL,
                subscription_status ENUM('active', 'inactive', 'expired') NOT NULL DEFAULT 'inactive',
                institution_id INT,
                last_active DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create subscription_plans table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscription_plans (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                price DECIMAL(10,2) NOT NULL,
                duration_days INT NOT NULL,
                description TEXT,
                degree_access ENUM('Dpharm', 'Bpharm', 'both') NOT NULL,
                includes_previous_years BOOLEAN DEFAULT TRUE,
                is_institution BOOLEAN DEFAULT FALSE,
                student_range INT NOT NULL DEFAULT 50,
                custom_student_range BOOLEAN DEFAULT FALSE,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Insert admin user if not exists
        admin_password = "admin123"
        password_hash = generate_password_hash(admin_password, method='pbkdf2:sha256:600000')
        
        cursor.execute('''
            INSERT INTO users (username, email, password_hash, role, user_type, subscription_status, last_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
            password_hash = VALUES(password_hash),
            last_active = VALUES(last_active)
        ''', ('admin', 'admin@example.com', password_hash, 'superadmin', 'individual', 'active', datetime.now()))
        
        # Insert default subscription plans
        plans = [
            # Individual plans
            ('Dpharm Package', 999.00, 365, 'Access to Dpharm exams', 'Dpharm', True, False, 1, False),
            ('Bpharm Package', 1499.00, 365, 'Access to Bpharm exams', 'Bpharm', True, False, 1, False),
            ('Combo Package', 1999.00, 365, 'Access to all exams', 'both', True, False, 1, False),
            
            # Institution plans
            ('Basic Institution', 4999.00, 365, 'Basic institution plan with up to 50 students', 'both', True, True, 50, False),
            ('Standard Institution', 9999.00, 365, 'Standard institution plan with up to 100 students', 'both', True, True, 100, False),
            ('Premium Institution', 19999.00, 365, 'Premium institution plan with up to 200 students', 'both', True, True, 200, False),
            ('Custom Institution', 9999.00, 365, 'Custom institution plan with flexible student range', 'both', True, True, 50, True)
        ]
        
        cursor.execute('SELECT COUNT(*) as count FROM subscription_plans')
        if cursor.fetchone()['count'] == 0:
            cursor.executemany('''
                INSERT INTO subscription_plans 
                (name, price, duration_days, description, degree_access, includes_previous_years, is_institution, student_range, custom_student_range)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', plans)
        
        conn.commit()
        logger.info("Database initialized successfully")
        
    except Exception as e:
        logger.error(f"Error initializing database: {str(e)}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    init_db() 