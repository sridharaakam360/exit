# admin.py
from flask import Blueprint, render_template, redirect, url_for, flash, session, request, jsonify, send_file
from utils.db import get_db_connection
from utils.security import super_admin_required, institute_admin_required
from forms import QuestionForm, UserForm
from datetime import datetime
import json
import logging
from mysql.connector import Error
from werkzeug.security import generate_password_hash
import pandas as pd
import io
import csv
from werkzeug.utils import secure_filename
import os

admin_bp = Blueprint('admin', __name__)
logger = logging.getLogger(__name__)

@admin_bp.route('/dashboard')
@super_admin_required
def admin_dashboard():
    conn = get_db_connection()
    if conn is None:
        flash('Database connection error.', 'danger')
        return redirect(url_for('auth.login'))
    
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Get active users count (users with active subscription or non-individual users)
        cursor.execute('''
            SELECT COUNT(*) as active_users 
            FROM users u 
            WHERE u.role != 'individual' 
            OR (u.role = 'individual' 
                AND u.subscription_status = 'active' 
                AND u.subscription_end >= CURDATE())
        ''')
        result = cursor.fetchone()
        active_users = result['active_users'] if result else 0
        
        # Get total questions count
        cursor.execute('SELECT COUNT(*) as total_questions FROM questions')
        result = cursor.fetchone()
        total_questions = result['total_questions'] if result else 0
        
        # Get total quizzes count
        cursor.execute('SELECT COUNT(*) as total_quizzes FROM results')
        result = cursor.fetchone()
        total_quizzes = result['total_quizzes'] if result else 0

        # Get institution stats
        cursor.execute('''
            SELECT 
                COUNT(*) as total_institutions,
                COUNT(CASE WHEN subscription_plan_id IS NOT NULL THEN 1 END) as subscribed_institutions,
                COUNT(CASE WHEN subscription_end >= CURDATE() THEN 1 END) as active_subscriptions
            FROM institutions
        ''')
        institution_stats = cursor.fetchone()

        # Get student stats
        cursor.execute('''
            SELECT 
                COUNT(*) as total_students,
                COUNT(CASE WHEN status = 'active' THEN 1 END) as active_students,
                COUNT(CASE WHEN status = 'inactive' THEN 1 END) as inactive_students
            FROM users 
            WHERE role = 'student'
        ''')
        student_stats = cursor.fetchone()
        
        # Get recent activity with proper error handling
        try:
            cursor.execute('''
                SELECT u.username, r.date_taken as date, 
                       CONCAT('Score: ', r.score, '/', r.total_questions) as content,
                       'result' as type
                FROM results r
                JOIN users u ON r.user_id = u.id
                ORDER BY r.date_taken DESC
                LIMIT 5
            ''')
            recent_activity = cursor.fetchall() or []
        except Error as err:
            logger.error(f"Error fetching recent activity: {str(err)}")
            recent_activity = []
        
        return render_template('admin_dashboard.html',
                            active_users=active_users,
                            total_questions=total_questions,
                            total_quizzes=total_quizzes,
                            recent_activity=recent_activity,
                            institution_stats=institution_stats,
                            student_stats=student_stats,
                            now=datetime.now())
    except Error as err:
        logger.error(f"Database error in admin dashboard: {str(err)}")
        flash('Error retrieving dashboard data.', 'danger')
        return render_template('admin_dashboard.html',
                            active_users=0,
                            total_questions=0,
                            total_quizzes=0,
                            recent_activity=[],
                            institution_stats={'total_institutions': 0, 'subscribed_institutions': 0, 'active_subscriptions': 0},
                            student_stats={'total_students': 0, 'active_students': 0, 'inactive_students': 0},
                            now=datetime.now())
    finally:
        cursor.close()
        conn.close()

