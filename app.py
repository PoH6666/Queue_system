from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import bcrypt
import secrets
from datetime import datetime
import os

app = Flask(__name__)
CORS(app)  # Allow mobile app to connect

# Database file
DATABASE = 'queue_system.db'


# ==================== DATABASE FUNCTIONS ====================

def get_db_connection():
    """Create database connection"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row  # Return rows as dictionaries
    return conn


def init_database():
    """Initialize database with tables"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            full_name TEXT NOT NULL,
            phone_number TEXT,
            email TEXT,
            role TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Queue table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ticket_number TEXT UNIQUE NOT NULL,
            queue_type TEXT DEFAULT 'general',
            status TEXT DEFAULT 'waiting',
            join_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            called_time TIMESTAMP,
            completed_time TIMESTAMP,
            position INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # Create default admin user (username: admin, password: admin123)
    admin_password = bcrypt.hashpw('admin123'.encode('utf-8'), bcrypt.gensalt())
    try:
        cursor.execute('''
            INSERT INTO users (username, password, full_name, role) 
            VALUES (?, ?, ?, ?)
        ''', ('admin', admin_password, 'System Admin', 'admin'))
        print("✅ Default admin created (username: admin, password: admin123)")
    except sqlite3.IntegrityError:
        print("ℹ️  Admin user already exists")

    conn.commit()
    conn.close()
    print("✅ Database initialized successfully!")


# ==================== HELPER FUNCTIONS ====================

def generate_ticket_number():
    """Generate unique ticket number"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Count waiting tickets today
    cursor.execute('''
        SELECT COUNT(*) as count FROM queue 
        WHERE date(join_time) = date('now') AND status = 'waiting'
    ''')
    count = cursor.fetchone()['count']
    conn.close()

    # Format: TICKET-001, TICKET-002, etc.
    return f"TICKET-{count + 1:03d}"


def update_queue_positions():
    """Recalculate positions for all waiting users"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get all waiting users ordered by join time
    cursor.execute('''
        SELECT id FROM queue 
        WHERE status = 'waiting' 
        ORDER BY join_time ASC
    ''')

    waiting_users = cursor.fetchall()

    # Update positions
    for idx, user in enumerate(waiting_users, start=1):
        cursor.execute('UPDATE queue SET position = ? WHERE id = ?', (idx, user['id']))

    conn.commit()
    conn.close()


# ==================== API ENDPOINTS ====================

@app.route('/', methods=['GET'])
def home():
    """Health check endpoint"""
    return jsonify({
        'status': 'online',
        'message': 'Queue System API is running!',
        'version': '1.0',
        'endpoints': [
            '/register', '/login', '/join_queue', '/queue_status',
            '/all_queues', '/call_next', '/leave_queue', '/queue_stats'
        ]
    })


