from flask import Blueprint, render_template, redirect, url_for, flash, session, request, Response
from flask_login import current_user, login_required
from utils.db import get_db_connection
from forms import SubscriptionForm
from datetime import datetime, timedelta
import logging
import csv
from io import StringIO
from mysql.connector import Error

user_bp = Blueprint('user', __name__)
logger = logging.getLogger(__name__)

@user_bp.route('/dashboard')
@login_required
def user_dashboard():
    conn = get_db_connection()
    if conn is None:
        flash('Database connection error.', 'danger')
        return redirect(url_for('index'))
    
    cursor = conn.cursor(dictionary=True)
    try:
        # Get user's subscription status
        cursor.execute('''
            SELECT sp.name as plan_name, sp.degree_access, 
                   u.subscription_start, u.subscription_end
            FROM users u
            LEFT JOIN subscription_plans sp ON u.subscription_plan_id = sp.id
            WHERE u.id = %s
        ''', (current_user.id,))
        subscription = cursor.fetchone()
        
        # Get available subjects based on subscription
        if subscription and subscription['degree_access']:
            cursor.execute('''
                SELECT s.id, s.name, s.degree_type
                FROM subjects s
                WHERE s.degree_type IN %s
                ORDER BY s.name
            ''', (tuple(subscription['degree_access'].split(',')),))
        else:
            cursor.execute('''
                SELECT s.id, s.name, s.degree_type
                FROM subjects s
                WHERE s.degree_type = 'free'
                ORDER BY s.name
            ''')
        subjects = cursor.fetchall()
        
        # Get recent activity
        cursor.execute('''
            SELECT q.id, q.question, q.chapter, q.difficulty, 
                   s.name as subject_name, q.created_at
            FROM questions q
            JOIN subjects s ON q.subject_id = s.id
            WHERE q.created_by = %s
            ORDER BY q.created_at DESC
            LIMIT 5
        ''', (current_user.id,))
        recent_activity = cursor.fetchall()
        
        return render_template('user/user_dashboard.html',
                             subscription=subscription,
                             subjects=subjects,
                             recent_activity=recent_activity)
    except Error as err:
        logger.error(f"Database error in user_dashboard: {str(err)}")
        flash('Error loading dashboard.', 'danger')
        return redirect(url_for('index'))
    finally:
        cursor.close()
        conn.close()

@user_bp.route('/export_dashboard/<format>')
@login_required
def export_user_dashboard(format):
    if format not in ['csv', 'pdf']:
        flash('Unsupported export format.', 'danger')
        return redirect(url_for('user.user_dashboard'))
        
    if format == 'pdf':
        flash('PDF export functionality is coming soon.', 'info')
        return redirect(url_for('user.user_dashboard'))
        
    conn = get_db_connection()
    if conn is None:
        flash('Database connection error.', 'danger')
        return redirect(url_for('user.user_dashboard'))
        
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute('''SELECT r.id, r.score, r.total_questions, r.time_taken, r.date_taken, e.name as exam_name
            FROM results r 
            LEFT JOIN exams e ON r.exam_id = e.id
            WHERE r.user_id = %s 
            ORDER BY r.date_taken DESC''',
            (current_user.id,))
        results = cursor.fetchall()
        
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(['Result ID', 'Exam', 'Score', 'Total Questions', 'Percentage', 'Time Taken (seconds)', 'Date Taken'])
        
        for result in results:
            percentage = round((result['score'] / result['total_questions']) * 100, 1) if result['total_questions'] > 0 else 0
            writer.writerow([
                result['id'], 
                result['exam_name'] or 'Unknown', 
                result['score'], 
                result['total_questions'], 
                f"{percentage}%",
                result['time_taken'], 
                result['date_taken'].strftime("%Y-%m-%d %H:%M:%S")
            ])
            
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename=user_results_{current_user.username}_{datetime.now().strftime("%Y%m%d")}.csv'}
        )
    except Error as err:
        logger.error(f"Database error during export: {str(err)}")
        flash('Error exporting data.', 'danger')
        return redirect(url_for('user.user_dashboard'))
    finally:
        cursor.close()
        conn.close()

@user_bp.route('/subscriptions')
@login_required
def subscriptions():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get all subscription plans with complete details
        cursor.execute('''
            SELECT id, name, price, duration_months, description, 
                   degree_access, includes_previous_years, 
                   is_institution, student_range, custom_student_range
            FROM subscription_plans
            WHERE is_institution = FALSE
            ORDER BY price ASC
        ''')
        plans = cursor.fetchall()
        
        # Get user's current subscription if any
        cursor.execute('''
            SELECT sp.*, u.subscription_start, u.subscription_end, u.subscription_status
            FROM users u
            LEFT JOIN subscription_plans sp ON u.subscription_plan_id = sp.id
            WHERE u.id = %s
        ''', (current_user.id,))
        current_subscription = cursor.fetchone()

        return render_template('subscriptions.html',
                             plans=plans,
                             current_subscription=current_subscription)
    except Exception as e:
        logger.error(f"Error loading subscription plans: {str(e)}")
        flash('Error loading subscription plans.', 'error')
        return redirect(url_for('user.user_dashboard'))
    finally:
        if 'conn' in locals():
            conn.close()

