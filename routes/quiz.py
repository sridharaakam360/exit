from flask import Blueprint, render_template, redirect, url_for, flash, session, request, jsonify
from utils.db import get_db_connection
from utils.security import login_required, quiz_access_required, sanitize_input
from datetime import datetime, timedelta, timezone
import json
import logging
from mysql.connector import Error
from flask import current_app
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from flask_wtf.csrf import CSRFProtect
import time
from flask_login import current_user

quiz_bp = Blueprint('quiz', __name__)
logger = logging.getLogger(__name__)

csrf = CSRFProtect()

@dataclass
class QuizSession:
    questions: List[Dict]
    start_time: datetime
    answers: Dict[str, str]
    quiz_type: str
    exam_id: int
    subject_id: Optional[int]
    time_limit: int = 60  # minutes

    @property
    def is_time_expired(self) -> bool:
        current_time = datetime.now(timezone.utc)
        start_time_utc = self.start_time.replace(tzinfo=timezone.utc)
        elapsed_time = current_time - start_time_utc
        return elapsed_time > timedelta(minutes=self.time_limit)

    @property
    def remaining_time(self) -> int:
        """Returns remaining time in seconds"""
        current_time = datetime.now(timezone.utc)
        start_time_utc = self.start_time.replace(tzinfo=timezone.utc)
        elapsed = current_time - start_time_utc
        remaining = timedelta(minutes=self.time_limit) - elapsed
        return max(0, int(remaining.total_seconds()))

    def is_complete(self) -> bool:
        return len(self.answers) == len(self.questions)

