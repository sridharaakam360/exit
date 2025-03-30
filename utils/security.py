# utils/security.py
from flask import session, redirect, url_for, flash, abort
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
        if 'username' not in session or 'user_id' not in session:
            flash('Please log in to access this page.', 'danger')
            logger.warning("Unauthorized access attempt to protected route")
            return redirect(url_for('auth.choose_login'))
        return f(*args, **kwargs)
    return decorated_function

def super_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'role' not in session or session['role'] != 'superadmin':
            flash('Only superadmins can access this page.', 'danger')
            logger.info(f"Non-superadmin user {session.get('username', 'unknown')} attempted to access admin route")
            if session.get('role') == 'instituteadmin':
                return redirect(url_for('institution.institution_dashboard'))
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

def institute_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'role' not in session or 'user_id' not in session:
            logger.warning("Missing basic session data")
            flash('Session expired or invalid. Please log in again.', 'danger')
            return redirect(url_for('auth.login'))
            
        # Superadmins can access everything
        if session['role'] == 'superadmin':
            return f(*args, **kwargs)
            
        # For institute admins, check institution_id
        if session['role'] == 'instituteadmin':
            if 'institution_id' not in session:
                logger.warning("Missing institution_id for institute admin")
                flash('Session expired or invalid. Please log in again.', 'danger')
                return redirect(url_for('auth.institution_login'))
            return f(*args, **kwargs)
            
        # For other roles, deny access
        flash('You do not have permission to access this page.', 'danger')
        logger.warning(f"Unauthorized access attempt by user {session.get('username', 'unknown')} with role {session.get('role', 'unknown')}")
        return redirect(url_for('auth.login'))
            
    return decorated_function

def quiz_access_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'role' not in session or 'user_id' not in session:
            flash('Please log in to access quizzes.', 'danger')
            return redirect(url_for('auth.login'))
        role = session['role']
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
                                FROM users WHERE id = %s''', (session['user_id'],))
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