@user_bp.route('/subscribe/<int:plan_id>', methods=['GET', 'POST'])
@login_required
def subscribe(plan_id):
    conn = None
    cursor = None
    
    try:
        conn = get_db_connection()
        if conn is None:
            flash('Database connection error.', 'danger')
            return redirect(url_for('user.subscriptions'))
        
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute('SELECT * FROM subscription_plans WHERE id = %s AND is_institution = FALSE', 
                      (plan_id,))
        plan = cursor.fetchone()
        
        if not plan:
            flash('Invalid subscription plan.', 'danger')
            return redirect(url_for('user.subscriptions'))
            
        form = SubscriptionForm()
        form.plan_id.choices = [(plan['id'], plan['name'])]
        
        cursor.execute('''SELECT e.id, e.name FROM plan_exam_access pea 
                        JOIN exams e ON pea.exam_id = e.id 
                        WHERE pea.plan_id = %s''', (plan_id,))
        plan['exams'] = [{'id': row['id'], 'name': row['name']} for row in cursor.fetchall()]
        
        if form.validate_on_submit():
            start_date = datetime.now().date()
            end_date = start_date + timedelta(days=plan['duration_months'])
                        
            cursor.execute('''UPDATE users 
                SET subscription_plan_id = %s,
                    subscription_start = %s,
                    subscription_end = %s,
                    subscription_status = %s,
                    last_active = %s
                WHERE id = %s''', (plan_id, start_date, end_date, 'active', datetime.now(), current_user.id))
            
            cursor.execute('''INSERT INTO subscription_history 
                (user_id, subscription_plan_id, start_date, end_date, amount_paid, payment_method) 
                VALUES (%s, %s, %s, %s, %s, %s)''', (current_user.id, plan_id, start_date, end_date, plan['price'], form.payment_method.data))
            
            conn.commit()
            flash(f'You have successfully subscribed to {plan["name"]}!', 'success')
            return redirect(url_for('user.subscriptions'))
        
        return render_template('subscribe.html', form=form, plan=plan)
    
    except Error as err:
        if conn:
            try:
                conn.rollback()
            except Error as rollback_err:
                logger.error(f"Rollback failed: {str(rollback_err)}")
        
        logger.error(f"Database error during subscription: {str(err)}")
        flash('Error processing subscription. Please try again.', 'danger')
        return redirect(url_for('user.subscriptions'))
    
    finally:
        if cursor:
            cursor.close()
        if conn:
            try:
                conn.close()
            except Error as close_err:
                logger.error(f"Connection close failed: {str(close_err)}")

@user_bp.route('/subscription_history')
@login_required
def subscription_history():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute('''SELECT u.subscription_plan_id, u.subscription_start, u.subscription_end, 
                   p.name as plan_name, p.price, p.description
            FROM users u
            JOIN subscription_plans p ON u.subscription_plan_id = p.id
            WHERE u.id = %s AND u.subscription_end > CURDATE() 
                AND u.subscription_plan_id IS NOT NULL''', (current_user.id,))
        active_sub = cursor.fetchone()
        
        if active_sub:
            cursor.execute('''SELECT COUNT(*) as count FROM subscription_history
                WHERE user_id = %s 
                AND subscription_plan_id = %s
                AND start_date = %s''', (current_user.id, active_sub['subscription_plan_id'], active_sub['subscription_start']))
            
            history_exists = cursor.fetchone()['count'] > 0
            
            if not history_exists:
                cursor.execute('''INSERT INTO subscription_history 
                    (user_id, subscription_plan_id, start_date, end_date, amount_paid, payment_method) 
                    VALUES (%s, %s, %s, %s, %s, %s)''', (
                    current_user.id, 
                    active_sub['subscription_plan_id'], 
                    active_sub['subscription_start'], 
                    active_sub['subscription_end'], 
                    active_sub['price'], 
                    'credit_card'
                ))
                conn.commit()
        
        cursor.execute('''SELECT sh.id, sh.start_date, sh.end_date, sh.amount_paid, sh.payment_method,
                   p.name as plan_name, p.description
            FROM subscription_history sh
            JOIN subscription_plans p ON sh.subscription_plan_id = p.id
            WHERE sh.user_id = %s
            ORDER BY sh.start_date DESC''', (current_user.id,))
        history = cursor.fetchall()
        
        # Convert all dates to datetime objects for consistent comparison
        current_time = datetime.now().date()
        for entry in history:
            if isinstance(entry['start_date'], datetime):
                entry['start_date'] = entry['start_date'].date()
            if isinstance(entry['end_date'], datetime):
                entry['end_date'] = entry['end_date'].date()
        
        return render_template('subscription_history.html', 
                             history=history, 
                             now=current_time)
    except Error as err:
        logger.error(f"Database error in subscription history: {str(err)}")
        flash('Error retrieving subscription history.', 'danger')
        return redirect(url_for('user.user_dashboard'))
    finally:
        cursor.close()
        conn.close()