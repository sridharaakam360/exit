import mysql.connector
from mysql.connector import Error
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': 'newpass123',  # Updated to match init_db.py
    'database': 'pharmacy_app',  # Updated to match your database name
    'charset': 'utf8mb4',
    'collation': 'utf8mb4_unicode_ci',
    'pool_name': 'mypool',
    'pool_size': 5,
    'pool_reset_session': True
}

def get_db_connection():
    try:
        conn = mysql.connector.connect(**db_config)
        if conn.is_connected():
            logger.info('Connected to MySQL database')
            return conn
    except Error as e:
        logger.error(f"Error connecting to MySQL: {e}")
    return None