@quiz_bp.route('/', methods=['GET', 'POST'])
@login_required
@quiz_access_required
def quiz():
    conn = get_db_connection()
    if conn is None:
        flash('Database connection error.', 'danger')
        return redirect(url_for('main.index'))
    
    cursor = conn.cursor(dictionary=True)
    try:
        # Get selected degree from request, default to empty string (All Degrees)
        selected_degree = request.args.get('degree', '')
        
        # Check user role and subscription for degree access control
        accessible_degrees = []
        
        # Superadmin has access to all degrees
        if current_user.role == 'superadmin':
            accessible_degrees = ['dpharm', 'bpharm']
        # For institution admin, check their subscription plan's degree access
        elif current_user.role == 'instituteadmin':
            cursor.execute('''
                SELECT sp.degree_access
                FROM institutions i
                JOIN subscription_plans sp ON i.subscription_plan_id = sp.id
                WHERE i.id = %s AND i.subscription_end >= CURDATE()
            ''', (session['institution_id'],))
            subscription = cursor.fetchone()
            
            if subscription and subscription['degree_access']:
                if subscription['degree_access'] == 'both':
                    accessible_degrees = ['dpharm', 'bpharm']
                else:
                    accessible_degrees = [subscription['degree_access'].lower()]
            else:
                flash('Your institution doesn\'t have an active subscription or access to any degrees.', 'warning')
                return redirect(url_for('institution.institution_dashboard'))
                
        # For students, check their institution's subscription
        elif current_user.role == 'student':
            cursor.execute('''
                SELECT sp.degree_access
                FROM institutions i
                JOIN institution_students ist ON i.id = ist.institution_id
                JOIN subscription_plans sp ON i.subscription_plan_id = sp.id
                WHERE ist.user_id = %s AND i.subscription_end >= CURDATE()
            ''', (current_user.id,))
            subscription = cursor.fetchone()
            
            if subscription and subscription['degree_access']:
                if subscription['degree_access'] == 'both':
                    accessible_degrees = ['dpharm', 'bpharm']
                else:
                    accessible_degrees = [subscription['degree_access'].lower()]
            else:
                flash('Your institution doesn\'t have an active subscription or access to any degrees.', 'warning')
                return redirect(url_for('user.user_dashboard'))
                
        # For individual users, check their subscription
        else:
            cursor.execute('''
                SELECT sp.degree_access
                FROM users u
                JOIN subscription_plans sp ON u.subscription_plan_id = sp.id
                WHERE u.id = %s AND u.subscription_end >= CURDATE() AND u.subscription_status = 'active'
            ''', (current_user.id,))
            subscription = cursor.fetchone()
            
            if subscription and subscription['degree_access']:
                if subscription['degree_access'] == 'both':
                    accessible_degrees = ['dpharm', 'bpharm']
                else:
                    accessible_degrees = [subscription['degree_access'].lower()]
            else:
                flash('You don\'t have an active subscription with access to any degrees.', 'warning')
                return redirect(url_for('user.subscriptions'))
        
        # Validate that selected degree is accessible to this user
        if selected_degree and selected_degree.lower() not in [d.lower() for d in accessible_degrees]:
            flash(f'You don\'t have access to {selected_degree} content.', 'warning')
            selected_degree = ''  # Reset to show all accessible degrees
        
        # Get subjects based on accessible degrees and selected degree
        if selected_degree:
            cursor.execute('SELECT id, name, degree_type FROM subjects WHERE degree_type = %s ORDER BY name', (selected_degree,))
        else:
            degree_params = ', '.join(['%s'] * len(accessible_degrees))
            cursor.execute(f'SELECT id, name, degree_type FROM subjects WHERE degree_type IN ({degree_params}) ORDER BY name', accessible_degrees)
        
        subjects = cursor.fetchall()
        
        # Get unique chapters based on degree filter and accessible subjects
        if subjects:
            subject_ids = [str(s['id']) for s in subjects]
            subject_ids_str = ', '.join(subject_ids)
            
            if selected_degree:
                cursor.execute(f'''
                    SELECT DISTINCT chapter 
                    FROM questions 
                    WHERE subject_id IN (SELECT id FROM subjects WHERE degree_type = %s) 
                    AND chapter IS NOT NULL AND chapter != ''
                    ORDER BY chapter
                ''', (selected_degree,))
            else:
                cursor.execute(f'''
                    SELECT DISTINCT chapter 
                    FROM questions 
                    WHERE subject_id IN ({subject_ids_str})
                    AND chapter IS NOT NULL AND chapter != ''
                    ORDER BY chapter
                ''')
            
            chapters = [row['chapter'] for row in cursor.fetchall()]
        else:
            chapters = []
        
        if request.method == 'POST':
            form = request.form
            subject_id = form.get('subject')
            chapter = form.get('chapter')
            difficulty = form.get('difficulty')
            question_type = form.get('type')
            
            # Verify subject is accessible to this user
            if subject_id:
                cursor.execute('''
                    SELECT s.degree_type 
                    FROM subjects s 
                    WHERE s.id = %s
                ''', (subject_id,))
                subject = cursor.fetchone()
                
                if not subject or subject['degree_type'].lower() not in [d.lower() for d in accessible_degrees]:
                    flash('You don\'t have access to this subject.', 'danger')
                    return redirect(url_for('quiz.quiz'))
            
            # Build query based on filters
            query = '''
                SELECT q.*, s.name as subject_name, s.degree_type
                FROM questions q 
                JOIN subjects s ON q.subject_id = s.id 
                WHERE 1=1
            '''
            params = []
            
            # Add subject filter if provided
            if subject_id:
                query += ' AND q.subject_id = %s'
                params.append(subject_id)
            # Add degree filter if provided but no subject
            elif selected_degree:
                query += ' AND LOWER(s.degree_type) = LOWER(%s)'
                params.append(selected_degree)
            # Otherwise limit to accessible degrees
            else:
                if accessible_degrees:
                    query += ' AND ('
                    degree_conditions = []
                    for _ in accessible_degrees:
                        degree_conditions.append('LOWER(s.degree_type) = LOWER(%s)')
                    query += ' OR '.join(degree_conditions) + ')'
                    params.extend(accessible_degrees)
                else:
                    # Failsafe - if no accessible degrees, return no results
                    query += ' AND 1=0'
            
            # Add chapter filter if provided
            if chapter:
                query += ' AND q.chapter = %s'
                params.append(chapter)
            
            # Add difficulty filter if provided
            if difficulty:
                query += ' AND q.difficulty = %s'
                params.append(difficulty)
            
            # Add question type filter if provided
            if question_type == 'previous_year':
                query += ' AND q.is_previous_year = TRUE'
            elif question_type == 'practice':
                query += ' AND q.is_previous_year = FALSE'
            
            query += ' ORDER BY RAND() LIMIT 10'
            
            cursor.execute(query, params)
            questions = cursor.fetchall()
            
            if not questions:
                flash('No questions found matching your criteria.', 'danger')
                return redirect(url_for('quiz.quiz', degree=selected_degree))
            
            # Initialize quiz session
            session['quiz_questions'] = questions
            session['current_question'] = 1
            session['total_questions'] = len(questions)
            session['answers'] = {}
            session['quiz_start_time'] = int(time.time())
            
            # Redirect to take_quiz to display the first question
            return redirect(url_for('quiz.take_quiz'))
        
        return render_template('quiz/quiz.html', 
                             subjects=subjects, 
                             chapters=chapters,
                             selected_degree=selected_degree,
                             accessible_degrees=accessible_degrees)
    
    except Error as err:
        logger.error(f"Database error in quiz: {str(err)}")
        flash('Error loading quiz settings.', 'danger')
        return redirect(url_for('main.index'))
    finally:
        cursor.close()
        conn.close()

