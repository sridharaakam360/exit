from flask import Blueprint, render_template, redirect, url_for, flash, session, request, jsonify
from utils.db import get_db_connection
from utils.security import quiz_access_required, login_required, sanitize_input
from datetime import datetime
import json
import logging
from mysql.connector import Error
from flask import current_app

quiz_bp = Blueprint('quiz', __name__)
logger = logging.getLogger(__name__)

@quiz_bp.route('/', methods=['GET', 'POST'])
@login_required
@quiz_access_required
def quiz():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get user's role and subscription details
        cursor.execute('''
            SELECT u.role, u.institution_id,
                   i.subscription_plan_id, i.subscription_start, i.subscription_end,
                   sp.degree_access, sp.includes_previous_years
            FROM users u
            LEFT JOIN institutions i ON u.institution_id = i.id
            LEFT JOIN subscription_plans sp ON i.subscription_plan_id = sp.id
            WHERE u.id = %s
        ''', (session['user_id'],))
        user = cursor.fetchone()
        
        if not user:
            flash('User not found.', 'error')
            return redirect(url_for('user.user_dashboard'))
        
        # Get accessible exams based on user type and subscription
        if user['role'] == 'superadmin':
            # Superadmin has access to all exams
            cursor.execute('SELECT * FROM exams ORDER BY name')
            accessible_exams = cursor.fetchall()
        elif user['role'] == 'individual':
            # Individual users can access exams based on their subscription plan
            cursor.execute('''
                SELECT DISTINCT e.*
                FROM exams e
                JOIN plan_exam_access pea ON e.id = pea.exam_id
                JOIN users u ON u.subscription_plan_id = pea.plan_id
                JOIN subscription_plans sp ON u.subscription_plan_id = sp.id
                WHERE u.id = %s
                AND u.subscription_status = 'active'
                AND DATE(u.subscription_end) >= CURDATE()
                AND (
                    sp.degree_access = 'both'
                    OR e.degree_type = sp.degree_access
                )
                ORDER BY e.name
            ''', (session['user_id'],))
            accessible_exams = cursor.fetchall()
        else:  # student
            if user['institution_id']:
                # Get institution's subscription details
                cursor.execute('''
                    SELECT i.subscription_plan_id, i.subscription_start, i.subscription_end,
                           sp.degree_access, sp.includes_previous_years
                    FROM institutions i
                    JOIN subscription_plans sp ON i.subscription_plan_id = sp.id
                    WHERE i.id = %s
                ''', (user['institution_id'],))
                inst_subscription = cursor.fetchone()
                
                if not inst_subscription:
                    flash('Your institution does not have an active subscription.', 'warning')
                    return redirect(url_for('user.user_dashboard'))
                
                current_date = datetime.now().date()
                if not inst_subscription['subscription_end'] or \
                   not inst_subscription['subscription_start'] or \
                   inst_subscription['subscription_end'].date() < current_date or \
                   inst_subscription['subscription_start'].date() > current_date:
                    flash('Your institution needs an active subscription for you to access quizzes.', 'warning')
                    return redirect(url_for('user.user_dashboard'))
                
                # Get accessible exams based on institution's subscription
                cursor.execute('''
                    SELECT DISTINCT e.*
                    FROM exams e
                    JOIN plan_exam_access pea ON e.id = pea.exam_id
                    WHERE pea.plan_id = %s
                    AND (
                        %s = 'both'
                        OR e.degree_type = %s
                    )
                    ORDER BY e.name
                ''', (
                    inst_subscription['subscription_plan_id'],
                    inst_subscription['degree_access'],
                    inst_subscription['degree_access']
                ))
                accessible_exams = cursor.fetchall()
                
                if not accessible_exams:
                    flash('No exams are available in your institution\'s subscription plan.', 'warning')
                    return redirect(url_for('user.user_dashboard'))
            else:
                flash('You need to be associated with an institution to access quizzes.', 'warning')
                return redirect(url_for('user.user_dashboard'))

        # Get subjects for the selected exam if exam_id is provided
        subjects = []
        exam_id = request.args.get('exam_id', type=int)
        if exam_id:
            cursor.execute('''
                SELECT DISTINCT s.*
                FROM subjects s
                JOIN questions q ON s.id = q.subject_id
                WHERE s.exam_id = %s
                AND EXISTS (
                    SELECT 1 FROM questions q2 
                    WHERE q2.subject_id = s.id
                )
                ORDER BY s.name
            ''', (exam_id,))
            subjects = cursor.fetchall()
            
            # If no subjects found, flash a message
            if not subjects:
                flash('No subjects available for this exam.', 'info')

        # Initialize default stats
        stats = {
            'total_quizzes': 0,
            'passed_quizzes': 0,
            'failed_quizzes': 0,
            'average_score': 0
        }
        
        # Try to get user's quiz statistics if the table exists
        try:
            cursor.execute('''
                SELECT COUNT(*) as total_quizzes,
                       COUNT(CASE WHEN score >= 60 THEN 1 END) as passed_quizzes,
                       COUNT(CASE WHEN score < 60 THEN 1 END) as failed_quizzes,
                       AVG(score) as average_score
                FROM quiz_attempts
                WHERE user_id = %s
            ''', (session['user_id'],))
            quiz_stats = cursor.fetchone()
            if quiz_stats:
                stats = quiz_stats
        except Exception as e:
            print(f"Error fetching quiz stats: {str(e)}")
            # Continue with default stats
        
        # Initialize empty recent attempts
        recent_attempts = []
        
        # Try to get recent quiz attempts if the table exists
        try:
            cursor.execute('''
                SELECT qa.*, e.name as exam_name, s.name as subject_name
                FROM quiz_attempts qa
                JOIN exams e ON qa.exam_id = e.id
                JOIN subjects s ON qa.subject_id = s.id
                WHERE qa.user_id = %s
                ORDER BY qa.attempt_date DESC
                LIMIT 5
            ''', (session['user_id'],))
            recent_attempts = cursor.fetchall()
        except Exception as e:
            print(f"Error fetching recent attempts: {str(e)}")
            # Continue with empty recent attempts

        # Handle POST request for quiz generation
        if request.method == 'POST' and request.form.get('generate'):
            quiz_type = request.form.get('quiz_type')
            exam_id = request.form.get('exam_id', type=int)
            subject_id = request.form.get('subject_id', type=int)
            difficulty = request.form.get('difficulty')
            num_questions = request.form.get('num_questions', type=int, default=10)

            if not quiz_type or not exam_id:
                flash('Please select both Quiz Type and Exam.', 'error')
                return redirect(url_for('quiz.quiz'))

            if quiz_type == 'subject_wise' and not subject_id:
                flash('Please select a Subject for Subject-wise Practice.', 'error')
                return redirect(url_for('quiz.quiz'))

            # Generate quiz questions
            questions = generate_quiz_questions(
                quiz_type=quiz_type,
                exam_id=exam_id,
                subject_id=subject_id,
                difficulty=difficulty,
                num_questions=num_questions,
                accessible_exams=accessible_exams,
                cursor=cursor
            )

            if not questions:
                flash('No questions found matching your criteria.', 'error')
                return redirect(url_for('quiz.quiz'))

            # Store questions in session for quiz taking
            session['quiz_questions'] = questions
            session['current_question'] = 0
            session['quiz_start_time'] = datetime.now().isoformat()
            session['quiz_type'] = quiz_type
            session['exam_id'] = exam_id
            session['subject_id'] = subject_id

            return render_template('quiz.html',
                                 questions=questions,
                                 accessible_exams=accessible_exams,
                                 subjects=subjects,
                                 stats=stats,
                                 recent_attempts=recent_attempts,
                                 user=user)
        
        return render_template('quiz.html',
                             accessible_exams=accessible_exams,
                             subjects=subjects,
                             stats=stats,
                             recent_attempts=recent_attempts,
                             user=user)
                             
    except Exception as e:
        print(f"Error in quiz route: {str(e)}")
        flash('An error occurred while loading the quiz page.', 'error')
        return redirect(url_for('user.user_dashboard'))
    finally:
        if 'conn' in locals():
            conn.close()

