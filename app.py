from sms_service import send_attendance_alert, send_absence_warning
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from flask_bcrypt import Bcrypt
from models import db, Student, Lecturer, Attendance, ClassSession, Faculty, Department, Course, LecturerCourse, Admin
from datetime import datetime, timedelta
import os, io, csv, random, string

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'smart_attendance_dev_2024')
bcrypt = Bcrypt(app)

DB_PASSWORD = os.environ.get('DB_PASSWORD', '123Qwerty?')
app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+mysqlconnector://root:{DB_PASSWORD}@localhost/smart_attendance'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

with app.app_context():
    db.create_all()

def haversine(lat1, lon1, lat2, lon2):
    from math import radians, sin, cos, sqrt, atan2
    R = 6371000
    dlat = radians(lat2-lat1); dlon = radians(lon2-lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return R*2*atan2(sqrt(a), sqrt(1-a))

def generate_token():
    return ''.join(random.choices(string.digits, k=6))

@app.route('/api/faculties')
def api_faculties():
    return jsonify([{'id': f.id, 'name': f.name} for f in Faculty.query.order_by(Faculty.name).all()])

@app.route('/api/departments/<int:faculty_id>')
def api_departments(faculty_id):
    return jsonify([{'id': d.id, 'name': d.name} for d in Department.query.filter_by(faculty_id=faculty_id).order_by(Department.name).all()])

@app.route('/api/courses/<int:department_id>/<int:level>')
def api_courses(department_id, level):
    courses = Course.query.filter_by(department_id=department_id, level=level).order_by(Course.code).all()
    return jsonify([{'id': c.id, 'code': c.code, 'title': c.title, 'semester': c.semester} for c in courses])

@app.route('/api/my_courses')
def api_my_courses():
    if 'lecturer_id' not in session: return jsonify([])
    return jsonify([{'id': lc.course.id, 'code': lc.course.code, 'title': lc.course.title, 'level': lc.course.level}
                    for lc in LecturerCourse.query.filter_by(lecturer_id=session['lecturer_id']).all()])

@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        matric_no = request.form['matric_no']
        password = request.form['password']
        device_hash = request.form.get('device_hash', '')
        student = Student.query.filter_by(matric_no=matric_no).first()
        if not student or not bcrypt.check_password_hash(student.password, password):
            return render_template('login.html', error='Invalid matric number or password')
        if student.device_hash and student.device_hash != device_hash:
            return render_template('login.html', error='Account bound to a different device. Contact your lecturer.')
        session['student_id'] = student.id
        session['student_name'] = student.full_name
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        full_name = request.form['full_name']
        matric_no = request.form['matric_no']
        email = request.form['email']
        password = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')
        parent_phone = request.form['parent_phone']
        device_hash = request.form.get('device_hash', '')
        faculty_id = request.form.get('faculty_id')
        department_id = request.form.get('department_id')
        level = request.form.get('level')
        if Student.query.filter_by(matric_no=matric_no).first():
            return render_template('register.html', error='Matric number already exists')
        if device_hash and Student.query.filter_by(device_hash=device_hash).first():
            return render_template('register.html', error='This device is already registered to another account.')
        student = Student(full_name=full_name, matric_no=matric_no, email=email, password=password,
                          parent_phone=parent_phone, assigned_pattern='circle', device_hash=device_hash,
                          faculty_id=faculty_id, department_id=department_id, level=int(level) if level else None)
        db.session.add(student)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    if 'student_id' not in session: return redirect(url_for('login'))
    student = Student.query.get(session['student_id'])
    if request.method == 'POST':
        if not bcrypt.check_password_hash(student.password, request.form['old_password']):
            return render_template('change_password.html', error='Current password is incorrect', role='student')
        student.password = bcrypt.generate_password_hash(request.form['new_password']).decode('utf-8')
        db.session.commit()
        return render_template('change_password.html', success='Password changed successfully!', role='student')
    return render_template('change_password.html', role='student')

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        step = request.form.get('step', '1')
        matric_no = request.form.get('matric_no', '')
        email = request.form.get('email', '')
        token = request.form.get('token', '')
        if step == '1':
            student = Student.query.filter_by(matric_no=matric_no, email=email).first()
            if not student:
                return render_template('forgot_password.html', step='1', error='Matric number and email do not match.')
            t = generate_token()
            student.reset_token = t
            student.reset_token_expiry = datetime.utcnow() + timedelta(minutes=15)
            db.session.commit()
            return render_template('forgot_password.html', step='2', matric_no=matric_no, email=email, test_token=t,
                                   info=f'Reset code generated. (In production, sent to {email})')
        elif step == '2':
            student = Student.query.filter_by(matric_no=matric_no).first()
            if not student or student.reset_token != token or student.reset_token_expiry < datetime.utcnow():
                return render_template('forgot_password.html', step='2', matric_no=matric_no, email=email, error='Invalid or expired code.')
            student.password = bcrypt.generate_password_hash(request.form['new_password']).decode('utf-8')
            student.reset_token = None; student.reset_token_expiry = None
            db.session.commit()
            return redirect(url_for('login'))
    return render_template('forgot_password.html', step='1')

@app.route('/dashboard')
def dashboard():
    if 'student_id' not in session: return redirect(url_for('login'))
    student = Student.query.get(session['student_id'])
    attendances = Attendance.query.filter_by(student_id=student.id).order_by(Attendance.timestamp.desc()).all()
    course_stats = {}
    for a in attendances:
        key = a.course_code
        if key not in course_stats:
            course_stats[key] = {'present': 0, 'absent': 0, 'sessions': []}
        course_stats[key][a.status] += 1
        course_stats[key]['sessions'].append(a)
    for key in course_stats:
        total = course_stats[key]['present'] + course_stats[key]['absent']
        course_stats[key]['total'] = total
        course_stats[key]['rate'] = round(course_stats[key]['present']/total*100) if total > 0 else 0
    return render_template('dashboard.html', student=student, attendances=attendances, course_stats=course_stats)

@app.route('/course_attendance/<course_code>')
def course_attendance(course_code):
    if 'student_id' not in session: return redirect(url_for('login'))
    student = Student.query.get(session['student_id'])
    attendances = Attendance.query.filter_by(student_id=student.id, course_code=course_code).order_by(Attendance.timestamp.asc()).all()
    total = len(attendances)
    present = len([a for a in attendances if a.status == 'present'])
    rate = round(present/total*100) if total > 0 else 0
    return render_template('course_attendance.html', student=student, course_code=course_code,
                           attendances=attendances, total=total, present=present, absent=total-present, rate=rate)

@app.route('/mark_attendance', methods=['GET', 'POST'])
def mark_attendance():
    if 'student_id' not in session: return redirect(url_for('login'))
    student = Student.query.get(session['student_id'])
    if request.method == 'POST':
        data = request.get_json()
        pattern_valid = data.get('pattern_valid', False)
        device_hash = data.get('device_hash', '')
        session_id = data.get('session_id')
        student_lat = data.get('student_lat')
        student_lng = data.get('student_lng')

        device_valid = False
        if student.device_hash is None:
            student.device_hash = device_hash; db.session.commit(); device_valid = True
        elif student.device_hash == device_hash:
            device_valid = True

        active_session = ClassSession.query.get(session_id) if session_id else \
                         ClassSession.query.filter_by(is_active=True).order_by(ClassSession.started_at.desc()).first()
        if not active_session or not active_session.is_active:
            return jsonify({'success': False, 'message': 'No active session found'})

        already = Attendance.query.filter_by(student_id=student.id, session_id=active_session.id).first()
        if already:
            return jsonify({'success': False, 'message': 'Already marked for this session'})

        server_location_valid = False
        if student_lat and student_lng:
            dist = haversine(float(student_lat), float(student_lng), active_session.lat, active_session.lng)
            server_location_valid = dist <= 50

        if pattern_valid and server_location_valid and device_valid:
            course = active_session.course
            att = Attendance(student_id=student.id, session_id=active_session.id, course_id=course.id,
                             course_code=course.code, status='present', location_valid=server_location_valid,
                             device_valid=device_valid, pattern_valid=pattern_valid)
            db.session.add(att); db.session.commit()
            try: send_attendance_alert(student.full_name, student.parent_phone, course.code, 'present', att.timestamp)
            except Exception as e: print(f"SMS error: {e}")
            return jsonify({'success': True, 'message': f'Attendance marked for {course.code}!'})
        else:
            reasons = []
            if not pattern_valid: reasons.append('Invalid pattern')
            if not server_location_valid: reasons.append('Not within classroom range')
            if not device_valid: reasons.append('Unrecognized device')
            return jsonify({'success': False, 'message': ', '.join(reasons)})
    return render_template('mark_attendance.html', student=student)

@app.route('/get_session')
def get_session():
    active = ClassSession.query.filter_by(is_active=True).order_by(ClassSession.started_at.desc()).first()
    if active:
        return jsonify({'success': True, 'session_id': active.id, 'pattern': active.pattern,
                        'lat': active.lat, 'lng': active.lng, 'course_code': active.course.code, 'course_title': active.course.title})
    return jsonify({'success': False, 'message': 'No active session'})

@app.route('/lecturer/login', methods=['GET', 'POST'])
def lecturer_login():
    if request.method == 'POST':
        lecturer = Lecturer.query.filter_by(email=request.form['email']).first()
        if lecturer and bcrypt.check_password_hash(lecturer.password, request.form['password']):
            session['lecturer_id'] = lecturer.id; session['lecturer_name'] = lecturer.full_name
            return redirect(url_for('lecturer_dashboard'))
        return render_template('lecturer_login.html', error='Invalid email or password')
    return render_template('lecturer_login.html')

@app.route('/lecturer/register', methods=['GET', 'POST'])
def lecturer_register():
    if request.method == 'POST':
        email = request.form['email']
        if Lecturer.query.filter_by(email=email).first():
            return render_template('lecturer_register.html', error='Email already exists')
        password = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')
        lecturer = Lecturer(full_name=request.form['full_name'], email=email, password=password,
                            department_id=request.form.get('department_id') or None)
        db.session.add(lecturer); db.session.flush()
        for cid in request.form.getlist('course_ids'):
            db.session.add(LecturerCourse(lecturer_id=lecturer.id, course_id=int(cid)))
        db.session.commit()
        return redirect(url_for('lecturer_login'))
    return render_template('lecturer_register.html')

@app.route('/lecturer/logout')
def lecturer_logout():
    session.clear(); return redirect(url_for('lecturer_login'))

@app.route('/lecturer/change_password', methods=['GET', 'POST'])
def lecturer_change_password():
    if 'lecturer_id' not in session: return redirect(url_for('lecturer_login'))
    lecturer = Lecturer.query.get(session['lecturer_id'])
    if request.method == 'POST':
        if not bcrypt.check_password_hash(lecturer.password, request.form['old_password']):
            return render_template('change_password.html', error='Current password is incorrect', role='lecturer')
        lecturer.password = bcrypt.generate_password_hash(request.form['new_password']).decode('utf-8')
        db.session.commit()
        return render_template('change_password.html', success='Password changed!', role='lecturer')
    return render_template('change_password.html', role='lecturer')

@app.route('/lecturer/forgot_password', methods=['GET', 'POST'])
def lecturer_forgot_password():
    if request.method == 'POST':
        step = request.form.get('step', '1')
        email = request.form.get('email', '')
        token = request.form.get('token', '')
        if step == '1':
            lecturer = Lecturer.query.filter_by(email=email).first()
            if not lecturer:
                return render_template('forgot_password.html', step='1', error='Email not found.', role='lecturer')
            t = generate_token()
            lecturer.reset_token = t; lecturer.reset_token_expiry = datetime.utcnow() + timedelta(minutes=15)
            db.session.commit()
            return render_template('forgot_password.html', step='2', email=email, test_token=t,
                                   info=f'Code generated. (In production, sent to {email})', role='lecturer')
        elif step == '2':
            lecturer = Lecturer.query.filter_by(email=email).first()
            if not lecturer or lecturer.reset_token != token or lecturer.reset_token_expiry < datetime.utcnow():
                return render_template('forgot_password.html', step='2', email=email, error='Invalid or expired code.', role='lecturer')
            lecturer.password = bcrypt.generate_password_hash(request.form['new_password']).decode('utf-8')
            lecturer.reset_token = None; lecturer.reset_token_expiry = None; db.session.commit()
            return redirect(url_for('lecturer_login'))
    return render_template('forgot_password.html', step='1', role='lecturer')

@app.route('/lecturer/dashboard')
def lecturer_dashboard():
    if 'lecturer_id' not in session: return redirect(url_for('lecturer_login'))
    lecturer = Lecturer.query.get(session['lecturer_id'])
    students = Student.query.all()
    my_courses = [lc.course for lc in lecturer.lecturer_courses]
    course_ids = [c.id for c in my_courses]
    attendances = Attendance.query.filter(Attendance.course_id.in_(course_ids)).order_by(Attendance.timestamp.desc()).all() if course_ids else []
    return render_template('lecturer_dashboard.html', lecturer=lecturer, students=students, attendances=attendances, my_courses=my_courses)

@app.route('/lecturer/set_session', methods=['POST'])
def set_session():
    if 'lecturer_id' not in session: return jsonify({'success': False})
    data = request.get_json()
    pattern = data.get('pattern'); lat = data.get('lat'); lng = data.get('lng'); course_id = data.get('course_id')
    for s in ClassSession.query.filter_by(lecturer_id=session['lecturer_id'], is_active=True).all():
        s.is_active = False; s.ended_at = datetime.utcnow()
    if pattern and lat and lng and course_id:
        db.session.add(ClassSession(lecturer_id=session['lecturer_id'], course_id=int(course_id),
                                    pattern=pattern, lat=lat, lng=lng, is_active=True, started_at=datetime.utcnow()))
        Student.query.update({'assigned_pattern': pattern})
    db.session.commit()
    return jsonify({'success': True})

@app.route('/lecturer/mark_absent', methods=['POST'])
def mark_absent():
    if 'lecturer_id' not in session: return jsonify({'success': False})
    data = request.get_json()
    session_id = data.get('session_id')
    active_session = ClassSession.query.get(session_id) if session_id else \
                     ClassSession.query.filter_by(lecturer_id=session['lecturer_id'], is_active=True).first()
    if not active_session: return jsonify({'success': False, 'message': 'No active session'})
    absent_count = 0
    for student in Student.query.all():
        if not Attendance.query.filter_by(student_id=student.id, session_id=active_session.id).first():
            db.session.add(Attendance(student_id=student.id, session_id=active_session.id,
                                      course_id=active_session.course_id, course_code=active_session.course.code,
                                      status='absent', location_valid=False, device_valid=False, pattern_valid=False))
            absent_count += 1
            try: send_attendance_alert(student.full_name, student.parent_phone, active_session.course.code, 'absent', datetime.utcnow())
            except: pass
    active_session.is_active = False; active_session.ended_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'message': f'{absent_count} students marked absent. Session ended.'})

