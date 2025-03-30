from utils.db import get_db_connection
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def verify_subscriptions():
    conn = get_db_connection()
    if conn is None:
        logger.error("Failed to connect to database")
        return
    
    cursor = conn.cursor(dictionary=True)
    try:
        # Check subscription plans
        cursor.execute('SELECT * FROM subscription_plans WHERE is_institution = FALSE ORDER BY price')
        plans = cursor.fetchall()
        logger.info(f"Found {len(plans)} subscription plans:")
        for plan in plans:
            logger.info(f"Plan: {plan['name']}, Price: {plan['price']}, Degree: {plan['degree_access']}")
            
            # Check exam access for this plan
            cursor.execute('''
                SELECT e.name, e.degree_type 
                FROM plan_exam_access pea 
                JOIN exams e ON pea.exam_id = e.id 
                WHERE pea.plan_id = %s
            ''', (plan['id'],))
            exams = cursor.fetchall()
            logger.info(f"  Accessible exams ({len(exams)}):")
            for exam in exams:
                logger.info(f"    - {exam['name']} ({exam['degree_type']})")
            logger.info("---")
    
    except Exception as e:
        logger.error(f"Error verifying subscriptions: {str(e)}")
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    verify_subscriptions() 