def generate_quiz_questions(quiz_type, exam_id, subject_id, difficulty, num_questions, accessible_exams, cursor):
    """Helper function to generate quiz questions"""
    query = '''SELECT q.*, s.name AS subject_name, s.exam_id 
              FROM questions q 
              JOIN subjects s ON q.subject_id = s.id'''
    
    # Only add plan_exam_access check for non-superadmin users
    user_role = session.get('role')
    if user_role != 'superadmin':
        query += ' JOIN plan_exam_access pea ON s.exam_id = pea.exam_id'
    
    query += ' WHERE 1=1'
    params = []

    # Get user's subscription plan
    user_id = session.get('user_id')
    current_date = datetime.now().date()
    
    if user_role == 'individual':
        cursor.execute('''
            SELECT sp.id, sp.name, sp.includes_previous_years, sp.degree_access
            FROM users u
            JOIN subscription_plans sp ON u.subscription_plan_id = sp.id
            WHERE u.id = %s AND u.subscription_status = 'active'
            AND DATE(u.subscription_end) >= CURDATE()
        ''', (user_id,))
        subscription = cursor.fetchone()
        if not subscription:
            return []
        
        # Add subscription plan check
        query += ' AND pea.plan_id = %s'
        params.append(subscription['id'])
        
        # For Basic plan, only allow previous year questions
        if subscription['name'] == 'Basic Package':
            query += ' AND q.is_previous_year = TRUE'
    
    elif user_role == 'student':
        cursor.execute('''
            SELECT sp.id, sp.name, sp.includes_previous_years, sp.degree_access
            FROM institution_students ist
            JOIN institutions i ON ist.institution_id = i.id
            JOIN subscription_plans sp ON i.subscription_plan_id = sp.id
            WHERE ist.user_id = %s
            AND DATE(i.subscription_end) >= CURDATE()
        ''', (user_id,))
        subscription = cursor.fetchone()
        if not subscription:
            return []
        
        # Add subscription plan check for non-superadmin
        if user_role != 'superadmin':
            query += ' AND pea.plan_id = %s'
            params.append(subscription['id'])

    if quiz_type == 'previous_year' and exam_id:
        query += ' AND s.exam_id = %s AND q.is_previous_year = TRUE'
        params.append(int(exam_id))
    elif quiz_type == 'subject_wise' and subject_id:
        query += ' AND q.subject_id = %s'
        params.append(int(subject_id))
        
        # For Basic plan, only allow previous year questions
        if user_role == 'individual' and subscription and subscription['name'] == 'Basic Package':
            query += ' AND q.is_previous_year = TRUE'
    
    if difficulty:
        query += ' AND q.difficulty = %s'
        params.append(difficulty)
    
    # Only apply exam access filter for non-superadmin users
    if accessible_exams and user_role != 'superadmin':
        exam_ids = [exam['id'] for exam in accessible_exams]  # Changed from exam[0] to exam['id']
        query += f' AND s.exam_id IN ({",".join(["%s"] * len(exam_ids))})'
        params.extend(exam_ids)
    
    query += ' ORDER BY RAND() LIMIT %s'
    params.append(num_questions)
    
    cursor.execute(query, params)
    return cursor.fetchall()

