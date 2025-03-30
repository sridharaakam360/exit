from flask import Blueprint, render_template, redirect, url_for, flash, session, request, jsonify
from flask import current_app
from functools import wraps
from typing import Dict, Any
import logging
from .services.quiz_service import QuizService
from .models.quiz import Quiz
from utils.security import login_required, quiz_access_required, sanitize_input
from datetime import datetime

quiz_bp = Blueprint('quiz', __name__)
logger = logging.getLogger(__name__)

def handle_quiz_error(f):
    """Decorator to handle quiz-related errors consistently."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Quiz error: {str(e)}")
            flash('An error occurred while processing your request.', 'error')
            return redirect(url_for('quiz.quiz'))
    return decorated_function

@quiz_bp.route('/', methods=['GET', 'POST'])
@login_required
@quiz_access_required
@handle_quiz_error
def quiz():
    """Main quiz page - handles both quiz generation and taking."""
    user_id = session['user_id']
    
    # Get user's accessible exams and stats
    accessible_exams = QuizService.get_accessible_exams(user_id)
    stats = QuizService.get_user_quiz_stats(user_id)
    recent_attempts = QuizService.get_recent_attempts(user_id)
    
    # Handle quiz generation request
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

        # Generate quiz
        quiz = QuizService.generate_quiz(
            user_id=user_id,
            exam_id=exam_id,
            subject_id=subject_id,
            quiz_type=quiz_type,
            difficulty=difficulty,
            num_questions=num_questions
        )
        
        # Store quiz in session
        session['quiz'] = quiz.to_dict()
        session['current_question'] = 0
        
        return render_template('quiz.html',
                             quiz=quiz,
                             accessible_exams=accessible_exams,
                             subjects=QuizService.get_subjects_for_exam(exam_id),
                             stats=stats,
                             recent_attempts=recent_attempts)
    
    # Handle quiz submission
    elif request.method == 'POST' and 'quiz' in session:
        quiz_data = session['quiz']
        quiz = Quiz.from_dict(quiz_data)
        
        # Extract answers and time taken
        answers = {}
        for key, value in request.form.items():
            if key.startswith('question_'):
                question_id = key.replace('question_', '')
                answers[question_id] = value.upper()
        
        time_taken = int(request.form.get('time_taken', 0))
        
        # Submit quiz
        quiz.submit(answers, time_taken)
        result_id = QuizService.save_quiz_result(quiz)
        
        # Clear quiz from session
        session.pop('quiz', None)
        session.pop('current_question', None)
        
        flash(f'Quiz completed! Your score: {quiz.score}/{quiz.num_questions}', 'success')
        return redirect(url_for('quiz.results', result_id=result_id))
    
    # Display quiz generation form
    return render_template('quiz.html',
                         accessible_exams=accessible_exams,
                         subjects=[],
                         stats=stats,
                         recent_attempts=recent_attempts)

@quiz_bp.route('/results')
@login_required
@handle_quiz_error
def results():
    """Display quiz results."""
    result_id = request.args.get('result_id', type=int)
    if not result_id:
        flash('Result ID is required.', 'error')
        return redirect(url_for('quiz.quiz'))

    result = QuizService.get_quiz_result(result_id, session['user_id'])
    if not result:
        flash('Result not found or access denied.', 'error')
        return redirect(url_for('quiz.quiz'))

    return render_template('results.html',
                         result=result,
                         score=result['score'],
                         total=result['num_questions'],
                         time=result['time_taken'],
                         exam_name=result['exam_name'],
                         subject_name=result['subject_name'],
                         date_taken=result['start_time'])

@quiz_bp.route('/api/subjects')
@login_required
@handle_quiz_error
def get_subjects():
    """API endpoint to get subjects for an exam."""
    exam_id = request.args.get('exam_id', type=int)
    if not exam_id:
        return jsonify({'error': 'Exam ID is required'}), 400

    subjects = QuizService.get_subjects_for_exam(exam_id)
    return jsonify(subjects)

@quiz_bp.route('/review_question/<int:qid>', methods=['POST'])
@login_required
@handle_quiz_error
def review_question(qid):
    """Handle question review submission."""
    comment = sanitize_input(request.form.get('comment', ''))
    rating = request.form.get('rating', type=int)
    
    if not rating or not (1 <= rating <= 5):
        flash('Rating must be between 1 and 5.', 'error')
        return redirect(request.referrer or url_for('quiz.quiz'))
    
    # Save review to database
    conn = current_app.config['db'].get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT INTO question_reviews 
            (question_id, user_id, comment, rating, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
            comment = VALUES(comment),
            rating = VALUES(rating),
            created_at = VALUES(created_at)
        ''', (qid, session['user_id'], comment, rating, datetime.now()))
        
        conn.commit()
        
        # Emit socket event for real-time updates
        current_app.socketio.emit('new_review', {
            'username': session['username'],
            'question_id': qid,
            'rating': rating
        }, namespace='/admin')
        
        flash('Your review has been submitted.', 'success')
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error saving review: {str(e)}")
        flash('Error submitting review.', 'error')
    finally:
        cursor.close()
        conn.close()
    
    return redirect(request.referrer or url_for('quiz.quiz')) 