@app.route('/lecturer/search_student')
def search_student():
    if 'lecturer_id' not in session: return jsonify({'success': False})
    matric_no = request.args.get('matric_no', '')
    course_id = request.args.get('course_id')
    student = Student.query.filter_by(matric_no=matric_no).first()
    if not student: return jsonify({'success': False, 'message': 'Student not found'})
    query = Attendance.query.filter_by(student_id=student.id)
    if course_id: query = query.filter_by(course_id=int(course_id))
    attendances = query.order_by(Attendance.timestamp.asc()).all()
    total = len(attendances); present = len([a for a in attendances if a.status == 'present'])
    dept = Department.query.get(student.department_id) if student.department_id else None
    fac = Faculty.query.get(student.faculty_id) if student.faculty_id else None
    return jsonify({'success': True,
        'student': {'name': student.full_name, 'matric_no': student.matric_no, 'email': student.email,
                    'parent_phone': student.parent_phone, 'faculty': fac.name if fac else 'N/A',
                    'department': dept.name if dept else 'N/A', 'level': student.level or 'N/A',
                    'device_bound': student.device_hash is not None},
        'stats': {'total': total, 'present': present, 'absent': total-present,
                  'rate': round(present/total*100) if total > 0 else 0},
        'sessions': [{'date': a.timestamp.strftime('%d %b %Y'), 'time': a.timestamp.strftime('%I:%M %p'),
                      'status': a.status, 'course': a.course_code} for a in attendances]})

