from typing import List, Optional, Dict, Any
from datetime import datetime
import logging
from mysql.connector import Error
from ..models.quiz import Quiz, Question
from utils.db import get_db_connection
from utils.security import get_user_role, get_user_subscription

logger = logging.getLogger(__name__)

class QuizService:
    @staticmethod
    def get_accessible_exams(user_id: int) -> List[Dict[str, Any]]:
        """Get exams accessible to the user based on their role and subscription."""
        conn = get_db_connection()
        if not conn:
            raise Exception("Database connection failed")

        cursor = conn.cursor(dictionary=True)
        try:
            user_role = get_user_role(user_id)
            
            if user_role == 'superadmin':
                cursor.execute('SELECT * FROM exams ORDER BY name')
                return cursor.fetchall()
            
            elif user_role == 'individual':
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
                ''', (user_id,))
                return cursor.fetchall()
            
            else:  # student
                cursor.execute('''
                    SELECT DISTINCT e.*
                    FROM exams e
                    JOIN plan_exam_access pea ON e.id = pea.exam_id
                    JOIN institution_students ist ON ist.institution_id = pea.plan_id
                    JOIN institutions i ON ist.institution_id = i.id
                    JOIN subscription_plans sp ON i.subscription_plan_id = sp.id
                    WHERE ist.user_id = %s
                    AND DATE(i.subscription_end) >= CURDATE()
                    AND (
                        sp.degree_access = 'both'
                        OR e.degree_type = sp.degree_access
                    )
                    ORDER BY e.name
                ''', (user_id,))
                return cursor.fetchall()

        except Error as err:
            logger.error(f"Database error in get_accessible_exams: {str(err)}")
            raise
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_subjects_for_exam(exam_id: int) -> List[Dict[str, Any]]:
        """Get subjects available for a specific exam."""
        conn = get_db_connection()
        if not conn:
            raise Exception("Database connection failed")

        cursor = conn.cursor(dictionary=True)
        try:
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
            return cursor.fetchall()
        except Error as err:
            logger.error(f"Database error in get_subjects_for_exam: {str(err)}")
            raise
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def generate_quiz(user_id: int, exam_id: int, subject_id: Optional[int],
                     quiz_type: str, difficulty: Optional[str], num_questions: int) -> Quiz:
        """Generate a new quiz based on the specified parameters."""
        conn = get_db_connection()
        if not conn:
            raise Exception("Database connection failed")

        cursor = conn.cursor(dictionary=True)
        try:
            # Build the query based on quiz type and parameters
            query = '''
                SELECT q.*, s.name AS subject_name, s.exam_id 
                FROM questions q 
                JOIN subjects s ON q.subject_id = s.id
                WHERE 1=1
            '''
            params = []

            if quiz_type == 'previous_year':
                query += ' AND s.exam_id = %s AND q.is_previous_year = TRUE'
                params.append(exam_id)
            elif quiz_type == 'subject_wise' and subject_id:
                query += ' AND q.subject_id = %s'
                params.append(subject_id)

            if difficulty:
                query += ' AND q.difficulty = %s'
                params.append(difficulty)

            # Add subscription-based filters
            user_role = get_user_role(user_id)
            subscription = get_user_subscription(user_id)
            
            if user_role == 'individual' and subscription:
                if subscription['name'] == 'Basic Package':
                    query += ' AND q.is_previous_year = TRUE'

            query += ' ORDER BY RAND() LIMIT %s'
            params.append(num_questions)

            cursor.execute(query, params)
            questions_data = cursor.fetchall()

            if not questions_data:
                raise Exception("No questions found matching the criteria")

            # Create Question objects
            questions = [
                Question(
                    id=q['id'],
                    question=q['question'],
                    option_a=q['option_a'],
                    option_b=q['option_b'],
                    option_c=q['option_c'],
                    option_d=q['option_d'],
                    correct_answer=q['correct_answer'],
                    explanation=q.get('explanation'),
                    difficulty=q['difficulty'],
                    subject_id=q['subject_id'],
                    is_previous_year=q['is_previous_year']
                )
                for q in questions_data
            ]

            # Create and return Quiz object
            return Quiz.create_new(
                user_id=user_id,
                exam_id=exam_id,
                subject_id=subject_id,
                quiz_type=quiz_type,
                difficulty=difficulty,
                num_questions=num_questions
            )

        except Error as err:
            logger.error(f"Database error in generate_quiz: {str(err)}")
            raise
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def save_quiz_result(quiz: Quiz) -> int:
        """Save quiz result to database and return the result ID."""
        conn = get_db_connection()
        if not conn:
            raise Exception("Database connection failed")

        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO quiz_attempts 
                (user_id, exam_id, subject_id, quiz_type, difficulty, 
                num_questions, score, time_taken, answers, start_time, end_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                quiz.user_id,
                quiz.exam_id,
                quiz.subject_id,
                quiz.quiz_type,
                quiz.difficulty,
                quiz.num_questions,
                quiz.score,
                quiz.time_taken,
                quiz.answers,
                quiz.start_time,
                quiz.end_time
            ))
            conn.commit()
            return cursor.lastrowid

        except Error as err:
            conn.rollback()
            logger.error(f"Database error in save_quiz_result: {str(err)}")
            raise
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_quiz_result(result_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve a specific quiz result."""
        conn = get_db_connection()
        if not conn:
            raise Exception("Database connection failed")

        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute('''
                SELECT qa.*, e.name AS exam_name, s.name AS subject_name
                FROM quiz_attempts qa
                LEFT JOIN exams e ON qa.exam_id = e.id
                LEFT JOIN subjects s ON qa.subject_id = s.id
                WHERE qa.id = %s AND qa.user_id = %s
            ''', (result_id, user_id))
            return cursor.fetchone()

        except Error as err:
            logger.error(f"Database error in get_quiz_result: {str(err)}")
            raise
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_user_quiz_stats(user_id: int) -> Dict[str, Any]:
        """Get user's quiz statistics."""
        conn = get_db_connection()
        if not conn:
            raise Exception("Database connection failed")

        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_quizzes,
                    COUNT(CASE WHEN score >= 60 THEN 1 END) as passed_quizzes,
                    COUNT(CASE WHEN score < 60 THEN 1 END) as failed_quizzes,
                    AVG(score) as average_score
                FROM quiz_attempts
                WHERE user_id = %s
            ''', (user_id,))
            return cursor.fetchone() or {
                'total_quizzes': 0,
                'passed_quizzes': 0,
                'failed_quizzes': 0,
                'average_score': 0
            }

        except Error as err:
            logger.error(f"Database error in get_user_quiz_stats: {str(err)}")
            raise
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_recent_attempts(user_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        """Get user's recent quiz attempts."""
        conn = get_db_connection()
        if not conn:
            raise Exception("Database connection failed")

        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute('''
                SELECT qa.*, e.name AS exam_name, s.name AS subject_name
                FROM quiz_attempts qa
                LEFT JOIN exams e ON qa.exam_id = e.id
                LEFT JOIN subjects s ON qa.subject_id = s.id
                WHERE qa.user_id = %s
                ORDER BY qa.start_time DESC
                LIMIT %s
            ''', (user_id, limit))
            return cursor.fetchall()

        except Error as err:
            logger.error(f"Database error in get_recent_attempts: {str(err)}")
            raise
        finally:
            cursor.close()
            conn.close() 