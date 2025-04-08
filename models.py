import mysql.connector
from mysql.connector import pooling
import logging
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
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

        # Subjects table
        cursor.execute('''CREATE TABLE IF NOT EXISTS subjects (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            degree_type ENUM('Dpharm', 'Bpharm') NOT NULL,
            description TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
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
            explanation TEXT,
            difficulty ENUM('easy', 'medium', 'hard') DEFAULT 'medium',
            chapter VARCHAR(100),
            subject_id INT NOT NULL,
            is_previous_year BOOLEAN DEFAULT FALSE,
            previous_year INT,
            topics JSON,
            created_by INT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
            FOREIGN KEY (created_by) REFERENCES users(id)
        )''')

        # Results table
        cursor.execute('''CREATE TABLE IF NOT EXISTS results (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            subject_id INT NOT NULL,
            score INT NOT NULL,
            total_questions INT NOT NULL,
            time_taken INT NOT NULL,
            answers JSON,
            date_taken DATETIME NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (subject_id) REFERENCES subjects(id)
        )''')

        # Question reviews table
        cursor.execute('''CREATE TABLE IF NOT EXISTS question_reviews (
            id INT AUTO_INCREMENT PRIMARY KEY,
            question_id INT NOT NULL,
            user_id INT NOT NULL,
            rating INT NOT NULL CHECK (rating BETWEEN 1 AND 5),
            comment TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE KEY unique_user_question_review (user_id, question_id)
        )''')

        # Subscription plans table
        cursor.execute('''CREATE TABLE IF NOT EXISTS subscription_plans (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            price DECIMAL(10,2) NOT NULL,
            duration_months INT NOT NULL,
            description TEXT,
            degree_access ENUM('Dpharm', 'Bpharm', 'both') NOT NULL,
            includes_previous_years BOOLEAN DEFAULT TRUE,
            is_institution BOOLEAN DEFAULT FALSE,
            student_range INT NOT NULL DEFAULT 50,
            custom_student_range BOOLEAN DEFAULT FALSE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')

        # Institutions table
        cursor.execute('''CREATE TABLE IF NOT EXISTS institutions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            institution_code VARCHAR(50) UNIQUE NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            admin_id INT,
            subscription_plan_id INT,
            subscription_start DATETIME,
            subscription_end DATETIME,
            student_range INT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (subscription_plan_id) REFERENCES subscription_plans(id),
            FOREIGN KEY (admin_id) REFERENCES users(id)
        )''')

        # Institution students table
        cursor.execute('''CREATE TABLE IF NOT EXISTS institution_students (
            id INT AUTO_INCREMENT PRIMARY KEY,
            institution_id INT NOT NULL,
            user_id INT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (institution_id) REFERENCES institutions(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
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
        raise
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

class User(UserMixin):
    def __init__(self, id, username, email, password=None, password_hash=None, role='individual', status='active', degree='none', 
                 user_type='individual', institution_id=None, subscription_plan_id=None,
                 subscription_start=None, subscription_end=None, subscription_status='pending',
                 last_active=None, created_at=None):
        self.id = id
        self.username = username
        self.email = email
        self.password = password or password_hash  # Handle both password and password_hash
        self.role = role
        self.status = status
        self.degree = degree
        self.user_type = user_type
        self.institution_id = institution_id
        self.subscription_plan_id = subscription_plan_id
        self.subscription_start = subscription_start
        self.subscription_end = subscription_end
        self.subscription_status = subscription_status
        self.last_active = last_active
        self.created_at = created_at

    @property
    def is_active(self):
        return self.status == 'active'

    @property
    def is_admin(self):
        return self.role in ['superadmin', 'instituteadmin']

    @staticmethod
    def get_by_id(user_id):
        conn = get_db_connection()
        if not conn:
            return None
        
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))
            user_data = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if user_data:
                return User(**user_data)
            return None
        except Exception as e:
            logger.error(f"Error getting user by id: {str(e)}")
            return None

    @staticmethod
    def get_by_username(username):
        conn = get_db_connection()
        if not conn:
            return None
        
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute('SELECT * FROM users WHERE username = %s', (username,))
            user_data = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if user_data:
                return User(**user_data)
            return None
        except Exception as e:
            logger.error(f"Error getting user by username: {str(e)}")
            return None

    def check_password(self, password):
        return check_password_hash(self.password, password)