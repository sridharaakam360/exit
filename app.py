from flask import Flask, render_template, redirect, url_for, request, session, flash
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO
import logging
from config import Config

# Initialize Flask app
app = Flask(__name__)
app.config.from_object(Config)

# Initialize extensions
csrf = CSRFProtect(app)
socketio = SocketIO(app, cors_allowed_origins="*")
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["6000 per day", "100 per hour"],
    storage_uri=app.config.get('REDIS_URL', "memory://"),
    storage_options={"socket_connect_timeout": 5},
    enabled=not app.debug
)

# Import routes after app is created
from routes.auth import create_auth_bp
from routes.admin import admin_bp
from routes.user import user_bp
from routes.quiz import quiz_bp
from routes.institution import institution_bp

# Create auth blueprint with limiter
auth_bp = create_auth_bp(limiter)

# Register blueprints
app.register_blueprint(auth_bp, url_prefix='/auth')
app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(user_bp, url_prefix='/user')
app.register_blueprint(quiz_bp, url_prefix='/quiz')
app.register_blueprint(institution_bp, url_prefix='/institution')

# Root route
@app.route('/')
def index():
    if 'user_id' in session and 'role' in session:
        if session['role'] == 'superadmin':
            return redirect(url_for('admin.admin_dashboard'))
        elif session['role'] == 'instituteadmin':
            return redirect(url_for('institution.institution_dashboard'))
        else:
            return redirect(url_for('user.user_dashboard'))
    return redirect(url_for('auth.choose_login'))

# Error handlers
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    logger = logging.getLogger(__name__)
    logger.error(f"Internal server error: {str(e)}")
    return render_template('500.html'), 500

if __name__ == '__main__':
    socketio.run(app, debug=True)