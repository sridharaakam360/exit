from flask import Blueprint, render_template, redirect, url_for, flash, session, request
from flask_login import login_user, logout_user, login_required, current_user
from forms import LoginTypeForm, LoginForm, InstitutionLoginForm, RegisterForm, InstitutionRegisterForm, StudentRegisterForm
from utils.db import get_db_connection
from utils.security import sanitize_input
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import logging
import random
import string
from mysql.connector import Error
from models import User

logger = logging.getLogger(__name__)

def create_auth_bp(limiter):
    auth_bp = Blueprint('auth', __name__)
    
    @auth_bp.route('/choose_login', methods=['GET', 'POST'])
    def choose_login():
        if current_user.is_authenticated:
            if current_user.role == 'superadmin':
                return redirect(url_for('admin.admin_dashboard'))
            elif current_user.role == 'instituteadmin':
                return redirect(url_for('institution.institution_dashboard'))
            else:
                return redirect(url_for('user.user_dashboard'))
            
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
        return render_template('auth/choose_login.html', form=form)

    @auth_bp.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            if current_user.role == 'superadmin':
                return redirect(url_for('admin.admin_dashboard'))
            elif current_user.role == 'instituteadmin':
                return redirect(url_for('institution.institution_dashboard'))
            else:
                return redirect(url_for('user.user_dashboard'))
            
        if request.method == 'POST':
            username = request.form.get('username')
            password = request.form.get('password')
            
            if not username or not password:
                flash('Please enter both username and password', 'error')
                return redirect(url_for('auth.login'))
            
            conn = get_db_connection()
            if not conn:
                flash('Database connection error', 'error')
                return redirect(url_for('auth.login'))
            
            try:
                cursor = conn.cursor(dictionary=True)
                cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
                user_data = cursor.fetchone()
                
                if user_data:
                    # Create a dictionary without any extra fields for User object
                    user_dict = {
                        'id': user_data['id'],
                        'username': user_data['username'],
                        'email': user_data['email'],
                        'password_hash': user_data['password_hash'],
                        'role': user_data['role'],
                        'status': user_data['status']
                    }
                    user = User(**user_dict)
                    
                    if user.check_password(password):
                        if user.status != 'active':
                            flash('Your account is not active.', 'danger')
                        else:
                            login_user(user)
                            # Set session variables
                            session['user_id'] = user.id
                            session['username'] = user.username
                            session['role'] = user.role
                            
                            flash('Login successful!', 'success')
                            
                            if user.role == 'superadmin':
                                return redirect(url_for('admin.admin_dashboard'))
                            elif user.role == 'instituteadmin':
                                return redirect(url_for('institution.institution_dashboard'))
                            else:
                                return redirect(url_for('user.user_dashboard'))
                    else:
                        flash('Invalid password.', 'danger')
                else:
                    flash('Invalid username.', 'danger')
                
            except Exception as e:
                logger.error(f"Login error: {str(e)}")
                flash('An error occurred during login', 'error')
                return redirect(url_for('auth.login'))
            finally:
                if 'cursor' in locals():
                    cursor.close()
                if conn:
                    conn.close()
        
        return render_template('auth/login.html')

    @auth_bp.route('/institution_login', methods=['GET', 'POST'])
    def institution_login():
        if current_user.is_authenticated:
            if current_user.role == 'superadmin':
                return redirect(url_for('admin.admin_dashboard'))
            elif current_user.role == 'instituteadmin':
                return redirect(url_for('institution.institution_dashboard'))
            else:
                return redirect(url_for('user.user_dashboard'))
            
        if request.method == 'POST':
            institution_code = request.form.get('institution_code')
            username = request.form.get('username')
            password = request.form.get('password')
            
            if not all([institution_code, username, password]):
                flash('All fields are required.', 'danger')
                return render_template('auth/institution_login.html')
            
            conn = get_db_connection()
            if conn is None:
                logger.error("Failed to get DB connection")
                flash('Database connection error.', 'danger')
                return render_template('auth/institution_login.html')
            
            cursor = conn.cursor(dictionary=True)
            
            try:
                cursor.execute('''
                    SELECT u.*, i.id as institution_id, i.name as institution_name 
                    FROM users u
                    JOIN institutions i ON u.id = i.admin_id
                    WHERE u.username = %s AND i.institution_code = %s AND u.role = 'instituteadmin'
                ''', (username, institution_code))
                user_data = cursor.fetchone()
                
                if user_data:
                    # Make sure institution_id is included in the User object
                    institution_id = user_data['institution_id']
                    institution_name = user_data['institution_name']
                    
                    # Create User object properly including institution_id
                    user = User(
                        id=user_data['id'],
                        username=user_data['username'],
                        email=user_data['email'],
                        password_hash=user_data['password_hash'],
                        role=user_data['role'],
                        status=user_data['status'],
                        institution_id=institution_id
                    )
                    
                    if user.check_password(password):
                        if user.status != 'active':
                            flash('Your account is not active.', 'danger')
                        else:
                            login_user(user)
                            # Set all necessary session variables
                            session['user_id'] = user.id
                            session['username'] = user.username
                            session['role'] = user.role
                            session['institution_id'] = institution_id
                            session['institution_name'] = institution_name
                            
                            # Add logging to debug
                            logger.info(f"Institution login successful for {username}, institution_id: {institution_id}")
                            logger.debug(f"User object: {vars(user)}")
                            
                            flash('Login successful!', 'success')
                            return redirect(url_for('institution.institution_dashboard'))
                    else:
                        flash('Invalid password.', 'danger')
                else:
                    flash('Invalid institution code or username.', 'danger')
            except Error as err:
                logger.error(f"Database error during institution login: {str(err)}")
                flash('Error during login.', 'danger')
            finally:
                cursor.close()
                conn.close()
        
        return render_template('auth/institution_login.html')

    @auth_bp.route('/student_login', methods=['GET', 'POST'])
    def student_login():
        if current_user.is_authenticated:
            if current_user.role == 'superadmin':
                return redirect(url_for('admin.admin_dashboard'))
            elif current_user.role == 'instituteadmin':
                return redirect(url_for('institution.institution_dashboard'))
            else:
                return redirect(url_for('user.user_dashboard'))
            
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
                user_data = cursor.fetchone()
                
                if user_data:
                    user = User(**user_data)
                    if user.check_password(password):
                        if user.status != 'active':
                            flash('Your account is not active.', 'danger')
                        else:
                            login_user(user)
                            # Set session variables
                            session['user_id'] = user.id
                            session['username'] = user.username
                            session['role'] = user.role
                            session['institution_id'] = user.institution_id
                            
                            flash('Login successful!', 'success')
                            return redirect(url_for('user.user_dashboard'))
                    else:
                        flash('Invalid password.', 'danger')
                else:
                    flash('Invalid institution code or username.', 'danger')
            except Error as err:
                logger.error(f"Database error during student login: {str(err)}")
                flash('Error during login.', 'danger')
            finally:
                cursor.close()
                conn.close()
        
        return render_template('auth/student_login.html', form=form)

    @auth_bp.route('/logout')
    @login_required
    def logout():
        logout_user()
        session.clear()
        flash('You have been logged out.', 'success')
        return redirect(url_for('auth.choose_login'))

    @auth_bp.route('/register', methods=['GET', 'POST'])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for('index'))
            
        form = RegisterForm()
        if form.validate_on_submit():
            username = sanitize_input(form.username.data)
            email = sanitize_input(form.email.data)
            password = form.password.data
            
            conn = get_db_connection()
            if conn is None:
                flash('Database connection error.', 'danger')
                return redirect(url_for('auth.register'))
            
            cursor = conn.cursor(dictionary=True)
            
            try:
                # Check if username or email already exists
                cursor.execute('SELECT id FROM users WHERE username = %s OR email = %s', 
                             (username, email))
                if cursor.fetchone():
                    flash('Username or email already exists.', 'danger')
                    return redirect(url_for('auth.register'))
                
                # Create new user
                password_hash = generate_password_hash(password)
                cursor.execute('''
                    INSERT INTO users (username, email, password_hash, role, status)
                    VALUES (%s, %s, %s, 'individual', 'active')
                ''', (username, email, password_hash))
                
                conn.commit()
                flash('Registration successful! Please login.', 'success')
                return redirect(url_for('auth.login'))
                
            except Error as err:
                logger.error(f"Database error during registration: {str(err)}")
                flash('Error during registration.', 'danger')
            finally:
                cursor.close()
                conn.close()
        
        return render_template('auth/register.html', form=form)

    @auth_bp.route('/institution_register', methods=['GET', 'POST'])
    def institution_register():
        if current_user.is_authenticated:
            return redirect(url_for('index'))
            
        form = InstitutionRegisterForm()
        if form.validate_on_submit():
            institution_name = sanitize_input(form.institution_name.data)
            institution_code = sanitize_input(form.institution_code.data)
            username = sanitize_input(form.username.data)
            email = sanitize_input(form.email.data)
            password = form.password.data
            
            conn = get_db_connection()
            if conn is None:
                flash('Database connection error.', 'danger')
                return redirect(url_for('auth.institution_register'))
            
            cursor = conn.cursor(dictionary=True)
            
            try:
                # Check if institution code already exists
                cursor.execute('SELECT id FROM institutions WHERE institution_code = %s', 
                             (institution_code,))
                if cursor.fetchone():
                    flash('Institution code already exists.', 'danger')
                    return redirect(url_for('auth.institution_register'))
                
                # Check if username or email already exists
                cursor.execute('SELECT id FROM users WHERE username = %s OR email = %s', 
                             (username, email))
                if cursor.fetchone():
                    flash('Username or email already exists.', 'danger')
                    return redirect(url_for('auth.institution_register'))
                
                # Create new user
                password_hash = generate_password_hash(password)
                cursor.execute('''
                    INSERT INTO users (username, email, password_hash, role, status)
                    VALUES (%s, %s, %s, 'instituteadmin', 'active')
                ''', (username, email, password_hash))
                user_id = cursor.lastrowid
                
                # Create institution
                cursor.execute('''
                    INSERT INTO institutions (name, institution_code, admin_id, email)
                    VALUES (%s, %s, %s, %s)
                ''', (institution_name, institution_code, user_id, email))
                
                conn.commit()
                flash('Registration successful! Please login.', 'success')
                return redirect(url_for('auth.institution_login'))
                
            except Error as err:
                logger.error(f"Database error during institution registration: {str(err)}")
                flash('Error during registration.', 'danger')
            finally:
                cursor.close()
                conn.close()
        
        return render_template('auth/institution_register.html', form=form)

    @auth_bp.route('/student_register', methods=['GET', 'POST'])
    def student_register():
        if current_user.is_authenticated:
            return redirect(url_for('index'))
            
        form = StudentRegisterForm()
        if form.validate_on_submit():
            username = sanitize_input(form.username.data)
            email = sanitize_input(form.email.data)
            password = form.password.data
            institution_code = sanitize_input(form.institution_code.data)
            
            conn = get_db_connection()
            if conn is None:
                flash('Database connection error.', 'danger')
                return redirect(url_for('auth.student_register'))
            
            cursor = conn.cursor(dictionary=True)
            
            try:
                # Check if institution exists
                cursor.execute('SELECT id FROM institutions WHERE institution_code = %s', 
                             (institution_code,))
                institution = cursor.fetchone()
                if not institution:
                    flash('Invalid institution code.', 'danger')
                    return redirect(url_for('auth.student_register'))
                
                # Check if username or email already exists
                cursor.execute('SELECT id FROM users WHERE username = %s OR email = %s', 
                             (username, email))
                if cursor.fetchone():
                    flash('Username or email already exists.', 'danger')
                    return redirect(url_for('auth.student_register'))
                
                # Create new user
                password_hash = generate_password_hash(password)
                cursor.execute('''
                    INSERT INTO users (username, email, password_hash, role, status)
                    VALUES (%s, %s, %s, 'student', 'active')
                ''', (username, email, password_hash))
                user_id = cursor.lastrowid
                
                # Link student to institution
                cursor.execute('''
                    INSERT INTO institution_students (institution_id, user_id)
                    VALUES (%s, %s)
                ''', (institution['id'], user_id))
                
                conn.commit()
                flash('Registration successful! Please login.', 'success')
                return redirect(url_for('auth.student_login'))
                
            except Error as err:
                logger.error(f"Database error during student registration: {str(err)}")
                flash('Error during registration.', 'danger')
            finally:
                cursor.close()
                conn.close()
        
        return render_template('auth/student_register.html', form=form)

    return auth_bp