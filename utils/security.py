# utils/security.py
from flask import session, redirect, url_for, flash, abort
from flask_login import current_user
from functools import wraps
import bleach
import logging
from datetime import datetime


logger = logging.getLogger(__name__)

def sanitize_input(data):
    """Sanitize input data to prevent XSS and injection attacks."""
    if isinstance(data, str):
        return bleach.clean(data)
    return data

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('Please log in to access this page.', 'danger')
            logger.warning("Unauthorized access attempt to protected route")
            return redirect(url_for('auth.choose_login'))
        return f(*args, **kwargs)
    return decorated_function

def super_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'superadmin':
            flash('Only superadmins can access this page.', 'danger')
            logger.info(f"Non-superadmin user {current_user.username if current_user.is_authenticated else 'unknown'} attempted to access admin route")
            if current_user.is_authenticated and current_user.role == 'instituteadmin':
                return redirect(url_for('institution.institution_dashboard'))
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

def institute_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            logger.warning("User not authenticated")
            flash('Session expired or invalid. Please log in again.', 'danger')
            return redirect(url_for('auth.login'))
            
        # Superadmins can access everything
        if current_user.role == 'superadmin':
            return f(*args, **kwargs)
            
        # For institute admins, check institution_id
        if current_user.role == 'instituteadmin':
            if not hasattr(current_user, 'institution_id') or not current_user.institution_id:
                logger.warning("Missing institution_id for institute admin")
                flash('Session expired or invalid. Please log in again.', 'danger')
                return redirect(url_for('auth.institution_login'))
            return f(*args, **kwargs)
            
        # For other roles, deny access
        flash('You do not have permission to access this page.', 'danger')
        logger.warning(f"Unauthorized access attempt by user {current_user.username if current_user.is_authenticated else 'unknown'} with role {current_user.role if current_user.is_authenticated else 'unknown'}")
        return redirect(url_for('auth.login'))
            
    return decorated_function

def quiz_access_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('Please log in to access quizzes.', 'danger')
            return redirect(url_for('auth.login'))
            
        role = current_user.role
        if role in ['superadmin', 'instituteadmin', 'student']:
            return f(*args, **kwargs)
        elif role == 'individual':
            from utils.db import get_db_connection
            conn = get_db_connection()
            if conn is None:
                flash('Database connection error.', 'danger')
                return redirect(url_for('user.user_dashboard'))
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute('''SELECT subscription_end, subscription_status 
                                FROM users WHERE id = %s''', (current_user.id,))
                user = cursor.fetchone()
                current_date = datetime.now().date()
                if user and user['subscription_end'] and user['subscription_end'].date() >= current_date and user['subscription_status'] == 'active':
                    return f(*args, **kwargs)
                else:
                    flash('You need an active subscription to access quizzes.', 'warning')
                    return redirect(url_for('user.subscriptions'))
            except Exception as e:
                logger.error(f"Error checking subscription: {str(e)}")
                flash('Error verifying access.', 'danger')
                return redirect(url_for('user.user_dashboard'))
            finally:
                cursor.close()
                conn.close()
        else:
            abort(403)
    return decorated_function