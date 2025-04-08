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
        
        # Create subscription_plans table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscription_plans (
                id INT PRIMARY KEY AUTO_INCREMENT,
                name VARCHAR(100) NOT NULL,
                price DECIMAL(10,2) NOT NULL,
                duration_months INT NOT NULL,
                description TEXT,
                degree_access ENUM('Dpharm', 'Bpharm', 'both') NOT NULL,
                includes_previous_years BOOLEAN DEFAULT TRUE,
                is_institution BOOLEAN DEFAULT FALSE,
                student_range INT NOT NULL DEFAULT 50,
                custom_student_range BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INT PRIMARY KEY AUTO_INCREMENT,
                username VARCHAR(50) UNIQUE NOT NULL,
                email VARCHAR(100) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                role ENUM('individual', 'student', 'instituteadmin', 'superadmin') NOT NULL,
                subscription_plan_id INT,
                subscription_start DATETIME,
                subscription_end DATETIME,
                subscription_status ENUM('active', 'expired', 'cancelled') DEFAULT 'expired',
                status ENUM('active', 'inactive') NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active DATETIME,
                FOREIGN KEY (subscription_plan_id) REFERENCES subscription_plans(id)
            )
        ''')
        
        # Create institutions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS institutions (
                id INT PRIMARY KEY AUTO_INCREMENT,
                name VARCHAR(100) NOT NULL,
                institution_code VARCHAR(50) UNIQUE NOT NULL,
                email VARCHAR(100) UNIQUE NOT NULL,
                admin_id INT,
                subscription_plan_id INT,
                subscription_start DATETIME,
                subscription_end DATETIME,
                student_range INT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (subscription_plan_id) REFERENCES subscription_plans(id),
                FOREIGN KEY (admin_id) REFERENCES users(id)
            )
        ''')
        
        # Create institution_students table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS institution_students (
                id INT PRIMARY KEY AUTO_INCREMENT,
                institution_id INT NOT NULL,
                user_id INT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (institution_id) REFERENCES institutions(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')
        
        # Create exams table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS exams (
                id INT PRIMARY KEY AUTO_INCREMENT,
                name VARCHAR(100) NOT NULL,
                degree_type ENUM('Dpharm', 'Bpharm') NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create plan_exam_access table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS plan_exam_access (
                id INT PRIMARY KEY AUTO_INCREMENT,
                plan_id INT NOT NULL,
                exam_id INT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (plan_id) REFERENCES subscription_plans(id),
                FOREIGN KEY (exam_id) REFERENCES exams(id),
                UNIQUE KEY unique_plan_exam (plan_id, exam_id)
            )
        ''')
        
        # Create subjects table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subjects (
                id INT PRIMARY KEY AUTO_INCREMENT,
                name VARCHAR(100) NOT NULL,
                exam_id INT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (exam_id) REFERENCES exams(id)
            )
        ''')
        
        # Create questions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS questions (
                id INT PRIMARY KEY AUTO_INCREMENT,
                question TEXT NOT NULL,
                option_a TEXT NOT NULL,
                option_b TEXT NOT NULL,
                option_c TEXT NOT NULL,
                option_d TEXT NOT NULL,
                correct_answer CHAR(1) NOT NULL,
                explanation TEXT,
                difficulty ENUM('easy', 'medium', 'hard') DEFAULT 'medium',
                chapter VARCHAR(100),
                subject_id INT NOT NULL,
                is_previous_year BOOLEAN DEFAULT FALSE,
                previous_year INT,
                topics JSON,
                created_by INT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (subject_id) REFERENCES subjects(id),
                FOREIGN KEY (created_by) REFERENCES users(id)
            )
        ''')
        
        # Create results table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS results (
                id INT PRIMARY KEY AUTO_INCREMENT,
                user_id INT NOT NULL,
                exam_id INT NOT NULL,
                score INT NOT NULL,
                total_questions INT NOT NULL,
                time_taken INT NOT NULL,
                answers JSON,
                date_taken DATETIME NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (exam_id) REFERENCES exams(id)
            )
        ''')
        
        # Create question_reviews table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS question_reviews (
                id INT PRIMARY KEY AUTO_INCREMENT,
                question_id INT NOT NULL,
                user_id INT NOT NULL,
                rating INT NOT NULL CHECK (rating BETWEEN 1 AND 5),
                comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (question_id) REFERENCES questions(id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                UNIQUE KEY unique_user_question_review (user_id, question_id)
            )
        ''')
        
        # Insert superadmin user if not exists
        admin_password = "admin123"
        password_hash = generate_password_hash(admin_password, method='pbkdf2:sha256:600000')
        
        cursor.execute('''
            INSERT INTO users (username, email, password_hash, role, status, subscription_status, last_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
            password_hash = VALUES(password_hash),
            last_active = VALUES(last_active)
        ''', ('superadmin', 'admin@example.com', password_hash, 'superadmin', 'active', 'active', datetime.now()))
        
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
                (name, price, duration_months, description, degree_access, includes_previous_years, is_institution, student_range, custom_student_range)
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