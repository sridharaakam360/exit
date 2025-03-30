import mysql.connector
from mysql.connector import pooling
import logging
from werkzeug.security import generate_password_hash
from config import Config
import os

logger = logging.getLogger(__name__)

db_config = {
    'pool_name': 'pharmacy_pool',
    'pool_size': 20,
    'host': Config.MYSQL_HOST,
    'user': Config.MYSQL_USER,
    'password': Config.MYSQL_PASSWORD,
    'database': Config.MYSQL_DB,
    'raise_on_warnings': True,
    'autocommit': False,
    'use_pure': True,
    'connection_timeout': 30
}

connection_pool = None

def get_db_connection():
    global connection_pool
    try:
        if connection_pool is None:
            connection_pool = pooling.MySQLConnectionPool(**db_config)
            logger.debug("Created DB connection pool")
        conn = connection_pool.get_connection()
        if conn.is_connected():
            logger.debug("Database connection established successfully")
            return conn
    except mysql.connector.Error as err:
        logger.error(f"Database connection error: {str(err)}")
        return None

def init_db():
    try:
        conn = mysql.connector.connect(
            host=db_config['host'],
            user=db_config['user'],
            password=db_config['password']
        )
        cursor = conn.cursor()
        cursor.execute("CREATE DATABASE IF NOT EXISTS pharmacy_app")
        cursor.execute("USE pharmacy_app")

        # Users table
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            email VARCHAR(255) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL,
            degree ENUM('Dpharm', 'Bpharm', 'none') DEFAULT 'none',
            role ENUM('individual', 'student', 'instituteadmin', 'superadmin') DEFAULT 'individual',
            user_type ENUM('individual', 'institutional') DEFAULT 'individual',
            status ENUM('active', 'inactive', 'suspended') DEFAULT 'active',
            institution_id INT,
            subscription_plan_id INT,
            subscription_start DATETIME,
            subscription_end DATETIME,
            subscription_status ENUM('active', 'expired', 'pending') DEFAULT 'pending',
            last_active DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_username (username),
            INDEX idx_role (role),
            INDEX idx_status (status),
            INDEX idx_last_active (last_active)
        )''')

        # Exams table
        cursor.execute('''CREATE TABLE IF NOT EXISTS exams (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name ENUM('MRB', 'ELITE', 'RRB', 'SBI', 'ISRO', 'Gpat', 'Drug Inspector', 'Junior Analyst', 'DRDO') NOT NULL,
            degree_type ENUM('Dpharm', 'Bpharm') NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')

        # Subjects table
        cursor.execute('''CREATE TABLE IF NOT EXISTS subjects (
            id INT AUTO_INCREMENT PRIMARY KEY,
            exam_id INT NOT NULL,
            name ENUM('Anatomy', 'Drug Store', 'Pharmacology(D)', 'Pharmaceutics(D)', 
                    'Medicinal Chemistry', 'Pharmaceutics(B)', 'Pharmacology(B)') NOT NULL,
            degree_type ENUM('Dpharm', 'Bpharm') NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE
        )''')

        # Questions table
        cursor.execute('''CREATE TABLE IF NOT EXISTS questions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            question TEXT NOT NULL,
            option_a VARCHAR(255) NOT NULL,
            option_b VARCHAR(255) NOT NULL,
            option_c VARCHAR(255) NOT NULL,
            option_d VARCHAR(255) NOT NULL,
            correct_answer CHAR(1) NOT NULL,
            category VARCHAR(50) NOT NULL,
            difficulty ENUM('easy', 'medium', 'hard') DEFAULT 'medium',
            subject_id INT,
            is_previous_year BOOLEAN DEFAULT FALSE,
            previous_year INT,
            topics JSON,
            explanation TEXT NOT NULL,
            created_by INT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE SET NULL,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        )''')

        # Subscription_plans table
        cursor.execute('''CREATE TABLE IF NOT EXISTS subscription_plans (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name ENUM('Dpharm Package', 'Bpharm Package', 'Combo Package') NOT NULL,
            price DECIMAL(10,2) NOT NULL,
            duration_days INT NOT NULL,
            description TEXT,
            degree_access ENUM('Dpharm', 'Bpharm', 'both') NOT NULL,
            includes_previous_years BOOLEAN DEFAULT TRUE,
            is_institution BOOLEAN DEFAULT FALSE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')

        # Plan_exam_access table
        cursor.execute('''CREATE TABLE IF NOT EXISTS plan_exam_access (
            id INT AUTO_INCREMENT PRIMARY KEY,
            plan_id INT NOT NULL,
            exam_id INT NOT NULL,
            subject_id INT,
            FOREIGN KEY (plan_id) REFERENCES subscription_plans(id) ON DELETE CASCADE,
            FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE,
            FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
            UNIQUE KEY unique_plan_exam_subject (plan_id, exam_id, subject_id)
        )''')

        # Results table
        cursor.execute('''CREATE TABLE IF NOT EXISTS results (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            exam_id INT,
            score INT NOT NULL,
            total_questions INT NOT NULL,
            time_taken INT NOT NULL,
            answers JSON,
            date_taken DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE SET NULL
        )''')

        # Question_reviews table
        cursor.execute('''CREATE TABLE IF NOT EXISTS question_reviews (
            id INT AUTO_INCREMENT PRIMARY KEY,
            question_id INT NOT NULL,
            user_id INT NOT NULL,
            comment TEXT,
            rating INT NOT NULL CHECK (rating >= 1 AND rating <= 5),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )''')

        # Subscription_history table
        cursor.execute('''CREATE TABLE IF NOT EXISTS subscription_history (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            subscription_plan_id INT NOT NULL,
            start_date DATETIME NOT NULL,
            end_date DATETIME NOT NULL,
            amount_paid DECIMAL(10,2) NOT NULL,
            payment_method VARCHAR(50) NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (subscription_plan_id) REFERENCES subscription_plans(id) ON DELETE CASCADE
        )''')

        # Institutions table
        cursor.execute('''CREATE TABLE IF NOT EXISTS institutions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            admin_id INT,
            subscription_plan_id INT,
            subscription_start DATETIME,
            subscription_end DATETIME,
            institution_code VARCHAR(20) UNIQUE NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (admin_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY (subscription_plan_id) REFERENCES subscription_plans(id) ON DELETE SET NULL
        )''')

        # Institution_students table
        cursor.execute('''CREATE TABLE IF NOT EXISTS institution_students (
            id INT AUTO_INCREMENT PRIMARY KEY,
            institution_id INT NOT NULL,
            user_id INT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (institution_id) REFERENCES institutions(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE KEY unique_student_institution (institution_id, user_id)
        )''')

        # Superadmin creation
        cursor.execute('SELECT COUNT(*) FROM users WHERE role = "superadmin"')
        if cursor.fetchone()[0] == 0:
            admin_password = os.getenv('ADMIN_PASSWORD', 'admin123')
            cursor.execute('''INSERT INTO users (
                username, email, password, role, status, degree, subscription_status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)''',
            ('superadmin', 'admin@example.com', generate_password_hash(admin_password), 'superadmin', 'active', 'none', 'active'))
            logger.info("Super admin user created successfully")
        conn.commit()
        logger.info("Database initialized successfully")
    except mysql.connector.Error as err:
        logger.error(f"Database initialization error: {str(err)}")
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()