@admin_bp.route('/manage_questions', methods=['GET', 'POST'])
@super_admin_required
def manage_questions():
    conn = get_db_connection()
    if conn is None:
        flash('Database connection error.', 'danger')
        return redirect(url_for('admin.admin_dashboard'))
    
    cursor = conn.cursor(dictionary=True)
    try:
        form = QuestionForm()
        cursor.execute('SELECT id, name FROM subjects ORDER BY name')
        subjects = cursor.fetchall()
        if not subjects:
            flash('No subjects available. Please add subjects first.', 'danger')
            return redirect(url_for('admin.admin_dashboard'))
        form.subject_id.choices = [(s['id'], s['name']) for s in subjects]

        if request.method == 'POST' and 'question' in request.form and form.validate_on_submit():
            topics_json = json.dumps([t.strip() for t in form.topics.data.split(',')]) if form.topics.data else '[]'
            cursor.execute('''INSERT INTO questions 
                            (question, option_a, option_b, option_c, option_d, correct_answer, category, difficulty, subject_id, 
                            is_previous_year, previous_year, topics, explanation, created_by) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                            (form.question.data, form.option_a.data, form.option_b.data,
                             form.option_c.data, form.option_d.data, form.correct_answer.data.upper(),
                             form.category.data, form.difficulty.data, form.subject_id.data,
                             form.is_previous_year.data, form.previous_year.data if form.is_previous_year.data else None,
                             topics_json, form.explanation.data, session['user_id']))
            conn.commit()
            flash('Question added successfully.', 'success')
            return redirect(url_for('admin.manage_questions'))

        # Get filter parameters
        subject_filter = request.args.get('subject', '')
        difficulty_filter = request.args.get('difficulty', '')
        type_filter = request.args.get('type', '')
        search_query = request.args.get('search', '')
        page = request.args.get('page', 1, type=int)
        per_page = 20

        # Build the base query
        base_query = '''SELECT q.*, u.username, e.name AS exam_name, s.name AS subject_name 
                       FROM questions q 
                       LEFT JOIN users u ON q.created_by = u.id 
                       JOIN subjects s ON q.subject_id = s.id 
                       LEFT JOIN exams e ON s.exam_id = e.id 
                       WHERE 1=1'''
        query_params = []

        # Add filters
        if subject_filter:
            base_query += ' AND q.subject_id = %s'
            query_params.append(int(subject_filter))
        
        if difficulty_filter:
            base_query += ' AND q.difficulty = %s'
            query_params.append(difficulty_filter)
        
        if type_filter == 'previous_year':
            base_query += ' AND q.is_previous_year = TRUE'
        elif type_filter == 'practice':
            base_query += ' AND q.is_previous_year = FALSE'
        
        if search_query:
            base_query += ' AND (q.question LIKE %s OR q.category LIKE %s OR q.topics LIKE %s)'
            search_term = f'%{search_query}%'
            query_params.extend([search_term, search_term, search_term])

        # Get total count for pagination
        count_query = f'SELECT COUNT(*) as total FROM ({base_query}) as filtered_questions'
        cursor.execute(count_query, query_params)
        total_questions = cursor.fetchone()['total']
        total_pages = (total_questions + per_page - 1) // per_page

        # Add pagination
        base_query += ' ORDER BY q.id DESC LIMIT %s OFFSET %s'
        offset = (page - 1) * per_page
        query_params.extend([per_page, offset])

        # Execute final query
        cursor.execute(base_query, query_params)
        questions = cursor.fetchall()

        return render_template('manage_questions.html', 
                             questions=questions, 
                             subjects=subjects, 
                             form=form, 
                             page=page, 
                             total_pages=total_pages, 
                             total_questions=total_questions)
    
    except Error as err:
        conn.rollback()
        logger.error(f"Database error in manage_questions: {str(err)}")
        flash('Error processing questions.', 'danger')
        return redirect(url_for('admin.admin_dashboard'))
    finally:
        cursor.close()
        conn.close()

@admin_bp.route('/edit_question/<int:question_id>', methods=['GET', 'POST'])
@super_admin_required
def edit_question(question_id):
    conn = get_db_connection()
    if conn is None:
        flash('Database connection error.', 'danger')
        return redirect(url_for('admin.manage_questions'))
    
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute('SELECT id, name FROM subjects ORDER BY name')
        subjects = cursor.fetchall()
        form = QuestionForm()
        form.subject_id.choices = [(s['id'], s['name']) for s in subjects]

        cursor.execute('''SELECT q.*, s.name AS subject_name, s.exam_id 
                         FROM questions q JOIN subjects s ON q.subject_id = s.id 
                         WHERE q.id = %s''', (question_id,))
        question = cursor.fetchone()
        if not question:
            flash('Question not found.', 'danger')
            return redirect(url_for('admin.manage_questions'))

        if request.method == 'GET':
            form.question.data = question['question']
            form.option_a.data = question['option_a']
            form.option_b.data = question['option_b']
            form.option_c.data = question['option_c']
            form.option_d.data = question['option_d']
            form.correct_answer.data = question['correct_answer']
            form.category.data = question['category']
            form.difficulty.data = question['difficulty']
            form.subject_id.data = question['subject_id']
            form.is_previous_year.data = question['is_previous_year']
            form.previous_year.data = question['previous_year']
            form.topics.data = ', '.join(json.loads(question['topics'])) if question['topics'] else ''
            form.explanation.data = question['explanation']
        elif form.validate_on_submit():
            topics_json = json.dumps([t.strip() for t in form.topics.data.split(',')]) if form.topics.data else '[]'
            cursor.execute('''UPDATE questions 
                            SET question = %s, option_a = %s, option_b = %s, option_c = %s, option_d = %s, 
                            correct_answer = %s, category = %s, difficulty = %s, subject_id = %s, 
                            is_previous_year = %s, previous_year = %s, topics = %s, explanation = %s 
                            WHERE id = %s''',
                            (form.question.data, form.option_a.data, form.option_b.data,
                             form.option_c.data, form.option_d.data, form.correct_answer.data.upper(),
                             form.category.data, form.difficulty.data, form.subject_id.data,
                             form.is_previous_year.data, form.previous_year.data if form.is_previous_year.data else None,
                             topics_json, form.explanation.data, question_id))
            conn.commit()
            flash('Question updated successfully.', 'success')
            return redirect(url_for('admin.manage_questions'))

        return render_template('edit_question.html', form=form, question_id=question_id)
    
    except Error as err:
        conn.rollback()
        logger.error(f"Database error in edit_question: {str(err)}")
        flash('Error updating question.', 'danger')
        return redirect(url_for('admin.manage_questions'))
    finally:
        cursor.close()
        conn.close()

@admin_bp.route('/delete_question/<int:qid>', methods=['POST'])
@super_admin_required
def delete_question(qid):
    conn = get_db_connection()
    if conn is None:
        flash('Database connection error.', 'danger')
        return redirect(url_for('admin.manage_questions'))
    
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT id FROM questions WHERE id = %s', (qid,))
        if not cursor.fetchone():
            flash('Question not found.', 'danger')
        else:
            cursor.execute('DELETE FROM questions WHERE id = %s', (qid,))
            conn.commit()
            flash('Question deleted successfully.', 'success')
            logger.info(f"Question {qid} deleted by superadmin {session['username']}")
    except Error as err:
        conn.rollback()
        logger.error(f"Database error during question deletion: {str(err)}")
        flash('Error deleting question.', 'danger')
    finally:
        cursor.close()
        conn.close()
    
    return redirect(url_for('admin.manage_questions'))

@admin_bp.route('/manage_users', methods=['GET', 'POST'])
@institute_admin_required  # Changed from super_admin_required
def manage_users():
    conn = get_db_connection()
    if conn is None:
        flash('Database connection error.', 'danger')
        return redirect(url_for('admin.admin_dashboard'))
    
    cursor = conn.cursor(dictionary=True)
    try:
        user_type = request.args.get('type', 'all')
        page = request.args.get('page', 1, type=int)
        per_page = 20
        offset = (page - 1) * per_page
        is_superadmin = session['role'] == 'superadmin'

        if is_superadmin and user_type == 'all':
            cursor.execute('SELECT COUNT(*) AS total FROM users')
            total = cursor.fetchone()['total']
            cursor.execute('''SELECT role, COUNT(*) AS count, MAX(last_active) AS last_active 
                            FROM users GROUP BY role 
                            ORDER BY FIELD(role, 'superadmin', 'instituteadmin', 'student', 'individual') 
                            LIMIT %s OFFSET %s''', (per_page, offset))
            users = cursor.fetchall()
            total_pages = (total + per_page - 1) // per_page
            return render_template('manage_users.html', users=users, view_type='summary', current_type='all', page=page, total_pages=total_pages)

        elif user_type in ['individual', 'student', 'superadmin', 'instituteadmin']:
            if not is_superadmin and user_type != 'student':
                flash('You can only manage students in your institution.', 'danger')
                return redirect(url_for('institution.institution_dashboard'))
            
            if not is_superadmin:  # Institute admin viewing their students
                cursor.execute('SELECT COUNT(*) AS total FROM users WHERE role = %s AND institution_id = %s', 
                             ('student', session['institution_id']))
                total = cursor.fetchone()['total']
                cursor.execute('''SELECT u.*, COUNT(DISTINCT r.id) AS quiz_count, 
                                AVG(r.score / r.total_questions * 100) AS avg_score, 
                                MAX(r.date_taken) AS last_quiz_date, i.name AS institution_name 
                                FROM users u LEFT JOIN results r ON u.id = r.user_id 
                                LEFT JOIN institutions i ON u.institution_id = i.id 
                                WHERE u.role = %s AND u.institution_id = %s
                                GROUP BY u.id ORDER BY u.username 
                                LIMIT %s OFFSET %s''', 
                                ('student', session['institution_id'], per_page, offset))
            else:  # Superadmin viewing all users of a type
                cursor.execute(f'SELECT COUNT(*) AS total FROM users WHERE role = %s', (user_type,))
                total = cursor.fetchone()['total']
                cursor.execute(f'''SELECT u.*, COUNT(DISTINCT r.id) AS quiz_count, 
                                AVG(r.score / r.total_questions * 100) AS avg_score, 
                                MAX(r.date_taken) AS last_quiz_date, i.name AS institution_name 
                                FROM users u LEFT JOIN results r ON u.id = r.user_id 
                                LEFT JOIN institutions i ON u.institution_id = i.id 
                                WHERE u.role = %s 
                                GROUP BY u.id ORDER BY u.username 
                                LIMIT %s OFFSET %s''', (user_type, per_page, offset))
            
            users = cursor.fetchall()
            total_pages = (total + per_page - 1) // per_page
            return render_template('manage_users.html', users=users, view_type='users', current_type=user_type, page=page, total_pages=total_pages, is_superadmin=is_superadmin)

    except Error as err:
        conn.rollback()
        logger.error(f"Database error in manage_users: {str(err)}")
        flash('Error retrieving users.', 'danger')
        return redirect(url_for('admin.admin_dashboard'))
    finally:
        cursor.close()
        conn.close()

@admin_bp.route('/add_user', methods=['GET', 'POST'])
@super_admin_required
def add_user():
    form = UserForm()
    
    if request.method == 'POST' and form.validate_on_submit():
        conn = get_db_connection()
        if conn is None:
            flash('Database connection error.', 'danger')
            return redirect(url_for('admin.manage_users'))
        
        cursor = conn.cursor(dictionary=True)
        try:
            username = form.username.data
            password = form.password.data
            role = form.role.data
            status = form.status.data
            
            cursor.execute('SELECT id FROM users WHERE username = %s', (username,))
            if cursor.fetchone():
                flash('Username already exists.', 'danger')
                return render_template('edit_user.html', form=form, is_edit=False)
            
            password_hash = generate_password_hash(password) if password else None
            
            cursor.execute('''INSERT INTO users 
                (username, role, status, password_hash, created_at)
                VALUES (%s, %s, %s, %s, NOW())''',
                (username, role, status, password_hash))
            conn.commit()
            
            flash('User added successfully.', 'success')
            return redirect(url_for('admin.manage_users'))
        
        except Error as err:
            conn.rollback()
            logger.error(f"Database error in add_user: {str(err)}")
            flash('Error adding user.', 'danger')
            return render_template('edit_user.html', form=form, is_edit=False)
        finally:
            cursor.close()
            conn.close()
    
    return render_template('edit_user.html', form=form, is_edit=False)

@admin_bp.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
@institute_admin_required  # Changed from super_admin_required
def edit_user(user_id):
    conn = get_db_connection()
    if conn is None:
        flash('Database connection error.', 'danger')
        return redirect(url_for('admin.manage_users'))
    
    cursor = conn.cursor(dictionary=True)
    try:
        form = UserForm()
        is_superadmin = session['role'] == 'superadmin'
        
        # Check user exists and permissions
        if is_superadmin:
            cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))
        else:  # Institute admin can only edit their students
            cursor.execute('''SELECT u.* FROM users u 
                            JOIN institution_students ist ON u.id = ist.user_id
                            WHERE u.id = %s AND ist.institution_id = %s AND u.role = 'student' ''', 
                          (user_id, session['institution_id']))
        
        user = cursor.fetchone()
        if not user:
            flash('User not found or not authorized.', 'danger')
            return redirect(url_for('admin.manage_users'))
        
        if request.method == 'GET':
            form.username.data = user['username']
            form.email.data = user['email']
            form.role.data = user['role']
            form.user_type.data = user['user_type']
            form.status.data = user['status']
        
        elif form.validate_on_submit():
            username = form.username.data
            email = form.email.data 
            role = form.role.data if is_superadmin else 'student'  # Institute admins can't change role
            user_type = form.user_type.data if is_superadmin else 'institutional'
            status = form.status.data
            password = form.password.data
            
            cursor.execute('SELECT id FROM users WHERE (username = %s OR email = %s) AND id != %s', 
                          (username, email, user_id))
            existing_user = cursor.fetchone()
            if existing_user:
                flash('Username or email already exists for another user.', 'danger')
                return render_template('edit_user.html', form=form, user_id=user_id, is_edit=True)
            
            try:
                conn.autocommit = False
                if password:
                    password_hash = generate_password_hash(password)
                    cursor.execute('''UPDATE users 
                        SET username = %s, email = %s, role = %s, user_type = %s, status = %s, password_hash = %s
                        WHERE id = %s''',
                        (username, email, role, user_type, status, password_hash, user_id))
                else:
                    cursor.execute('''UPDATE users 
                        SET username = %s, email = %s, role = %s, user_type = %s, status = %s
                        WHERE id = %s''',
                        (username, email, role, user_type, status, user_id))
                
                conn.commit()
                flash('User updated successfully.', 'success')
                return redirect(url_for('admin.manage_users', type='student' if not is_superadmin else None))
            except Error as err:
                conn.rollback()
                logger.error(f"Database error during update: {str(err)}")
                flash(f'Error updating user: {str(err)}', 'danger')
        
        return render_template('edit_user.html', form=form, user_id=user_id, is_edit=True, is_superadmin=is_superadmin)
    
    except Error as err:
        logger.error(f"Database error in edit_user: {str(err)}")
        flash('Error updating user.', 'danger')
        return redirect(url_for('admin.manage_users'))
    finally:
        cursor.close()
        conn.close()

@admin_bp.route('/delete_admin_user/<int:user_id>', methods=['POST'])
@institute_admin_required  # Changed from super_admin_required
def delete_admin_user(user_id):
    if user_id == session['user_id']:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('admin.manage_users'))
    
    conn = None
    cursor = None
    
    try:
        conn = get_db_connection()
        if conn is None:
            flash('Database connection error.', 'danger')
            return redirect(url_for('admin.manage_users'))
        
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)
        
        is_superadmin = session['role'] == 'superadmin'
        
        if is_superadmin:
            cursor.execute('SELECT role FROM users WHERE id = %s', (user_id,))
        else:
            cursor.execute('''SELECT u.role FROM users u 
                            JOIN institution_students ist ON u.id = ist.user_id
                            WHERE u.id = %s AND ist.institution_id = %s AND u.role = 'student' ''', 
                          (user_id, session['institution_id']))
        
        user = cursor.fetchone()
        if not user:
            flash('User not found or not authorized.', 'danger')
            return redirect(url_for('admin.manage_users'))

        if is_superadmin and user['role'] == 'superadmin':
            cursor.execute('SELECT COUNT(*) AS count FROM users WHERE role = "superadmin"')
            if cursor.fetchone()['count'] <= 1:
                flash('Cannot delete the last superadmin.', 'danger')
                return redirect(url_for('admin.manage_users'))

        cursor.execute('DELETE FROM question_reviews WHERE user_id = %s', (user_id,))
        cursor.execute('DELETE FROM results WHERE user_id = %s', (user_id,))
        cursor.execute('DELETE FROM subscription_history WHERE user_id = %s', (user_id,))
        cursor.execute('DELETE FROM institution_students WHERE user_id = %s', (user_id,))
        if is_superadmin:
            cursor.execute('UPDATE institutions SET admin_id = NULL WHERE admin_id = %s', (user_id,))
        cursor.execute('DELETE FROM users WHERE id = %s', (user_id,))
        
        conn.commit()
        flash('User deleted successfully.', 'success')
    
    except Error as err:
        if conn:
            conn.rollback()
        logger.error(f"Database error in delete_user: {str(err)}")
        flash('Error deleting user.', 'danger')
    
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    
    return redirect(url_for('admin.manage_users', type='student' if session['role'] == 'instituteadmin' else None))

@admin_bp.route('/download_template/<format>')
@super_admin_required
def download_template(format):
    try:
        # Define template data
        template_data = {
            'Question': ['Sample question 1?', 'Sample question 2?'],
            'Option A': ['Option A1', 'Option A2'],
            'Option B': ['Option B1', 'Option B2'],
            'Option C': ['Option C1', 'Option C2'],
            'Option D': ['Option D1', 'Option D2'],
            'Correct Answer': ['A', 'B'],
            'Category': ['Category1', 'Category2'],
            'Difficulty': ['easy', 'medium'],
            'Subject ID': ['1', '2'],
            'Is Previous Year': ['FALSE', 'TRUE'],
            'Previous Year': ['', '2022'],
            'Topics': ['topic1,topic2', 'topic3,topic4'],
            'Explanation': ['Explanation for question 1', 'Explanation for question 2']
        }

        if format == 'csv':
            # Create CSV in memory
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=template_data.keys())
            writer.writeheader()
            
            # Write sample rows
            for i in range(len(template_data['Question'])):
                row = {k: template_data[k][i] for k in template_data.keys()}
                writer.writerow(row)
            
            # Prepare the CSV file for download
            output.seek(0)
            return send_file(
                io.BytesIO(output.getvalue().encode('utf-8')),
                mimetype='text/csv',
                as_attachment=True,
                download_name='question_template.csv'
            )
            
        elif format == 'excel':
            # Create Excel file in memory
            output = io.BytesIO()
            df = pd.DataFrame(template_data)
            df.to_excel(output, index=False, engine='openpyxl')
            output.seek(0)
            
            return send_file(
                output,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name='question_template.xlsx'
            )
        
        else:
            flash('Invalid format specified.', 'danger')
            return redirect(url_for('admin.manage_questions'))
            
    except Exception as e:
        logger.error(f"Error generating template: {str(e)}")
        flash('Error generating template.', 'danger')
        return redirect(url_for('admin.manage_questions'))

@admin_bp.route('/upload_questions', methods=['POST'])
@super_admin_required
def upload_questions():
    if 'question_file' not in request.files:
        flash('No file uploaded.', 'danger')
        return redirect(url_for('admin.manage_questions'))
    
    file = request.files['question_file']
    if file.filename == '':
        flash('No file selected.', 'danger')
        return redirect(url_for('admin.manage_questions'))
    
    if not file.filename.endswith(('.csv', '.xlsx', '.xls')):
        flash('Invalid file format. Please upload a CSV or Excel file.', 'danger')
        return redirect(url_for('admin.manage_questions'))
    
    try:
        # Read the file
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
        
        # Skip first row if checkbox is checked
        if request.form.get('skip_first_row') == 'on':
            df = df.iloc[1:]
        
        # Get default subject ID
        default_subject_id = request.form.get('subject_id_bulk')
        
        # Validate required columns
        required_columns = ['Question', 'Option A', 'Option B', 'Option C', 'Option D', 'Correct Answer', 'Explanation']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            flash(f'Missing required columns: {", ".join(missing_columns)}', 'danger')
            return redirect(url_for('admin.manage_questions'))
        
        conn = get_db_connection()
        if conn is None:
            flash('Database connection error.', 'danger')
            return redirect(url_for('admin.manage_questions'))
        
        cursor = conn.cursor()
        success_count = 0
        error_count = 0
        
        try:
            for index, row in df.iterrows():
                try:
                    # Process topics
                    topics = row.get('Topics', '').split(',') if pd.notna(row.get('Topics')) else []
                    topics_json = json.dumps([t.strip() for t in topics])
                    
                    # Get subject ID
                    subject_id = row.get('Subject ID', default_subject_id)
                    if not subject_id:
                        raise ValueError('Subject ID is required')
                    
                    # Validate correct answer
                    correct_answer = str(row['Correct Answer']).strip().upper()
                    if correct_answer not in ['A', 'B', 'C', 'D']:
                        raise ValueError('Correct Answer must be A, B, C, or D')
                    
                    # Handle boolean fields
                    is_previous_year = str(row.get('Is Previous Year', '')).upper() == 'TRUE'
                    previous_year = row.get('Previous Year') if is_previous_year and pd.notna(row.get('Previous Year')) else None
                    
                    # Insert question
                    cursor.execute('''INSERT INTO questions 
                        (question, option_a, option_b, option_c, option_d, correct_answer, 
                         category, difficulty, subject_id, is_previous_year, previous_year, 
                         topics, explanation, created_by) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                        (str(row['Question']), str(row['Option A']), str(row['Option B']),
                         str(row['Option C']), str(row['Option D']), correct_answer,
                         row.get('Category', ''), row.get('Difficulty', 'medium'),
                         subject_id, is_previous_year, previous_year,
                         topics_json, str(row['Explanation']), session['user_id']))
                    success_count += 1
                    
                except (ValueError, KeyError) as e:
                    error_count += 1
                    logger.error(f"Error processing row {index + 1}: {str(e)}")
                    continue
            
            conn.commit()
            flash(f'Successfully imported {success_count} questions. {error_count} questions failed.', 
                  'success' if error_count == 0 else 'warning')
            
        except Error as err:
            conn.rollback()
            logger.error(f"Database error during bulk upload: {str(err)}")
            flash('Error uploading questions.', 'danger')
            
        finally:
            cursor.close()
            conn.close()
            
    except Exception as e:
        logger.error(f"Error processing file: {str(e)}")
        flash('Error processing file.', 'danger')
        
    return redirect(url_for('admin.manage_questions'))