from flask import Blueprint, render_template, redirect, url_for, flash, session, request
from forms import LoginTypeForm, LoginForm, InstitutionLoginForm, RegisterForm, InstitutionRegisterForm, StudentRegisterForm
from utils.db import get_db_connection
from utils.security import sanitize_input, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import logging
import random
import string
from mysql.connector import Error

logger = logging.getLogger(__name__)

def create_auth_bp(limiter):
    auth_bp = Blueprint('auth', __name__)
    
    @auth_bp.route('/choose_login', methods=['GET', 'POST'])
    def choose_login():
        form = LoginTypeForm()
        if form.validate_on_submit():
            login_type = form.login_type.data
            logger.info(f"Login type selected: {login_type}")
            if login_type == 'individual':
                return redirect(url_for('auth.login'))
            elif login_type == 'institution':
                return redirect(url_for('auth.institution_login'))
            elif login_type == 'student':
                return redirect(url_for('auth.student_login'))
            else:
                flash('Invalid login type selected.', 'danger')
                logger.error(f"Unexpected login_type: {login_type}")
        return render_template('choose_login.html', form=form)

    @auth_bp.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            username = request.form.get('username')
            password = request.form.get('password')
            
            if not username or not password:
                flash('Username and password are required.', 'danger')
                return redirect(url_for('auth.login'))
            
            conn = get_db_connection()
            if conn is None:
                flash('Database connection error.', 'danger')
                return redirect(url_for('auth.login'))
            
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute('SELECT * FROM users WHERE username = %s', (username,))
                user = cursor.fetchone()
                
                if user and user.get('password_hash'):
                    try:
                        if check_password_hash(user['password_hash'], password):
                            session['user_id'] = user['id']
                            session['username'] = user['username']
                            session['role'] = user['role']
                            
                            # Update last active time
                            cursor.execute('UPDATE users SET last_active = %s WHERE id = %s',
                                        (datetime.now(), user['id']))
                            conn.commit()
                            
                            if user['role'] == 'superadmin':
                                return redirect(url_for('admin.admin_dashboard'))
                            elif user['role'] == 'instituteadmin':
                                return redirect(url_for('institution.institution_dashboard'))
                            else:
                                return redirect(url_for('user.user_dashboard'))
                    except ValueError:
                        logger.error(f"Invalid password hash format for user {username}")
                        flash('Account configuration error. Please contact support.', 'danger')
                        return redirect(url_for('auth.login'))
                
                flash('Invalid username or password.', 'danger')
                return redirect(url_for('auth.login'))
                
            except Error as err:
                logger.error(f"Database error in login: {str(err)}")
                flash('Error during login.', 'danger')
                return redirect(url_for('auth.login'))
            finally:
                cursor.close()
                conn.close()
        
        return render_template('login.html')

    # Institution Login
    @auth_bp.route('/institution_login', methods=['GET', 'POST'])
    def institution_login():
        if request.method == 'POST':
            institution_code = request.form.get('institution_code')
            username = request.form.get('username')
            password = request.form.get('password')
            
            if not all([institution_code, username, password]):
                flash('All fields are required.', 'danger')
                return render_template('institution_login.html')
            
            conn = get_db_connection()
            if conn is None:
                logger.error("Failed to get DB connection")
                flash('Database connection error.', 'danger')
                return render_template('institution_login.html')
            
            cursor = conn.cursor(dictionary=True)
            
            try:
                # First, find the institution by code
                cursor.execute('SELECT id FROM institutions WHERE institution_code = %s', (institution_code,))
                institution = cursor.fetchone()
                
                if not institution:
                    flash('Invalid institution code.', 'danger')
                    return render_template('institution_login.html')
                
                # Then check the admin credentials
                cursor.execute('''
                    SELECT u.*, i.id as institution_id, i.name as institution_name 
                    FROM users u
                    JOIN institutions i ON u.id = i.admin_id
                    WHERE u.username = %s AND i.institution_code = %s AND u.role = 'instituteadmin'
                ''', (username, institution_code))
                user = cursor.fetchone()
                
                if user and check_password_hash(user['password_hash'], password):
                    if user['status'] != 'active':
                        flash('Your account is not active.', 'danger')
                    else:
                        # Set all required session data
                        session['user_id'] = user['id']
                        session['username'] = user['username']
                        session['role'] = 'instituteadmin'
                        session['institution_id'] = user['institution_id']
                        session['institution_name'] = user['institution_name']
                        session.permanent = True
                        
                        # Update last active time
                        cursor.execute('UPDATE users SET last_active = %s WHERE id = %s',
                                    (datetime.now(), user['id']))
                        conn.commit()
                        
                        logger.info(f"Institution admin {username} logged in successfully")
                        flash('Login successful!', 'success')
                        return redirect(url_for('institution.institution_dashboard'))
                else:
                    flash('Invalid username or password.', 'danger')
            except Error as err:
                logger.error(f"Database error during institution login: {str(err)}")
                flash('Error during login.', 'danger')
            finally:
                cursor.close()
                conn.close()
        
        return render_template('institution_login.html')

    # Student Login
    @auth_bp.route('/student_login', methods=['GET', 'POST'])
    def student_login():
        form = InstitutionLoginForm()
        if form.validate_on_submit():
            institution_code = sanitize_input(form.institution_code.data)
            username = sanitize_input(form.username.data)
            password = form.password.data
            
            conn = get_db_connection()
            if conn is None:
                flash('Database connection error.', 'danger')
                return redirect(url_for('auth.student_login'))
            
            cursor = conn.cursor(dictionary=True)
            
            try:
                cursor.execute('''SELECT u.*, i.id as institution_id 
                    FROM users u
                    JOIN institution_students ist ON u.id = ist.user_id
                    JOIN institutions i ON ist.institution_id = i.id
                    WHERE u.username = %s AND i.institution_code = %s AND u.role = 'student' ''',
                    (username, institution_code))
                user = cursor.fetchone()
                
                if user and check_password_hash(user['password_hash'], password):
                    if user['status'] != 'active':
                        flash('Your account is not active.', 'danger')
                    else:
                        session['username'] = user['username']
                        session['user_id'] = user['id']
                        session['role'] = user['role']
                        session['institution_id'] = user['institution_id']
                        session.permanent = True
                        
                        cursor.execute('UPDATE users SET last_active = %s WHERE id = %s',
                                    (datetime.now(), user['id']))
                        conn.commit()
                        
                        logger.info(f"Student {username} logged in successfully")
                        flash('Login successful!', 'success')
                        return redirect(url_for('user.user_dashboard'))
                else:
                    flash('Invalid credentials or institution code.', 'danger')
            except Error as err:
                logger.error(f"Database error during student login: {str(err)}")
                flash('Error during login.', 'danger')
            finally:
                cursor.close()
                conn.close()
        
        return render_template('student_login.html', form=form)

    # User Registration
    @auth_bp.route('/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            username = request.form.get('username')
            email = request.form.get('email')
            password = request.form.get('password')
            
            if not username or not email or not password:
                flash('All fields are required.', 'danger')
                return redirect(url_for('auth.register'))
            
            conn = get_db_connection()
            if conn is None:
                flash('Database connection error.', 'danger')
                return redirect(url_for('auth.register'))
            
            cursor = conn.cursor(dictionary=True)
            try:
                # Check if username or email already exists
                cursor.execute('SELECT id FROM users WHERE username = %s OR email = %s', (username, email))
                if cursor.fetchone():
                    flash('Username or email already exists.', 'danger')
                    return redirect(url_for('auth.register'))
                
                # Hash password using Werkzeug's method
                password_hash = generate_password_hash(password, method='pbkdf2:sha256:600000')
                
                # Insert new user
                cursor.execute('''
                    INSERT INTO users (username, email, password_hash, role, user_type, subscription_status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                ''', (username, email, password_hash, 'individual', 'individual', 'inactive'))
                
                conn.commit()
                flash('Registration successful! Please log in.', 'success')
                return redirect(url_for('auth.login'))
                
            except Error as err:
                logger.error(f"Database error in registration: {str(err)}")
                flash('Error during registration.', 'danger')
                return redirect(url_for('auth.register'))
            finally:
                cursor.close()
                conn.close()
        
        return render_template('register.html')

    @auth_bp.route('/institution_register', methods=['GET', 'POST'])
    def institution_register():
        if request.method == 'POST':
            institution_name = request.form.get('institution_name')
            institution_email = request.form.get('institution_email')
            admin_username = request.form.get('admin_username')
            admin_email = request.form.get('admin_email')
            admin_password = request.form.get('admin_password')
            institution_code = request.form.get('institution_code', '').upper()

            if not all([institution_name, institution_email, admin_username, admin_email, admin_password, institution_code]):
                flash('All fields are required.', 'danger')
                return render_template('institution_register.html')

            conn = get_db_connection()
            if conn is None:
                flash('Database connection error.', 'danger')
                return render_template('institution_register.html')

            cursor = conn.cursor(dictionary=True)
            try:
                # Start transaction
                conn.start_transaction()

                # Check if institution code already exists
                cursor.execute('SELECT id FROM institutions WHERE institution_code = %s', (institution_code,))
                if cursor.fetchone():
                    flash('Institution code already exists.', 'danger')
                    return render_template('institution_register.html')

                # Check if institution email already exists
                cursor.execute('SELECT id FROM institutions WHERE email = %s', (institution_email,))
                if cursor.fetchone():
                    flash('Institution email already exists.', 'danger')
                    return render_template('institution_register.html')

                # Check if admin username or email already exists
                cursor.execute('SELECT id FROM users WHERE username = %s OR email = %s', 
                             (admin_username, admin_email))
                if cursor.fetchone():
                    flash('Admin username or email already exists.', 'danger')
                    return render_template('institution_register.html')

                # Create admin user first
                password_hash = generate_password_hash(admin_password)
                cursor.execute('''INSERT INTO users 
                    (username, email, password_hash, role, status) 
                    VALUES (%s, %s, %s, 'instituteadmin', 'active')''',
                    (admin_username, admin_email, password_hash))
                admin_id = cursor.lastrowid

                # Create institution with admin_id
                cursor.execute('''INSERT INTO institutions 
                    (name, email, institution_code, admin_id) 
                    VALUES (%s, %s, %s, %s)''',
                    (institution_name, institution_email, institution_code, admin_id))
                institution_id = cursor.lastrowid

                # Update admin user with institution_id in institution_students table
                cursor.execute('''INSERT INTO institution_students 
                    (institution_id, user_id) VALUES (%s, %s)''',
                    (institution_id, admin_id))

                conn.commit()
                flash('Institution registered successfully. Please login with your admin credentials.', 'success')
                return redirect(url_for('auth.login'))

            except Error as err:
                conn.rollback()
                logger.error(f"Database error during institution registration: {str(err)}")
                flash('Error during registration.', 'danger')
                return render_template('institution_register.html')
            finally:
                cursor.close()
                conn.close()

        return render_template('institution_register.html')

    # Student Registration
    @auth_bp.route('/student_register', methods=['GET', 'POST'])
    def student_register():
        form = StudentRegisterForm()
        if form.validate_on_submit():
            username = sanitize_input(form.username.data)
            email = sanitize_input(form.email.data)
            password = form.password.data
            institution_code = sanitize_input(form.institution_code.data)
            
            conn = None
            cursor = None
            
            try:
                conn = get_db_connection()
                if conn is None:
                    logger.error("Failed to get DB connection")
                    flash('Database connection error.', 'danger')
                    return redirect(url_for('auth.student_register'))
                
                # Ensure autocommit is OFF to manage transactions manually
                conn.autocommit = False
                cursor = conn.cursor(dictionary=True)
                
                cursor.execute('SELECT id FROM users WHERE username = %s OR email = %s', (username, email))
                if cursor.fetchone():
                    flash('Username or email already exists.', 'danger')
                    return redirect(url_for('auth.student_register'))
                
                cursor.execute('SELECT id, subscription_end FROM institutions WHERE institution_code = %s', (institution_code,))
                institution = cursor.fetchone()
                if not institution:
                    flash('Invalid institution code.', 'danger')
                    return redirect(url_for('auth.student_register'))
                if institution.get('subscription_end') and institution['subscription_end'] < datetime.now():
                    flash('Institution subscription has expired.', 'danger')
                    return redirect(url_for('auth.student_register'))
                
                hashed_password = generate_password_hash(password)
                
                # Insert the new student user
                cursor.execute('''INSERT INTO users 
                    (username, email, password_hash, role, user_type, status, institution_id, last_active) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
                    (username, email, hashed_password, 'student', 'institutional', 'active', institution['id'], datetime.now()))
                user_id = cursor.lastrowid
                
                # Create institution-student relation
                cursor.execute('''INSERT INTO institution_students 
                    (institution_id, user_id) 
                    VALUES (%s, %s)''',
                    (institution['id'], user_id))
                
                conn.commit()
                
                logger.info(f"New student {username} registered for institution {institution_code}")
                flash('Registration successful! Please log in.', 'success')
                return redirect(url_for('auth.student_login'))
            except Error as err:
                if conn:
                    try:
                        conn.rollback()
                    except Error as rollback_err:
                        logger.error(f"Rollback failed: {str(rollback_err)}")
                
                logger.error(f"Database error during student registration: {str(err)}")
                flash('Error during registration. Please try again.', 'danger')
            finally:
                if cursor:
                    cursor.close()
                if conn:
                    try:
                        conn.close()
                    except Error as close_err:
                        logger.error(f"Connection close failed: {str(close_err)}")
                        
        return render_template('student_register.html', form=form)

    # Logout
    @auth_bp.route('/logout')
    @login_required
    def logout():
        logger.info(f"User {session['username']} logged out")
        session.clear()
        flash('You have been logged out.', 'success')
        return redirect(url_for('auth.choose_login'))
    

    return auth_bp