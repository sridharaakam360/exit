# config.py
from datetime import datetime, timedelta

class Config:
    SECRET_KEY = 'my-static-secret-key-12345'
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    REDIS_URL = 'redis://localhost:6379/0'
    MYSQL_HOST = 'localhost'
    MYSQL_USER = 'root'
    MYSQL_PASSWORD = 'newpass123'  # Replace with actual password
    MYSQL_DB = 'pharmacy_app'