@app.route('/register', methods=['POST'])
def register():
    """Register a new user"""
    try:
        data = request.json
        username = data.get('username')
        password = data.get('password')
        full_name = data.get('full_name')
        phone_number = data.get('phone_number', '')
        email = data.get('email', '')

        # Validation
        if not username or not password or not full_name:
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400

        # Hash password
        hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO users (username, password, full_name, phone_number, email) 
            VALUES (?, ?, ?, ?, ?)
        ''', (username, hashed, full_name, phone_number, email))

        conn.commit()
        user_id = cursor.lastrowid
        conn.close()

        return jsonify({
            'success': True,
            'message': 'User registered successfully!',
            'user_id': user_id,
            'username': username
        }), 201

    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': 'Username already exists'}), 409
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/login', methods=['POST'])
def login():
    """User login"""
    try:
        data = request.json
        username = data.get('username')
        password = data.get('password')

        if not username or not password:
            return jsonify({'success': False, 'error': 'Missing credentials'}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()
        conn.close()

        if not user:
            return jsonify({'success': False, 'error': 'Invalid username or password'}), 401

        # Verify password
        if bcrypt.checkpw(password.encode('utf-8'), user['password']):
            return jsonify({
                'success': True,
                'message': 'Login successful!',
                'user_id': user['id'],
                'username': user['username'],
                'full_name': user['full_name'],
                'role': user['role']
            }), 200
        else:
            return jsonify({'success': False, 'error': 'Invalid username or password'}), 401

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/join_queue', methods=['POST'])
def join_queue():
    """Join the queue"""
    try:
        data = request.json
        user_id = data.get('user_id')
        queue_type = data.get('queue_type', 'general')

        if not user_id:
            return jsonify({'success': False, 'error': 'User ID required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        # Check if user already in queue
        cursor.execute('''
            SELECT * FROM queue 
            WHERE user_id = ? AND status = 'waiting'
        ''', (user_id,))

        existing = cursor.fetchone()
        if existing:
            conn.close()
            return jsonify({
                'success': False,
                'error': 'You are already in the queue!',
                'ticket_number': existing['ticket_number'],
                'position': existing['position']
            }), 409

        # Generate ticket and add to queue
        ticket_number = generate_ticket_number()

        cursor.execute('''
            INSERT INTO queue (user_id, ticket_number, queue_type, status) 
            VALUES (?, ?, ?, 'waiting')
        ''', (user_id, ticket_number, queue_type))

        conn.commit()
        conn.close()

        # Update positions
        update_queue_positions()

        # Get position
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT position FROM queue WHERE ticket_number = ?', (ticket_number,))
        position = cursor.fetchone()['position']
        conn.close()

        return jsonify({
            'success': True,
            'message': 'Successfully joined the queue!',
            'ticket_number': ticket_number,
            'position': position,
            'queue_type': queue_type
        }), 201

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/queue_status', methods=['GET'])
def queue_status():
    """Get user's queue status"""
    try:
        user_id = request.args.get('user_id')

        if not user_id:
            return jsonify({'success': False, 'error': 'User ID required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT q.*, u.full_name, u.phone_number 
            FROM queue q
            JOIN users u ON q.user_id = u.id
            WHERE q.user_id = ? AND q.status = 'waiting'
        ''', (user_id,))

        queue_entry = cursor.fetchone()
        conn.close()

        if not queue_entry:
            return jsonify({
                'success': True,
                'in_queue': False,
                'message': 'You are not in the queue'
            }), 200

        return jsonify({
            'success': True,
            'in_queue': True,
            'ticket_number': queue_entry['ticket_number'],
            'position': queue_entry['position'],
            'status': queue_entry['status'],
            'queue_type': queue_entry['queue_type'],
            'join_time': queue_entry['join_time']
        }), 200

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/all_queues', methods=['GET'])
def all_queues():
    """Get all queue entries"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT q.*, u.full_name, u.phone_number 
            FROM queue q
            JOIN users u ON q.user_id = u.id
            WHERE q.status = 'waiting'
            ORDER BY q.position ASC
        ''')

        queues = cursor.fetchall()
        conn.close()

        queue_list = []
        for q in queues:
            queue_list.append({
                'id': q['id'],
                'ticket_number': q['ticket_number'],
                'full_name': q['full_name'],
                'phone_number': q['phone_number'],
                'position': q['position'],
                'queue_type': q['queue_type'],
                'join_time': q['join_time'],
                'status': q['status']
            })

        return jsonify({
            'success': True,
            'total_waiting': len(queue_list),
            'queue': queue_list
        }), 200

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/call_next', methods=['POST'])
def call_next():
    """Call next person in queue (Admin only)"""
    try:
        data = request.json
        admin_id = data.get('admin_id')

        # Verify admin (in production, use proper JWT tokens)
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT role FROM users WHERE id = ?', (admin_id,))
        admin = cursor.fetchone()

        if not admin or admin['role'] != 'admin':
            conn.close()
            return jsonify({'success': False, 'error': 'Admin access required'}), 403

        # Get first person in queue
        cursor.execute('''
            SELECT q.*, u.full_name 
            FROM queue q
            JOIN users u ON q.user_id = u.id
            WHERE q.status = 'waiting'
            ORDER BY q.position ASC
            LIMIT 1
        ''')

        next_person = cursor.fetchone()

        if not next_person:
            conn.close()
            return jsonify({
                'success': False,
                'message': 'Queue is empty'
            }), 404

        # Update status to completed (changed from in-progress)
        cursor.execute('''
            UPDATE queue 
            SET status = 'completed', called_time = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (next_person['id'],))

        conn.commit()
        conn.close()

        # Update positions
        update_queue_positions()

        return jsonify({
            'success': True,
            'message': 'Next person called!',
            'ticket_number': next_person['ticket_number'],
            'full_name': next_person['full_name']
        }), 200

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/leave_queue', methods=['DELETE'])
def leave_queue():
    """Leave the queue"""
    try:
        user_id = request.args.get('user_id')

        if not user_id:
            return jsonify({'success': False, 'error': 'User ID required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE queue 
            SET status = 'cancelled', completed_time = CURRENT_TIMESTAMP 
            WHERE user_id = ? AND status = 'waiting'
        ''', (user_id,))

        if cursor.rowcount == 0:
            conn.close()
            return jsonify({
                'success': False,
                'error': 'You are not in the queue'
            }), 404

        conn.commit()
        conn.close()

        # Update positions
        update_queue_positions()

        return jsonify({
            'success': True,
            'message': 'Successfully left the queue'
        }), 200

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/queue_stats', methods=['GET'])
def queue_stats():
    """Get queue statistics"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Total waiting
        cursor.execute('SELECT COUNT(*) as count FROM queue WHERE status = "waiting"')
        waiting = cursor.fetchone()['count']

        # Total completed today
        cursor.execute('''
            SELECT COUNT(*) as count FROM queue 
            WHERE status = "completed" AND date(completed_time) = date('now')
        ''')
        completed_today = cursor.fetchone()['count']

        # Total users
        cursor.execute('SELECT COUNT(*) as count FROM users WHERE role != "admin"')
        total_users = cursor.fetchone()['count']

        conn.close()

        return jsonify({
            'success': True,
            'waiting_in_queue': waiting,
            'completed_today': completed_today,
            'total_users': total_users
        }), 200

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== RUN SERVER ====================

if __name__ == '__main__':
    print("\n" + "=" * 50)
    print("🚀 Queue System Backend Server Starting...")
    print("=" * 50)
    
    # ALWAYS initialize database on startup (safe with IF NOT EXISTS)
    print("🔧 Initializing database...")
    try:
        init_database()
    except Exception as e:
        print(f"⚠️  Database initialization error: {e}")

    # Get port from environment (Railway/Render provides this)
    port = int(os.environ.get('PORT', 5000))

    print(f"🌐 Server running on port: {port}")
    print("=" * 50 + "\n")

    # Run Flask app (debug=False for production)
    app.run(debug=False, host='0.0.0.0', port=port)