@quiz_bp.route('/get-subject-filters')
@login_required
def get_subject_filters():
    """Get chapters and topics for a subject"""
    try:
        subject_id = request.args.get('subject_id')
        
        if not subject_id:
            return jsonify({'error': 'Subject ID is required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        try:
            # Get chapters for the subject
            cursor.execute('''
                SELECT DISTINCT chapter 
                FROM questions 
                WHERE subject_id = %s 
                AND chapter IS NOT NULL 
                AND chapter != '' 
                ORDER BY chapter
            ''', (subject_id,))
            
            chapters = [row['chapter'] for row in cursor.fetchall()]
            
            # Get the total number of questions
            cursor.execute('''
                SELECT COUNT(*) as count 
                FROM questions 
                WHERE subject_id = %s
            ''', (subject_id,))
            
            question_count = cursor.fetchone()['count']
            
            response = {
                'chapters': chapters,
                'question_count': question_count
            }
            
            return jsonify(response)

        finally:
            cursor.close()
            conn.close()

    except Exception as e:
        logger.error(f"Error in get_subject_filters: {str(e)}", exc_info=True)
        return jsonify({'error': 'Failed to fetch filters'}), 500

@quiz_bp.route('/get-question-count', methods=['POST'])
@login_required
def get_question_count():
    """Get available question count based on selected filters"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        subject_id = request.form.get('subject_id')
        chapter = request.form.get('chapter')
        difficulty = request.form.get('difficulty')
        question_type = request.form.get('type')
        
        if not subject_id:
            return jsonify({'count': 0})
        
        # Build query based on filters
        query = '''
            SELECT COUNT(*) as count
            FROM questions q 
            JOIN subjects s ON q.subject_id = s.id 
            WHERE q.subject_id = %s
        '''
        params = [subject_id]
        
        if chapter:
            query += ' AND q.chapter = %s'
            params.append(chapter)
        
        if difficulty:
            query += ' AND q.difficulty = %s'
            params.append(difficulty)
        
        if question_type == 'previous_year':
            query += ' AND q.is_previous_year = TRUE'
        elif question_type == 'practice':
            query += ' AND q.is_previous_year = FALSE'
        
        cursor.execute(query, params)
        result = cursor.fetchone()
        
        return jsonify({'count': result['count'] if result else 0})
    
    except Exception as e:
        logger.error(f"Error in get_question_count: {str(e)}")
        return jsonify({'error': str(e), 'count': 0})
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

@quiz_bp.route('/take_quiz', methods=['GET', 'POST'])
@login_required
def take_quiz():
    # Check if quiz session exists
    if 'quiz_questions' not in session or 'current_question' not in session:
        flash('No active quiz session.', 'warning')
        return redirect(url_for('quiz.quiz'))
    
    questions = session['quiz_questions']
    current_question = session.get('current_question', 1)
    total_questions = session.get('total_questions', len(questions))
    
    # Ensure current_question is within bounds
    if current_question < 1 or current_question > total_questions:
        flash('Invalid question number.', 'danger')
        return redirect(url_for('quiz.quiz'))
    
    # Get the current question (0-indexed in the list)
    question = questions[current_question - 1]
    
    # Handle form submission
    if request.method == 'POST':
        # Cancel quiz
        if request.form.get('cancel_quiz'):
            session.pop('quiz_questions', None)
            session.pop('current_question', None)
            session.pop('total_questions', None)
            session.pop('answers', None)
            session.pop('quiz_start_time', None)
            flash('Quiz cancelled.', 'info')
            return redirect(url_for('quiz.quiz'))
        
        # Get answer
        answer = request.form.get('answer')
        time_taken = request.form.get('time_taken', 0)
        
        # Store answer in session
        answers = session.get('answers', {})
        answers[str(question['id'])] = answer
        session['answers'] = answers
        
        # Handle previous question
        if request.form.get('previous_question'):
            if current_question > 1:
                session['current_question'] = current_question - 1
            return redirect(url_for('quiz.take_quiz'))
        
        # Handle next question
        if request.form.get('next_question'):
            session['current_question'] = current_question + 1
            return redirect(url_for('quiz.take_quiz'))
        
        # Handle submit quiz
        if request.form.get('submit_quiz'):
            return handle_quiz_submission()
    
    # Get the user's answer for this question if it exists
    answer = session.get('answers', {}).get(str(question['id']))
    
    return render_template('quiz/take_quiz.html', 
                          question=question,
                          current_question=current_question,
                          total_questions=total_questions,
                          answer=answer)

def handle_quiz_submission():
    try:
        # Calculate results
        questions = session['quiz_questions']
        answers = session.get('answers', {})
        total_questions = len(questions)
        
        # Calculate score
        correct_count = 0
        for question in questions:
            question_id = str(question['id'])
            user_answer = answers.get(question_id)
            if user_answer and user_answer == question['correct_answer']:
                correct_count += 1
        
        score = (correct_count / total_questions) * 100 if total_questions > 0 else 0
        
        # Calculate time taken
        start_time = session.get('quiz_start_time', 0)
        end_time = int(time.time())
        time_taken = end_time - start_time
        
        # Store results in database
        conn = get_db_connection()
        if conn is None:
            flash('Database connection error.', 'danger')
            return redirect(url_for('main.index'))
        
        cursor = conn.cursor()
        try:
            # Get subject_id from the first question
            subject_id = questions[0]['subject_id'] if questions else None
            
            # Insert result
            cursor.execute('''
                INSERT INTO results (
                    user_id, subject_id, score, total_questions,
                    time_taken, answers, date_taken
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (
                session.get('user_id'), 
                subject_id,
                score,
                total_questions,
                time_taken,
                json.dumps(answers),
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ))
            conn.commit()
            
            # Get the result_id
            result_id = cursor.lastrowid
            
            # Clear quiz session
            session.pop('quiz_questions', None)
            session.pop('current_question', None)
            session.pop('total_questions', None)
            session.pop('answers', None)
            session.pop('quiz_start_time', None)
            
            flash('Quiz completed successfully!', 'success')
            return redirect(url_for('quiz.results', result_id=result_id))
            
        except Error as err:
            conn.rollback()
            logger.error(f"Error in take_quiz: {str(err)}")
            flash('Error submitting quiz.', 'danger')
            return redirect(url_for('quiz.quiz'))
        finally:
            cursor.close()
            conn.close()
            
    except Exception as e:
        logger.error(f"Error in handle_quiz_submission: {str(e)}")
        flash('Error processing quiz.', 'danger')
        return redirect(url_for('quiz.quiz'))

@quiz_bp.route('/submit-quiz', methods=['POST'])
@login_required
def submit_quiz():
    """Handle quiz submission"""
    try:
        quiz_session = get_quiz_session()
        if not quiz_session:
            flash('No active quiz found.', 'error')
            return redirect(url_for('quiz.quiz'))

        # Get the last answer from the form
        answer = request.form.get('answer')
        question_id = request.form.get('question_id')
        time_taken = int(request.form.get('time_taken', 0))
        
        if answer and question_id:
            quiz_session.answers[question_id] = answer
            session['quiz_session'] = quiz_session.__dict__

        current_time = datetime.now(timezone.utc)
        total_time_taken = int((current_time - quiz_session.start_time.replace(tzinfo=timezone.utc)).total_seconds())

        # Calculate score and prepare question details
        total_questions = len(quiz_session.questions)
        correct_count = 0
        question_details = []

        for question in quiz_session.questions:
            question_id = str(question['id'])
            user_answer = quiz_session.answers.get(question_id)
            is_correct = user_answer and user_answer == question['correct_answer']
            
            if is_correct:
                correct_count += 1
            
            question_details.append({
                'id': question_id,
                'question': question['question'],
                'user_answer': user_answer,
                'correct_answer': question['correct_answer'],
                'is_correct': is_correct,
                'explanation': question.get('explanation', ''),
                'difficulty': question['difficulty'],
                'chapter': question.get('chapter', ''),
                'topics': question.get('topics', [])
            })
        
        score = int((correct_count / total_questions) * 100) if total_questions > 0 else 0

        # Store result in database
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        try:
            # First, verify the subject exists if subject_id is provided
            if quiz_session.subject_id:
                cursor.execute('SELECT id FROM subjects WHERE id = %s', (quiz_session.subject_id,))
                if not cursor.fetchone():
                    raise ValueError(f"Subject with ID {quiz_session.subject_id} not found")

            # Insert the result
            cursor.execute('''
                INSERT INTO results (
                    user_id, subject_id, score, total_questions,
                    time_taken, answers, date_taken
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (
                session['user_id'],
                quiz_session.subject_id,
                score,
                total_questions,
                total_time_taken,
                json.dumps(quiz_session.answers),
                current_time.replace(tzinfo=None)
            ))
            
            result_id = cursor.lastrowid
            conn.commit()
            
            # Clear quiz session
            session.pop('quiz_session', None)
            session.pop('quiz_active', None)
            
            logger.info(f"Quiz submitted successfully. Result ID: {result_id}")
            return redirect(url_for('quiz.results', result_id=result_id))
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error in quiz submission: {str(e)}")
            flash('Error saving quiz results. Please try again.', 'error')
            return redirect(url_for('quiz.take_quiz'))
        finally:
            conn.close()

    except Exception as e:
        logger.error(f"Error submitting quiz: {str(e)}")
        flash('Error submitting quiz. Please try again.', 'error')
        return redirect(url_for('quiz.take_quiz'))

@quiz_bp.route('/results/<int:result_id>')
@login_required
def results(result_id):
    """Display quiz results"""
    try:
        logger.info(f"Fetching results for result_id: {result_id}")
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get result details with subject information
        cursor.execute('''
            SELECT r.*, s.name as subject_name 
            FROM results r
            LEFT JOIN subjects s ON r.subject_id = s.id
            WHERE r.id = %s AND r.user_id = %s
        ''', (result_id, session['user_id']))
        
        result = cursor.fetchone()
        logger.info(f"Found result: {result}")
        
        if not result:
            logger.warning(f"Result not found for id: {result_id}")
            flash('Result not found.', 'error')
            return redirect(url_for('quiz.quiz'))

        # Parse stored answers
        answers = json.loads(result['answers']) if result['answers'] else {}
        logger.info(f"Parsed answers: {answers}")
        
        # Get question IDs from the answers dictionary
        question_ids = list(answers.keys())
        logger.info(f"Question IDs: {question_ids}")
        
        # Get full question details from database
        if question_ids:
            cursor.execute('''
                SELECT q.*, s.name as subject_name
                FROM questions q
                JOIN subjects s ON q.subject_id = s.id
                WHERE q.id IN ({})
            '''.format(','.join(['%s'] * len(question_ids))), question_ids)
            
            questions = cursor.fetchall()
            logger.info(f"Found {len(questions)} questions")
            
            # Process questions and check answers
            processed_questions = []
            correct_answers = 0
            
            for question in questions:
                question_id = str(question['id'])
                user_answer = answers.get(question_id)
                is_correct = user_answer == question['correct_answer']
                
                if is_correct:
                    correct_answers += 1
                
                processed_questions.append({
                    'id': question_id,
                    'question': question['question'],
                    'options': {
                        'A': question['option_a'],
                        'B': question['option_b'],
                        'C': question['option_c'],
                        'D': question['option_d']
                    },
                    'user_answer': user_answer,
                    'correct_answer': question['correct_answer'],
                    'is_correct': is_correct,
                    'explanation': question.get('explanation', ''),
                    'difficulty': question['difficulty'],
                    'chapter': question.get('chapter', ''),
                    'topics': json.loads(question['topics']) if question['topics'] else []
                })
            
            # Calculate statistics
            statistics = {
                'total_questions': len(questions),
                'answered_questions': len(answers),
                'correct_answers': correct_answers,
                'score': result['score'],
                'time_taken': format_time(result['time_taken']),
                'difficulty_distribution': {
                    'easy': sum(1 for q in questions if q['difficulty'] == 'easy'),
                    'medium': sum(1 for q in questions if q['difficulty'] == 'medium'),
                    'hard': sum(1 for q in questions if q['difficulty'] == 'hard')
                }
            }
            
            # Calculate width percentages for progress bars
            total = len(questions)
            easy_count = statistics['difficulty_distribution']['easy']
            medium_count = statistics['difficulty_distribution']['medium']
            hard_count = statistics['difficulty_distribution']['hard']
            
            easy_width = int((easy_count / total * 100) if total > 0 else 0)
            medium_width = int((medium_count / total * 100) if total > 0 else 0)
            hard_width = int((hard_count / total * 100) if total > 0 else 0)
            
            logger.info(f"Calculated statistics: {statistics}")

            # Group questions by chapter and topic for subject-wise quizzes
            if result['subject_id']:
                chapter_stats = {}
                topic_stats = {}
                
                for question in questions:
                    # Update chapter stats
                    if question['chapter']:
                        chapter_stats[question['chapter']] = chapter_stats.get(question['chapter'], {'total': 0, 'correct': 0})
                        chapter_stats[question['chapter']]['total'] += 1
                        if answers.get(str(question['id'])) == question['correct_answer']:
                            chapter_stats[question['chapter']]['correct'] += 1
                    
                    # Update topic stats
                    if question['topics']:
                        topics = json.loads(question['topics']) if isinstance(question['topics'], str) else question['topics']
                        for topic in topics:
                            topic_stats[topic] = topic_stats.get(topic, {'total': 0, 'correct': 0})
                            topic_stats[topic]['total'] += 1
                            if answers.get(str(question['id'])) == question['correct_answer']:
                                topic_stats[topic]['correct'] += 1
                
                statistics['chapter_stats'] = chapter_stats
                statistics['topic_stats'] = topic_stats
                logger.info(f"Chapter stats: {chapter_stats}")
                logger.info(f"Topic stats: {topic_stats}")

            logger.info("Rendering template with data")
            return render_template('quiz/quiz_results.html',
                                 result=result,
                                 questions=processed_questions,
                                 statistics=statistics,
                                 subject_name=result['subject_name'],
                                 quiz_type='subject_wise',
                                 easy_count=easy_count,
                                 medium_count=medium_count,
                                 hard_count=hard_count,
                                 easy_width=easy_width,
                                 medium_width=medium_width,
                                 hard_width=hard_width,
                                 total_questions=total)

    except Exception as e:
        logger.error(f"Error displaying results: {str(e)}", exc_info=True)
        flash('Error displaying results.', 'error')
        return redirect(url_for('quiz.quiz'))
    finally:
        if 'conn' in locals():
            conn.close()

def generate_quiz_questions(subject_id=None, chapter=None, topics=None, difficulty=None, num_questions=10, user_data=None, cursor=None):
    """Generate quiz questions based on selected criteria"""
    try:
        query = '''
            SELECT q.*, s.name as subject_name
            FROM questions q
            JOIN subjects s ON q.subject_id = s.id
            WHERE q.subject_id = %s
        '''
        params = [subject_id]

        if chapter:
            query += ' AND q.chapter = %s'
            params.append(chapter)

        if topics:
            topic_conditions = []
            for topic in topics:
                topic_conditions.append('JSON_CONTAINS(q.topics, %s)')
                params.append(f'"{topic}"')
            query += f' AND ({" OR ".join(topic_conditions)})'

        if difficulty:
            query += ' AND q.difficulty = %s'
            params.append(difficulty)

        # Add degree access check for non-superadmin users
        if user_data['role'] != 'superadmin':
            query += '''
                AND EXISTS (
                    SELECT 1 FROM plan_subject_access psa
                    WHERE psa.subject_id = s.id
                    AND psa.plan_id = %s
                    AND (s.degree_type = %s OR %s = 'both')
                )
            '''
            params.extend([user_data['plan_id'], user_data['degree_access'], user_data['degree_access']])

        query += ' ORDER BY RAND() LIMIT %s'
        params.append(num_questions)

        cursor.execute(query, params)
        return cursor.fetchall()

    except Error as err:
        logger.error(f"Error generating quiz questions: {str(err)}")
        return None

def init_quiz_session(questions: List[Dict], quiz_type: str, quiz_params: dict) -> None:
    """Initialize a new quiz session"""
    try:
        # Convert questions to a list of dictionaries and extract question IDs
        question_list = [dict(q) for q in questions]
        question_ids = [str(q['id']) for q in question_list]
        
        # Create quiz session dictionary with all necessary fields
        quiz_session = {
            'questions': question_list,
            'question_ids': question_ids,
            'total_questions': len(questions),
            'start_time': datetime.now(timezone.utc).isoformat(),
            'answers': {},
            'quiz_type': quiz_type,
            'exam_id': quiz_params.get('exam_id', None),
            'subject_id': quiz_params.get('subject_id', None)
        }
        
        session['quiz_session'] = quiz_session
        session['quiz_active'] = True
        logger.info(f"Quiz session initialized: {len(questions)} questions, type={quiz_type}")
    except Exception as e:
        logger.error(f"Error initializing quiz session: {str(e)}")
        raise

def get_quiz_session() -> Optional[QuizSession]:
    """Retrieve current quiz session"""
    if 'quiz_session' not in session:
        return None
    return QuizSession(**session['quiz_session'])

def validate_question_number(question_num: Optional[str], total_questions: int) -> int:
    """Validate and return the current question number"""
    try:
        num = int(question_num) if question_num else 1
        return max(1, min(num, total_questions))
    except (ValueError, TypeError):
        return 1

def format_time(seconds: int) -> str:
    """Format seconds into readable time"""
    minutes = seconds // 60
    remaining_seconds = seconds % 60
    return f"{minutes}:{remaining_seconds:02d}"

@quiz_bp.route('/test-history')
@login_required
def test_history():
    """Display user's quiz history"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get user's quiz history with subject information
        cursor.execute('''
            SELECT r.*, s.name as subject_name 
            FROM results r
            LEFT JOIN subjects s ON r.subject_id = s.id
            WHERE r.user_id = %s
            ORDER BY r.date_taken DESC
        ''', (session['user_id'],))
        
        results = cursor.fetchall()
        
        # Process each result to calculate statistics
        for result in results:
            answers = json.loads(result['answers']) if result['answers'] else {}
            
            # Get question IDs from the answers dictionary
            question_ids = list(answers.keys())
            
            # Get full question details from database
            if question_ids:
                cursor.execute('''
                    SELECT q.*
                    FROM questions q
                    WHERE q.id IN ({})
                '''.format(','.join(['%s'] * len(question_ids))), question_ids)
                
                questions = cursor.fetchall()
                
                # Calculate correct answers by comparing with correct answers
                correct_answers = 0
                for question in questions:
                    question_id = str(question['id'])
                    user_answer = answers.get(question_id)
                    if user_answer == question['correct_answer']:
                        correct_answers += 1
                
                result['total_questions'] = len(questions)
                result['correct_answers'] = correct_answers
                result['score_percentage'] = (correct_answers / len(questions) * 100) if len(questions) > 0 else 0
                result['time_taken_formatted'] = format_time(result['time_taken'])
        
        return render_template('quiz/test_history.html', results=results)
        
    except Exception as e:
        logger.error(f"Error displaying test history: {str(e)}", exc_info=True)
        flash('Error displaying test history.', 'error')
        return redirect(url_for('quiz.quiz'))
    finally:
        if 'conn' in locals():
            conn.close() 