@app.route('/lecturer/reset_device/<int:student_id>', methods=['POST'])
def reset_device(student_id):
    if 'lecturer_id' not in session: return jsonify({'success': False})
    student = Student.query.get_or_404(student_id)
    student.device_hash = None; db.session.commit()
    return jsonify({'success': True, 'message': f'Device reset for {student.full_name}'})

@app.route('/lecturer/all_students')
def all_students():
    if 'lecturer_id' not in session: return jsonify({'success': False})
    result = []
    for s in Student.query.all():
        atts = Attendance.query.filter_by(student_id=s.id).all()
        total = len(atts); present = len([a for a in atts if a.status == 'present'])
        dept = Department.query.get(s.department_id) if s.department_id else None
        result.append({'id': s.id, 'name': s.full_name, 'matric_no': s.matric_no,
                       'department': dept.name if dept else 'N/A', 'level': s.level or 'N/A',
                       'present': present, 'absent': total-present, 'total': total,
                       'rate': round(present/total*100) if total > 0 else 0,
                       'device_bound': s.device_hash is not None})
    return jsonify({'success': True, 'students': result})

@app.route('/lecturer/download_attendance/<int:course_id>')
def download_attendance(course_id):
    if 'lecturer_id' not in session: return redirect(url_for('lecturer_login'))
    course = Course.query.get_or_404(course_id)
    sessions = ClassSession.query.filter_by(course_id=course_id).order_by(ClassSession.started_at).all()
    students = Student.query.order_by(Student.matric_no).all()
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(['S/N','Matric No','Full Name','Level','Department'] +
                    [f"Session {i+1}\n{s.started_at.strftime('%d/%m/%y')}" for i,s in enumerate(sessions)] +
                    ['Total Present','Total Absent','Rate (%)'])
    for i, student in enumerate(students, 1):
        dept = Department.query.get(student.department_id) if student.department_id else None
        row = [i, student.matric_no, student.full_name, student.level or '', dept.name if dept else '']
        tp = 0
        for s in sessions:
            att = Attendance.query.filter_by(student_id=student.id, session_id=s.id).first()
            if att:
                row.append('P' if att.status == 'present' else 'A')
                if att.status == 'present': tp += 1
            else: row.append('-')
        tot = len(sessions); ab = tot - tp
        row += [tp, ab, f'{round(tp/tot*100) if tot > 0 else 0}%']
        writer.writerow(row)
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', as_attachment=True,
                     download_name=f"{course.code}_attendance_{datetime.now().strftime('%Y%m%d')}.csv")

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        admin = Admin.query.filter_by(username=request.form['username']).first()
        if admin and bcrypt.check_password_hash(admin.password, request.form['password']):
            session['admin_id'] = admin.id; return redirect(url_for('admin_dashboard'))
        return render_template('admin_login.html', error='Invalid credentials')
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_id', None); return redirect(url_for('admin_login'))