def handle_quiz_submission(request, conn, cursor, user_id):
    """Handle quiz submission and scoring"""
    try:
        user_answers = {}
        exam_id = None
        
        # Extract user answers
        for key, value in request.form.items():
            if key.startswith('question_'):
                question_id = int(key.replace('question_', ''))
                user_answers[str(question_id)] = value.upper()  # Convert question_id to string for JSON
                if not exam_id:
                    cursor.execute('SELECT s.exam_id FROM questions q JOIN subjects s ON q.subject_id = s.id WHERE q.id = %s', (question_id,))
                    result = cursor.fetchone()
                    if result:
                        exam_id = result['exam_id']

        if user_answers:
            question_ids = list(map(int, user_answers.keys()))  # Convert back to int for SQL query
            placeholders = ','.join(['%s'] * len(question_ids))
            cursor.execute(f'''SELECT id, correct_answer 
                            FROM questions 
                            WHERE id IN ({placeholders})''', question_ids)
            correct_answers = {row['id']: row['correct_answer'] for row in cursor.fetchall()}
            
            score = sum(1 for qid, ans in user_answers.items() if correct_answers.get(int(qid)) == ans)
            total_questions = len(user_answers)
            time_taken = int(request.form.get('time_taken', 0))

            # Convert answers dict to JSON string
            answers_json = json.dumps(user_answers)

            cursor.execute('''INSERT INTO results 
                            (user_id, exam_id, score, total_questions, time_taken, answers, date_taken) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s)''',
                            (user_id, exam_id, score, total_questions, time_taken, answers_json, datetime.now()))
            last_row_id = cursor.lastrowid
            conn.commit()
            
            flash(f'Quiz completed! Your score: {score}/{total_questions}', 'success')
            return redirect(url_for('quiz.results', result_id=last_row_id))
        else:
            flash('No answers submitted.', 'danger')
            return redirect(url_for('quiz.quiz'))
            
    except Error as err:
        conn.rollback()
        logger.error(f"Database error in quiz submission: {str(err)}")
        flash('Error submitting quiz. Please try again.', 'danger')
        return redirect(url_for('quiz.quiz'))

