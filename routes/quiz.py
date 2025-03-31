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
    """Handle quiz selection and generation"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Get user's subscription details
        cursor.execute('''
            SELECT 
                u.role,
                COALESCE(u.subscription_plan_id, i.subscription_plan_id) as plan_id,
                COALESCE(sp_ind.degree_access, sp_inst.degree_access) as degree_access
            FROM users u
            LEFT JOIN institutions i ON u.institution_id = i.id
            LEFT JOIN subscription_plans sp_ind ON u.subscription_plan_id = sp_ind.id
            LEFT JOIN subscription_plans sp_inst ON i.subscription_plan_id = sp_inst.id
            WHERE u.id = %s
        ''', (session['user_id'],))
        user_data = cursor.fetchone()
        
        if not user_data:
            flash('User subscription details not found.', 'error')
            return redirect(url_for('user.user_dashboard'))

        if request.method == 'POST':
            quiz_type = request.form.get('quiz_type')
            subject_id = request.form.get('subject_id', type=int)
            exam_id = request.form.get('exam_id', type=int)
            chapter = request.form.get('chapter')
            selected_topics = request.form.getlist('topics')
            difficulty = request.form.get('difficulty')
            num_questions = request.form.get('num_questions', type=int, default=10)

            # Validate based on quiz type
            if quiz_type == 'subject_wise':
                if not subject_id:
                    flash('Please select a subject for Subject-wise quiz.', 'error')
                    return redirect(url_for('quiz.quiz', quiz_type=quiz_type))
            
                # Generate quiz questions for subject-wise
                questions = generate_quiz_questions(
                    quiz_type=quiz_type,
                    subject_id=subject_id,
                    chapter=chapter,
                    topics=selected_topics,
                    difficulty=difficulty,
                    num_questions=num_questions,
                    user_data=user_data,
                    cursor=cursor
                )

            elif quiz_type == 'previous_year':
                if not exam_id:
                    flash('Please select an exam for Previous Year quiz.', 'error')
                    return redirect(url_for('quiz.quiz', quiz_type=quiz_type))

                # Generate quiz questions for previous year
                questions = generate_quiz_questions(
                    quiz_type=quiz_type,
                    exam_id=exam_id,
                    difficulty=difficulty,
                    num_questions=num_questions,
                    user_data=user_data,
                    cursor=cursor
                )
            else:
                flash('Please select a valid quiz type.', 'error')
                return redirect(url_for('quiz.quiz'))

            if not questions:
                flash('No questions found matching your criteria.', 'error')
                return redirect(url_for('quiz.quiz', quiz_type=quiz_type))

            # Initialize quiz session
            quiz_params = {
                'exam_id': exam_id if quiz_type == 'previous_year' else questions[0]['exam_id'],
                'subject_id': subject_id if quiz_type == 'subject_wise' else None
            }
            init_quiz_session(questions, quiz_type, quiz_params)
            return redirect(url_for('quiz.take_quiz'))

        # Handle GET request - Load filters
        quiz_type = request.args.get('quiz_type')
        subject_id = request.args.get('subject_id', type=int)
        chapters = []
        topics = []
        subjects = []
        exams = []

        # Get exams for previous year quiz type
        cursor.execute('''
            SELECT DISTINCT e.id, e.name
            FROM exams e
            JOIN subjects s ON e.id = s.exam_id
            JOIN questions q ON s.id = q.subject_id
            JOIN plan_exam_access pea ON e.id = pea.exam_id
            WHERE pea.plan_id = %s
            AND (e.degree_type = %s OR %s = 'both')
            AND q.is_previous_year = TRUE
            ORDER BY e.name
        ''', (user_data['plan_id'], user_data['degree_access'], user_data['degree_access']))
        exams = cursor.fetchall()

        if quiz_type == 'subject_wise':
            # Get subjects
            cursor.execute('''
                SELECT DISTINCT s.id, s.name, s.exam_id, e.name as exam_name
                FROM subjects s
                JOIN exams e ON s.exam_id = e.id
                JOIN questions q ON s.id = q.subject_id
                JOIN plan_exam_access pea ON e.id = pea.exam_id
                WHERE pea.plan_id = %s
                AND (e.degree_type = %s OR %s = 'both')
                ORDER BY s.name
            ''', (user_data['plan_id'], user_data['degree_access'], user_data['degree_access']))
            subjects = cursor.fetchall()

            # Get chapters and topics if subject is selected
            if subject_id:
                cursor.execute('''
                    SELECT DISTINCT chapter
                    FROM questions
                    WHERE subject_id = %s
                    AND chapter IS NOT NULL
                    ORDER BY chapter
                ''', (subject_id,))
                chapters = [row['chapter'] for row in cursor.fetchall() if row['chapter']]

                # Get topics using JSON_TABLE for better performance
                cursor.execute('''
                    SELECT DISTINCT topic
                    FROM questions,
                    JSON_TABLE(
                        topics,
                        '$[*]' COLUMNS (topic VARCHAR(255) PATH '$')
                    ) AS topics_table
                    WHERE subject_id = %s 
                    AND topics IS NOT NULL
                    ORDER BY topic
                ''', (subject_id,))
                topics = [row['topic'] for row in cursor.fetchall() if row['topic']]

        return render_template('quiz.html',
                             quiz_type=quiz_type,
                             subjects=subjects,
                             chapters=chapters,
                             topics=topics,
                             selected_subject_id=subject_id,
                             user_data=user_data,
                             exams=exams)

    except Exception as e:
        logger.error(f"Error in quiz route: {str(e)}", exc_info=True)
        flash('An error occurred while loading the quiz page.', 'error')
        return redirect(url_for('user.user_dashboard'))
    finally:
        if 'conn' in locals():
            conn.close()

@quiz_bp.route('/get-subject-filters')
@login_required
def get_subject_filters():
    """Get chapters and topics for a subject"""
    try:
        subject_id = request.args.get('subject_id')
        logger.info(f"Received request for subject_id: {subject_id}")
        
        if not subject_id:
            logger.warning("No subject_id provided in request")
            return jsonify({'error': 'Subject ID is required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        try:
            # Get user's subscription details
            logger.info("Fetching user subscription details...")
            cursor.execute('''
                SELECT 
                    u.role,
                    COALESCE(u.subscription_plan_id, i.subscription_plan_id) as plan_id,
                    COALESCE(sp_ind.degree_access, sp_inst.degree_access) as degree_access
                FROM users u
                LEFT JOIN institutions i ON u.institution_id = i.id
                LEFT JOIN subscription_plans sp_ind ON u.subscription_plan_id = sp_ind.id
                LEFT JOIN subscription_plans sp_inst ON i.subscription_plan_id = sp_inst.id
                WHERE u.id = %s
            ''', (session['user_id'],))
            user_data = cursor.fetchone()
            
            logger.info(f"User subscription data: {user_data}")
            
            if not user_data:
                logger.error("No user subscription data found")
                return jsonify({'error': 'User subscription details not found'}), 403

            # First, check if the subject exists and is accessible
            logger.info("Checking subject accessibility...")
            cursor.execute('''
                SELECT s.id, s.name, s.exam_id
                FROM subjects s
                JOIN exams e ON s.exam_id = e.id
                JOIN plan_exam_access pea ON e.id = pea.exam_id
                WHERE s.id = %s
                AND pea.plan_id = %s
                AND (e.degree_type = %s OR %s = 'both')
            ''', (subject_id, user_data['plan_id'], user_data['degree_access'], user_data['degree_access']))
            
            subject = cursor.fetchone()
            if not subject:
                logger.error(f"Subject {subject_id} not found or not accessible")
                return jsonify({'error': 'Subject not found or not accessible'}), 404

            # Get chapters for the subject
            logger.info("Fetching chapters...")
            chapter_query = '''
                SELECT DISTINCT chapter
                FROM questions q
                JOIN subjects s ON q.subject_id = s.id
                JOIN exams e ON s.exam_id = e.id
                JOIN plan_exam_access pea ON e.id = pea.exam_id
                WHERE q.subject_id = %s
                AND pea.plan_id = %s
                AND (e.degree_type = %s OR %s = 'both')
                AND q.chapter IS NOT NULL 
                AND q.chapter != ''
                ORDER BY q.chapter
            '''
            logger.info(f"Executing chapter query: {chapter_query}")
            logger.info(f"Chapter query parameters: {subject_id, user_data['plan_id'], user_data['degree_access'], user_data['degree_access']}")
            
            cursor.execute(chapter_query, (subject_id, user_data['plan_id'], user_data['degree_access'], user_data['degree_access']))
            chapters = [row['chapter'] for row in cursor.fetchall()]
            logger.info(f"Found chapters: {chapters}")

            # Get topics for the subject
            logger.info("Fetching topics...")
            topic_query = '''
                SELECT DISTINCT topic
                FROM questions q
                JOIN subjects s ON q.subject_id = s.id
                JOIN exams e ON s.exam_id = e.id
                JOIN plan_exam_access pea ON e.id = pea.exam_id,
                JSON_TABLE(
                    q.topics,
                    '$[*]' COLUMNS (topic VARCHAR(255) PATH '$')
                ) AS topics_table
                WHERE q.subject_id = %s
                AND pea.plan_id = %s
                AND (e.degree_type = %s OR %s = 'both')
                AND q.topics IS NOT NULL
                AND JSON_LENGTH(q.topics) > 0
                AND topic IS NOT NULL
                AND topic != ''
                ORDER BY topic
            '''
            logger.info(f"Executing topic query: {topic_query}")
            logger.info(f"Topic query parameters: {subject_id, user_data['plan_id'], user_data['degree_access'], user_data['degree_access']}")
            
            cursor.execute(topic_query, (subject_id, user_data['plan_id'], user_data['degree_access'], user_data['degree_access']))
            topics = [row['topic'] for row in cursor.fetchall()]
            logger.info(f"Found topics: {topics}")

            # Get total question count for the subject
            logger.info("Fetching total question count...")
            count_query = '''
                SELECT COUNT(*) as count
                FROM questions q
                JOIN subjects s ON q.subject_id = s.id
                JOIN exams e ON s.exam_id = e.id
                JOIN plan_exam_access pea ON e.id = pea.exam_id
                WHERE q.subject_id = %s
                AND pea.plan_id = %s
                AND (e.degree_type = %s OR %s = 'both')
            '''
            logger.info(f"Executing count query: {count_query}")
            logger.info(f"Count query parameters: {subject_id, user_data['plan_id'], user_data['degree_access'], user_data['degree_access']}")
            
            cursor.execute(count_query, (subject_id, user_data['plan_id'], user_data['degree_access'], user_data['degree_access']))
            question_count = cursor.fetchone()['count']
            logger.info(f"Total question count: {question_count}")

            response = {
                'chapters': chapters,
                'topics': topics,
                'question_count': question_count
            }
            logger.info(f"Sending response: {response}")
            return jsonify(response)

        finally:
            cursor.close()
            conn.close()

    except Exception as e:
        logger.error(f"Error in get_subject_filters: {str(e)}", exc_info=True)
        return jsonify({'error': 'Failed to fetch filters'}), 500

@quiz_bp.route('/take-quiz', methods=['GET', 'POST'])
@login_required
def take_quiz():
    """Handle quiz taking process"""
    try:
        quiz_session = session.get('quiz_session')
        if not quiz_session:
            flash('No active quiz session found.', 'error')
            return redirect(url_for('quiz.quiz'))

        current_question = int(request.args.get('question_number', 1))
        total_questions = quiz_session['total_questions']
        question_ids = quiz_session['question_ids']
        answers = quiz_session.get('answers', {})

        if current_question < 1 or current_question > total_questions:
            flash('Invalid question number.', 'error')
            return redirect(url_for('quiz.quiz'))

        # Get the current question
        question_id = question_ids[current_question - 1]
        
        # Get question from database
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute('''
                SELECT q.*, s.name as subject_name, s.exam_id, e.name as exam_name
                FROM questions q
                JOIN subjects s ON q.subject_id = s.id
                JOIN exams e ON s.exam_id = e.id
                WHERE q.id = %s
            ''', (question_id,))
            question = cursor.fetchone()
            
            if not question:
                flash('Question not found.', 'error')
                return redirect(url_for('quiz.quiz'))
        finally:
            cursor.close()
            conn.close()

        if request.method == 'POST':
            # Handle quiz cancellation
            if request.form.get('cancel_quiz') == 'true':
                session.pop('quiz_session', None)
                session.pop('quiz_active', None)
                flash('Quiz cancelled successfully.', 'info')
                return redirect(url_for('quiz.quiz'))

            # Handle answer submission
            answer = request.form.get('answer')
            if not answer:
                flash('Please select an answer.', 'error')
                return redirect(url_for('quiz.take_quiz', question_number=current_question))

            # Store the answer
            answers[str(question_id)] = answer
            quiz_session['answers'] = answers
            session['quiz_session'] = quiz_session

            # Handle next question or quiz submission
            if request.form.get('next_question') == 'true' and current_question < total_questions:
                return redirect(url_for('quiz.take_quiz', question_number=current_question + 1))
            elif request.form.get('submit_quiz') == 'true':
                # Calculate score
                correct_answers = 0
                for q_id, ans in answers.items():
                    conn = get_db_connection()
                    cursor = conn.cursor(dictionary=True)
                    try:
                        cursor.execute('SELECT correct_answer FROM questions WHERE id = %s', (q_id,))
                        q = cursor.fetchone()
                        if q and q['correct_answer'] == ans:
                            correct_answers += 1
                    finally:
                        cursor.close()
                        conn.close()

                score = (correct_answers / total_questions) * 100
                time_taken = int(request.form.get('time_taken', 0))

                # Store result in database
                conn = get_db_connection()
                cursor = conn.cursor(dictionary=True)
                try:
                    cursor.execute('''
                        INSERT INTO results (
                            user_id, exam_id, subject_id, score, total_questions,
                            time_taken, answers, question_details, date_taken
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (
                        session['user_id'],
                        question['exam_id'],
                        question['subject_id'] if quiz_session['quiz_type'] == 'subject_wise' else None,
                        score,
                        total_questions,
                        time_taken,
                        json.dumps(answers),
                        json.dumps({
                            'question_ids': question_ids,
                            'answers': answers
                        }),
                        datetime.now(timezone.utc).replace(tzinfo=None)
                    ))
                    result_id = cursor.lastrowid
                    conn.commit()
                finally:
                    cursor.close()
                    conn.close()

                # Clear quiz session
                session.pop('quiz_session', None)
                session.pop('quiz_active', None)

                flash('Quiz submitted successfully!', 'success')
                return redirect(url_for('quiz.results', result_id=result_id))

        return render_template('take_quiz.html',
                             question=question,
                             current_question=current_question,
                             total_questions=total_questions,
                             answer=answers.get(str(question_id)),
                             quiz_type=quiz_session['quiz_type'])

    except Exception as e:
        current_app.logger.error(f"Error in take_quiz: {str(e)}")
        flash('An error occurred while taking the quiz.', 'error')
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
                    user_id, exam_id, subject_id, score, total_questions,
                    time_taken, answers, question_details, date_taken
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                session['user_id'],
                quiz_session.exam_id,
                quiz_session.subject_id,
                score,
                total_questions,
                total_time_taken,
                json.dumps(quiz_session.answers),
                json.dumps(question_details),
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
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get result details with exam and subject information
        cursor.execute('''
            SELECT r.*, e.name as exam_name, s.name as subject_name 
            FROM results r
            LEFT JOIN exams e ON r.exam_id = e.id
            LEFT JOIN subjects s ON r.subject_id = s.id
            WHERE r.id = %s AND r.user_id = %s
        ''', (result_id, session['user_id']))
        
        result = cursor.fetchone()
        
        if not result:
            flash('Result not found.', 'error')
            return redirect(url_for('quiz.quiz'))

        # Parse stored data
        answers = json.loads(result['answers']) if result['answers'] else {}
        question_details = json.loads(result['question_details']) if result['question_details'] else {}
        
        # Get question details from the stored structure
        question_ids = question_details.get('question_ids', [])
        
        # Get full question details from database
        if question_ids:
            cursor.execute('''
                SELECT q.*, s.name as subject_name, e.name as exam_name
                FROM questions q
                JOIN subjects s ON q.subject_id = s.id
                JOIN exams e ON s.exam_id = e.id
                WHERE q.id IN ({})
            '''.format(','.join(['%s'] * len(question_ids))), question_ids)
            
            questions = cursor.fetchall()
            
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

            return render_template('quiz_results.html',
                                 result=result,
                                 questions=processed_questions,
                                 statistics=statistics,
                                 exam_name=result['exam_name'],
                                 subject_name=result['subject_name'],
                                 quiz_type='previous_year' if result['subject_id'] is None else 'subject_wise')

    except Exception as e:
        logger.error(f"Error displaying results: {str(e)}", exc_info=True)
        flash('Error displaying results.', 'error')
        return redirect(url_for('quiz.quiz'))
    finally:
        if 'conn' in locals():
            conn.close()

def generate_quiz_questions(quiz_type, subject_id=None, exam_id=None, chapter=None, 
                          topics=None, difficulty=None, num_questions=10, user_data=None, cursor=None):
    """Generate quiz questions based on selected criteria"""
    try:
        query = '''
            SELECT DISTINCT q.*, s.name AS subject_name, e.name as exam_name, s.exam_id
            FROM questions q
            JOIN subjects s ON q.subject_id = s.id
            JOIN exams e ON s.exam_id = e.id
            JOIN plan_exam_access pea ON e.id = pea.exam_id
            WHERE pea.plan_id = %s
            AND (e.degree_type = %s OR %s = 'both')
        '''
        params = [user_data['plan_id'], user_data['degree_access'], user_data['degree_access']]

        if quiz_type == 'subject_wise':
            if subject_id:
                query += ' AND q.subject_id = %s'
                params.append(subject_id)

                if chapter:
                    query += ' AND q.chapter = %s'
                    params.append(chapter)

                if topics:
                    topic_conditions = []
                    for topic in topics:
                        topic_conditions.append('JSON_CONTAINS(q.topics, %s)')
                        params.append(json.dumps(topic))
                    if topic_conditions:
                        query += f' AND ({" OR ".join(topic_conditions)})'

        elif quiz_type == 'previous_year':
            query += ' AND q.is_previous_year = TRUE'
            if exam_id:
                query += ' AND s.exam_id = %s'
                params.append(exam_id)

        if difficulty:
            query += ' AND q.difficulty = %s'
            params.append(difficulty)

        query += ' ORDER BY RAND() LIMIT %s'
        params.append(num_questions)

        logger.info(f"Executing query with params: {params}")
        cursor.execute(query, params)
        questions = cursor.fetchall()
        
        logger.info(f"Found {len(questions)} questions")
        return questions

    except Exception as e:
        logger.error(f"Error generating quiz questions: {str(e)}", exc_info=True)
        return []

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
            'exam_id': quiz_params['exam_id'],
            'subject_id': quiz_params['subject_id']
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

@quiz_bp.route('/get-question-count', methods=['POST'])
@login_required
def get_question_count():
    """Get available question count based on selected filters"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Get user's subscription details
        cursor.execute('''
            SELECT 
                u.role,
                COALESCE(u.subscription_plan_id, i.subscription_plan_id) as plan_id,
                COALESCE(sp_ind.degree_access, sp_inst.degree_access) as degree_access
            FROM users u
            LEFT JOIN institutions i ON u.institution_id = i.id
            LEFT JOIN subscription_plans sp_ind ON u.subscription_plan_id = sp_ind.id
            LEFT JOIN subscription_plans sp_inst ON i.subscription_plan_id = sp_inst.id
            WHERE u.id = %s
        ''', (session['user_id'],))
        user_data = cursor.fetchone()
        
        if not user_data:
            return jsonify({'error': 'User subscription details not found'}), 400

        quiz_type = request.form.get('quiz_type')
        subject_id = request.form.get('subject_id')
        exam_id = request.form.get('exam_id')
        chapter = request.form.get('chapter')
        topics = request.form.getlist('topics')
        difficulty = request.form.get('difficulty')

        query = '''
            SELECT COUNT(DISTINCT q.id) as count
            FROM questions q
            JOIN subjects s ON q.subject_id = s.id
            JOIN exams e ON s.exam_id = e.id
            JOIN plan_exam_access pea ON e.id = pea.exam_id
            WHERE pea.plan_id = %s
            AND (e.degree_type = %s OR %s = 'both')
        '''
        params = [user_data['plan_id'], user_data['degree_access'], user_data['degree_access']]

        if quiz_type == 'subject_wise' and subject_id:
            query += ' AND q.subject_id = %s'
            params.append(subject_id)

            if chapter:
                query += ' AND q.chapter = %s'
                params.append(chapter)

            if topics:
                topic_conditions = []
                for topic in topics:
                    topic_conditions.append('JSON_CONTAINS(q.topics, %s)')
                    params.append(json.dumps(topic))
                if topic_conditions:
                    query += f' AND ({" OR ".join(topic_conditions)})'

        elif quiz_type == 'previous_year' and exam_id:
            query += ' AND q.is_previous_year = TRUE'
            query += ' AND s.exam_id = %s'
            params.append(exam_id)

        if difficulty:
            query += ' AND q.difficulty = %s'
            params.append(difficulty)

        cursor.execute(query, params)
        result = cursor.fetchone()
        question_count = result['count'] if result else 0

        return jsonify({'count': question_count})

    except Exception as e:
        logger.error(f"Error getting question count: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        if 'conn' in locals():
            conn.close() 