@app.route('/admin/dashboard')
def admin_dashboard():
    if 'admin_id' not in session: return redirect(url_for('admin_login'))
    return render_template('admin_dashboard.html',
        faculties=Faculty.query.order_by(Faculty.name).all(),
        departments=Department.query.order_by(Department.name).all(),
        courses=Course.query.order_by(Course.code).all(),
        students=Student.query.all(),
        lecturers=Lecturer.query.all())

@app.route('/admin/faculty/add', methods=['POST'])
def admin_add_faculty():
    if 'admin_id' not in session: return jsonify({'success': False})
    name = request.form.get('name','').strip()
    if Faculty.query.filter_by(name=name).first(): return jsonify({'success': False, 'message': 'Already exists'})
    db.session.add(Faculty(name=name)); db.session.commit()
    return jsonify({'success': True, 'message': 'Faculty added'})

@app.route('/admin/faculty/delete/<int:fid>', methods=['POST'])
def admin_delete_faculty(fid):
    if 'admin_id' not in session: return jsonify({'success': False})
    db.session.delete(Faculty.query.get_or_404(fid)); db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/department/add', methods=['POST'])
def admin_add_department():
    if 'admin_id' not in session: return jsonify({'success': False})
    db.session.add(Department(name=request.form.get('name','').strip(), faculty_id=int(request.form.get('faculty_id')))); db.session.commit()
    return jsonify({'success': True, 'message': 'Department added'})

