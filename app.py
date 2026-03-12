import os
from dotenv import load_dotenv
load_dotenv(override=True)  # MUST be before everything else

from flask import Flask, render_template, request, jsonify, session, redirect
from core.optimizer import ScheduleOptimizer

# ── MongoDB ───────────────────────────────────────────────────────────────
MONGO_URI = os.environ.get('MONGO_URI', '')
USE_MONGO  = False
users_col  = None

if MONGO_URI:
    try:
        from pymongo import MongoClient
        client = MongoClient(MONGO_URI,serverSelectionTimeoutMS=5000,tls=True,tlsAllowInvalidCertificates=True)
        client.admin.command('ping')   # test connection
        db        = client['gremlin']
        users_col = db['users']
        USE_MONGO = True
        print("✓ MongoDB connected")
    except Exception as e:
        print(f"✗ MongoDB failed: {e}")
        USE_MONGO = False
else:
    print("⚠ No MONGO_URI found — using in-memory fallback")

_mem_users = {}   # fallback

# ── DB helpers ────────────────────────────────────────────────────────────
def get_user(email):
    if USE_MONGO:
        return users_col.find_one({'email': email}, {'_id': 0})
    return _mem_users.get(email)

def user_exists(email):
    if USE_MONGO:
        return users_col.count_documents({'email': email}) > 0
    return email in _mem_users

def create_user(email, name, password):
    doc = {'email': email, 'name': name, 'password': password,
           'profile': None, 'schedule': None}
    if USE_MONGO:
        users_col.insert_one(doc)
    else:
        _mem_users[email] = doc

def update_user(email, fields):
    if USE_MONGO:
        users_col.update_one({'email': email}, {'$set': fields})
    else:
        if email in _mem_users:
            _mem_users[email].update(fields)

# ── App ───────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'gremlin_dev_2024')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/onboarding')
def onboarding():
    if 'user' not in session:
        return redirect('/')
    return render_template('onboarding.html')

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect('/')
    return render_template('dashboard.html')

@app.route('/api/signup', methods=['POST'])
def signup():
    d     = request.get_json() or {}
    name  = d.get('name', '').strip()
    email = d.get('email', '').strip().lower()
    pw    = d.get('password', '').strip()
    if not all([name, email, pw]):
        return jsonify({'error': 'All fields required'}), 400
    if len(pw) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    if user_exists(email):
        return jsonify({'error': 'Email already registered'}), 400
    create_user(email, name, pw)
    session['user'] = email
    session['name'] = name
    return jsonify({'success': True})

@app.route('/api/signin', methods=['POST'])
def signin():
    d     = request.get_json() or {}
    email = d.get('email', '').strip().lower()
    pw    = d.get('password', '').strip()
    u     = get_user(email)
    if not u or u.get('password') != pw:
        return jsonify({'error': 'Invalid email or password'}), 401
    session['user'] = email
    session['name'] = u.get('name', 'Student')
    return jsonify({'success': True, 'has_profile': u.get('profile') is not None})

@app.route('/api/logout')
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/save_profile', methods=['POST'])
def save_profile():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data  = request.get_json() or {}
    email = session['user']
    try:
        schedule = ScheduleOptimizer(data).optimize()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    update_user(email, {'profile': data, 'schedule': schedule})
    return jsonify({'success': True, 'schedule': schedule})

@app.route('/api/get_schedule')
def get_schedule():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    u = get_user(session['user'])
    if not u:
        return jsonify({'error': 'User not found'}), 404
    return jsonify({'schedule': u.get('schedule'), 'profile': u.get('profile'),
                    'name': u.get('name', 'Student')})

@app.route('/api/mood_checkin', methods=['POST'])
def mood_checkin():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    d       = request.get_json() or {}
    email   = session['user']
    u       = get_user(email)
    profile = u.get('profile') if u else None
    if not profile:
        return jsonify({'success': False, 'error': 'No profile'}), 400
    profile = dict(profile)
    profile['mood_override']   = max(1, min(10, int(d.get('mood', 7))))
    profile['energy_override'] = max(1, min(10, int(d.get('energy', 7))))
    try:
        schedule = ScheduleOptimizer(profile).optimize()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    update_user(email, {'schedule': schedule, 'profile': profile})
    m, e = profile['mood_override'], profile['energy_override']
    msg = ("I've lightened the load. Rest counts." if m<=3 or e<=3 else
           "Balanced day — you've got this." if m<=6 or e<=6 else
           "You're on fire! Hard subjects first.")
    return jsonify({'success': True, 'schedule': schedule, 'message': msg})

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'mongo': USE_MONGO})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=os.environ.get('FLASK_ENV') != 'production', port=port)