@quiz_bp.route('/results')
@login_required
def results():
    result_id = request.args.get('result_id', type=int)
    if not result_id:
        flash('Result ID is required.', 'danger')
        return redirect(url_for('user.user_dashboard'))

    conn = get_db_connection()
    if conn is None:
        flash('Database connection error.', 'danger')
        return redirect(url_for('user.user_dashboard'))
    
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute('''SELECT r.*, e.name AS exam_name 
                         FROM results r LEFT JOIN exams e ON r.exam_id = e.id 
                         WHERE r.id = %s AND r.user_id = %s''', (result_id, session['user_id']))
        result = cursor.fetchone()
        if not result:
            flash('Result not found or access denied.', 'danger')
            return redirect(url_for('user.user_dashboard'))

        answers = json.loads(result['answers']) if result['answers'] else {}
        if not answers:
            return render_template('results.html', score=result['score'], total=result['total_questions'], time=result['time_taken'], detailed_results=[])

        question_ids = list(answers.keys())
        cursor.execute(f'''SELECT q.*, s.name AS subject_name 
                         FROM questions q LEFT JOIN subjects s ON q.subject_id = s.id 
                         WHERE q.id IN ({','.join(['%s'] * len(question_ids))})''', question_ids)
        questions = {q['id']: q for q in cursor.fetchall()}

        cursor.execute(f'''SELECT question_id, comment, rating 
                         FROM question_reviews 
                         WHERE question_id IN ({','.join(['%s'] * len(question_ids))}) AND user_id = %s''', 
                         question_ids + [session['user_id']])
        reviews = {r['question_id']: r for r in cursor.fetchall()}

        detailed_results = [
            {
                'question': q['question'],
                'options': {'A': q['option_a'], 'B': q['option_b'], 'C': q['option_c'], 'D': q['option_d']},
                'correct_answer': q['correct_answer'],
                'user_answer': answers[str(q['id'])].upper(),
                'explanation': q['explanation'],
                'subject': q['subject_name'],
                'question_id': q['id'],
                'already_reviewed': q['id'] in reviews,
                'review': reviews.get(q['id'])
            } for qid, q in questions.items()
        ]

        return render_template('results.html', score=result['score'], total=result['total_questions'], time=result['time_taken'], detailed_results=detailed_results, exam_name=result['exam_name'], date_taken=result['date_taken'])
    
    except Error as err:
        logger.error(f"Database error in results: {str(err)}")
        flash('Error retrieving results.', 'danger')
        return redirect(url_for('user.user_dashboard'))
    finally:
        cursor.close()
        conn.close()

@quiz_bp.route('/review_question/<int:qid>', methods=['POST'])
@login_required
def review_question(qid):
    comment = sanitize_input(request.form.get('comment', ''))
    rating = request.form.get('rating', type=int)
    
    if not rating or not (1 <= rating <= 5):
        flash('Rating must be between 1 and 5.', 'danger')
        return redirect(request.referrer or url_for('user.user_dashboard'))
    
    conn = get_db_connection()
    if conn is None:
        flash('Database connection error.', 'danger')
        return redirect(request.referrer or url_for('user.user_dashboard'))
    
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT id FROM questions WHERE id = %s', (qid,))
        if not cursor.fetchone():
            flash('Question not found.', 'danger')
            return redirect(request.referrer or url_for('user.user_dashboard'))
        
        cursor.execute('''SELECT id FROM question_reviews 
            WHERE question_id = %s AND user_id = %s''',
            (qid, session['user_id']))
        existing_review = cursor.fetchone()
        
        if existing_review:
            cursor.execute('''UPDATE question_reviews 
                SET comment = %s, rating = %s, created_at = %s
                WHERE question_id = %s AND user_id = %s''',
                (comment, rating, datetime.now(), qid, session['user_id']))
            flash('Your review has been updated.', 'success')
        else:
            cursor.execute('''INSERT INTO question_reviews 
                (question_id, user_id, comment, rating) 
                VALUES (%s, %s, %s, %s)''',
                (qid, session['user_id'], comment, rating))
            flash('Your review has been submitted.', 'success')
        
        conn.commit()
        
        current_app.socketio.emit('new_review', {
            'username': session['username'],
            'question_id': qid,
            'rating': rating
        }, namespace='/admin')
        
        logger.info(f"Question {qid} reviewed by user {session['username']}")
        
    except Error as err:
        conn.rollback()
        logger.error(f"Database error during question review: {str(err)}")
        flash('Error submitting review.', 'danger')
    finally:
        cursor.close()
        conn.close()
    
    return redirect(request.referrer or url_for('user.user_dashboard'))