@app.route('/admin/department/delete/<int:did>', methods=['POST'])
def admin_delete_department(did):
    if 'admin_id' not in session: return jsonify({'success': False})
    db.session.delete(Department.query.get_or_404(did)); db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/course/add', methods=['POST'])
def admin_add_course():
    if 'admin_id' not in session: return jsonify({'success': False})
    db.session.add(Course(code=request.form.get('code','').strip().upper(),
                          title=request.form.get('title','').strip(),
                          level=int(request.form.get('level')), semester=int(request.form.get('semester')),
                          department_id=int(request.form.get('department_id')))); db.session.commit()
    return jsonify({'success': True, 'message': 'Course added'})

@app.route('/admin/course/delete/<int:cid>', methods=['POST'])
def admin_delete_course(cid):
    if 'admin_id' not in session: return jsonify({'success': False})
    db.session.delete(Course.query.get_or_404(cid)); db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/reset_device/<int:student_id>', methods=['POST'])
def admin_reset_device(student_id):
    if 'admin_id' not in session: return jsonify({'success': False})
    s = Student.query.get_or_404(student_id); s.device_hash = None; db.session.commit()
    return jsonify({'success': True, 'message': f'Device reset for {s.full_name}'})

@app.route('/admin/delete_student/<int:student_id>', methods=['POST'])
def admin_delete_student(student_id):
    if 'admin_id' not in session: return jsonify({'success': False})
    db.session.delete(Student.query.get_or_404(student_id)); db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/delete_lecturer/<int:lid>', methods=['POST'])
def admin_delete_lecturer(lid):
    if 'admin_id' not in session: return jsonify({'success': False})
    db.session.delete(Lecturer.query.get_or_404(lid)); db.session.commit()
    return jsonify({'success': True})

if __name__ == '__main__':
    app.run(debug=True)