import os
import re
from datetime import datetime
import time
import threading
import logging

# from functools import wraps
from io import StringIO
from logging.handlers import RotatingFileHandler
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    session,
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    login_required,
    logout_user,
    current_user,
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from urllib.parse import urlparse
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["SECRET_KEY"] = "n890gf-dev-secret"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///blog_final.db"
app.config["UPLOAD_FOLDER"] = os.path.join("static", "uploads")
# ADD THIS LINE: Allow up to 64 Megabytes per request
app.config["MAX_CONTENT_LENGTH"] = None

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


# --- Login attempt logging (split logs) ---
LOG_DIR = os.path.join(CWD_PATH if "CWD_PATH" in globals() else os.getcwd(), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

log_formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | ip=%(ip)s | user=%(user)s | status=%(status)s | ua=%(ua)s | msg=%(message)s"
)


def _create_logger(name, filename):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        handler = RotatingFileHandler(
            os.path.join(LOG_DIR, filename), maxBytes=5 * 1024 * 1024, backupCount=5
        )
        handler.setFormatter(log_formatter)
        logger.addHandler(handler)

    return logger


login_success_logger = _create_logger("login_success", "login_success.log")
login_failure_logger = _create_logger("login_failure", "login_failure.log")
login_security_logger = _create_logger("login_security", "login_security.log")


LOGIN_ATTEMPTS = {}
LOGIN_LOCK = threading.Lock()
MAX_ATTEMPTS = 3
BLOCK_WINDOW_SECONDS = 300  # 5 minutes
BAN_THRESHOLD = 3  # number of times hitting rate limit before ban
BAN_DURATION_SECONDS = 3600  # 1 hour


# --- MODELS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)


post_categories = db.Table(
    "post_categories",
    db.Column("post_id", db.Integer, db.ForeignKey("post.id"), primary_key=True),
    db.Column(
        "category_id", db.Integer, db.ForeignKey("category.id"), primary_key=True
    ),
)

post_tags = db.Table(
    "post_tags",
    db.Column("post_id", db.Integer, db.ForeignKey("post.id"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("tag.id"), primary_key=True),
)


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)


class Tag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    slug = db.Column(db.String(150), nullable=False, unique=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    categories = db.relationship(
        "Category",
        secondary=post_categories,
        backref=db.backref("posts", lazy="dynamic"),
    )
    tags = db.relationship(
        "Tag",
        secondary=post_tags,
        backref=db.backref("posts", lazy="select"),
    )


class BannedIPs(db.Model):
    __tablename__ = "banned_ips"
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(50))
    username = db.Column(db.String(100))
    ban_time = db.Column(db.Float, default=0.0)
    count = db.Column(db.Integer, default=0)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# --- ROUTES ---


@app.errorhandler(413)
@app.errorhandler(RequestEntityTooLarge)
def app_handle_413(e):
    return jsonify({"error": "File is too large. Max size is 64MB."}), 413


@app.context_processor
def inject_categories():
    return dict(categories=Category.query.order_by(Category.name).all())


@app.route("/")
def index():
    search_query = request.args.get("search", "")
    cat_name = request.args.get("category", "")
    tag_name = request.args.get("tag", "")

    query = Post.query

    # 1. Filter by Search
    if search_query:
        query = query.filter(
            Post.title.contains(search_query) | Post.content.contains(search_query)
        )

    # 2. Filter by Category
    if cat_name:
        query = query.join(Post.categories).filter(Category.name == cat_name)

    # 3. Filter by Tag
    if tag_name:
        query = query.join(Post.tags).filter(Tag.name == tag_name)

    # 4. Fetch the posts
    posts = query.order_by(Post.created_at.desc()).all()

    # 5. Fetch Sidebar Data (Crucial for the widgets to appear)
    categories = Category.query.all()
    all_tags = Tag.query.all()

    return render_template(
        "index.html",
        posts=posts,
        categories=categories,
        all_tags=all_tags,
        current_cat=cat_name,
        current_tag=tag_name,
    )


@app.route("/admin/new_post", methods=["GET", "POST"])
@login_required
def new_post():
    if request.method == "POST":
        title = request.form.get("title")
        content = request.form.get("content")
        selected_cat_ids = request.form.getlist(
            "categories"
        )  # Gets list of IDs from checkboxes

        slug = re.sub(r"[-\s]+", "-", re.sub(r"[^\w\s-]", "", title).strip().lower())
        post = Post(title=title, slug=slug, content=content)

        # Add existing categories by ID
        for cid in selected_cat_ids:
            cat = Category.query.get(cid)
            if cat:
                post.categories.append(cat)

        tags_input = request.form.get("tags", "")
        if tags_input:
            # Split by comma, remove whitespace, and ignore empty strings
            tag_names = [n.strip() for n in tags_input.split(",") if n.strip()]
            for name in tag_names:
                tag = Tag.query.filter_by(name=name).first()
                if not tag:
                    tag = Tag(name=name)
                    db.session.add(tag)
                post.tags.append(tag)

        db.session.add(post)
        db.session.commit()
        return redirect(url_for("view_post", slug=post.slug))

    categories = Category.query.order_by(Category.name).all()
    files = (
        os.listdir(app.config["UPLOAD_FOLDER"])
        if os.path.exists(app.config["UPLOAD_FOLDER"])
        else []
    )
    return render_template("editor.html", categories=categories, post=None, files=files)


@app.route("/admin/edit/<int:post_id>", methods=["GET", "POST"])
@login_required
def edit_post(post_id):
    post = Post.query.get_or_404(post_id)
    if request.method == "POST":
        post.title = request.form.get("title")
        post.content = request.form.get("content")

        # Clear and re-add categories
        post.categories = []
        for cid in request.form.getlist("categories"):
            cat = Category.query.get(cid)
            if cat:
                post.categories.append(cat)

        post.tags = []
        tags_input = request.form.get("tags", "")
        tag_names = [n.strip() for n in tags_input.split(",") if n.strip()]
        for name in tag_names:
            tag = Tag.query.filter_by(name=name).first()
            if not tag:
                tag = Tag(name=name)
                db.session.add(tag)
            post.tags.append(tag)

        db.session.commit()
        return redirect(url_for("view_post", slug=post.slug))

    categories = Category.query.order_by(Category.name).all()
    all_tags = Tag.query.all()
    files = (
        os.listdir(app.config["UPLOAD_FOLDER"])
        if os.path.exists(app.config["UPLOAD_FOLDER"])
        else []
    )
    return render_template(
        "editor.html",
        post=post,
        categories=categories,
        all_tags=all_tags,
        files=files,
    )


@app.route("/admin/delete/<int:post_id>", methods=["GET", "POST"])
@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    if not post:
        flash("Post not found.")
        return redirect(url_for("index"))

    # Remove associations in the many-to-many table first (SQLAlchemy usually handles this, but being explicit is safer)
    post.categories = []
    post.tags = []

    db.session.delete(post)
    db.session.commit()
    flash("Post deleted successfully.")
    return redirect(url_for("index"))


@app.route("/admin/media/upload", methods=["POST"])
@login_required
def upload_to_gallery():
    if "file" not in request.files:
        flash("No file part")
        return redirect(request.url)

    file = request.files["file"]
    if file.filename == "":
        flash("No selected file")
        return redirect(request.url)

    if file:
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
        flash(f"File {filename} uploaded successfully!")

    return redirect(url_for("media_gallery"))


@app.route("/admin/media")
@login_required
def media_gallery():
    # List all files in the upload directory
    files = []
    if os.path.exists(app.config["UPLOAD_FOLDER"]):
        files = os.listdir(app.config["UPLOAD_FOLDER"])
        # Sort by newest first (optional)
        files.sort(
            key=lambda x: os.path.getmtime(
                os.path.join(app.config["UPLOAD_FOLDER"], x)
            ),
            reverse=True,
        )

    return render_template("media.html", files=files)


@app.route("/admin/media/delete/<filename>", methods=["POST"])
@login_required
def delete_media(filename):
    # Secure the filename to prevent directory traversal attacks
    filename = secure_filename(filename)
    file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            flash(f"File {filename} deleted successfully.")
        else:
            flash(f"Error: File {filename} not found.", "error")
    except Exception as e:
        flash(f"Error deleting file: {str(e)}", "error")

    return redirect(url_for("media_gallery"))


@app.route("/post/<slug>")
def view_post(slug):
    post = Post.query.filter_by(slug=slug).first_or_404()
    return render_template("post.html", post=post)


@app.route("/admin/category/add", methods=["POST"])
@login_required
def add_category():
    name = request.form.get("name")
    if name:
        existing = Category.query.filter_by(name=name).first()
        if not existing:
            new_cat = Category(name=name)
            db.session.add(new_cat)
            db.session.commit()
            return jsonify({"success": True, "id": new_cat.id, "name": new_cat.name})
    return jsonify({"success": False}), 400


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        user_agent = request.headers.get("User-Agent", "")
        now = time.time()
        username = request.form.get("username")
        log_user = username or "-"

        # --- Check ban list (SQLAlchemy) ---
        ban_entry = BannedIPs.query.filter_by(ip=ip, username=username).first()

        if ban_entry:
            if now - ban_entry.ban_time < BAN_DURATION_SECONDS:
                login_security_logger.warning(
                    "banned_ip_attempt",
                    extra={
                        "ip": ip,
                        "user": log_user,
                        "status": "banned",
                        "ua": user_agent,
                    },
                )
                return render_template(
                    "login.html",
                    error="Too many attempts. You are temporarily banned.",
                )
            else:
                # Ban expired, remove it
                db.session.delete(ban_entry)
                db.session.commit()

        # --- Rate limit check ---
        with LOGIN_LOCK:
            attempts = LOGIN_ATTEMPTS.get(ip, [])
            attempts = [t for t in attempts if now - t < BLOCK_WINDOW_SECONDS]
            LOGIN_ATTEMPTS[ip] = attempts

            if len(attempts) >= MAX_ATTEMPTS:
                # Re-fetch ban_entry in case it was deleted above
                ban_entry = BannedIPs.query.filter_by(ip=ip, username=username).first()

                if ban_entry:
                    ban_entry.count += 1
                else:
                    ban_entry = BannedIPs(ip=ip, username=username, ban_time=0, count=1)
                    db.session.add(ban_entry)

                # Check if we should elevate to a timed ban
                if ban_entry.count >= BAN_THRESHOLD:
                    ban_entry.ban_time = now
                    db.session.commit()

                    login_security_logger.warning(
                        "ip_banned",
                        extra={
                            "ip": ip,
                            "user": log_user,
                            "status": "banned",
                            "ua": user_agent,
                        },
                    )
                    return render_template(
                        "login.html",
                        error="Too many attempts. You are temporarily banned.",
                    )

                db.session.commit()

                login_security_logger.warning(
                    "rate_limited",
                    extra={
                        "ip": ip,
                        "user": log_user,
                        "status": "blocked",
                        "ua": user_agent,
                    },
                )
                return render_template(
                    "login.html", error="Too many attempts. Try again later."
                )

        password = request.form.get("password")
        user = User.query.filter_by(username=username).first()
        # user = User.query.filter_by(username=request.form.get("username")).first()
        if user and check_password_hash(user.password, password):
            # reset attempts on success
            with LOGIN_LOCK:
                LOGIN_ATTEMPTS.pop(ip, None)

            # Clear any ban tracking for this user/ip combo
            BannedIPs.query.filter_by(ip=ip, username=username).delete()
            db.session.commit()

            remember = request.form.get("remember") == "on"
            session["user_id"] = user.id
            session.permanent = remember

            login_success_logger.info(
                "login_success",
                extra={
                    "ip": ip,
                    "user": log_user,
                    "status": "success",
                    "ua": user_agent,
                },
            )
            login_user(user)
            return redirect(url_for("index"))
        else:
            # record failed attempt
            with LOGIN_LOCK:
                LOGIN_ATTEMPTS.setdefault(ip, []).append(time.time())

            login_failure_logger.info(
                "login_failure",
                extra={
                    "ip": ip,
                    "user": log_user,
                    "status": "failure",
                    "ua": user_agent,
                },
            )
            return render_template("login.html", error="Invalid credentials")

    return render_template("login.html")


@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("index"))


@app.context_processor
def inject_sidebar_data():
    return dict(
        categories=Category.query.order_by(Category.name).all(),
        all_tags=Tag.query.order_by(Tag.name).all(),  # Add this
    )


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username="george").first():
            db.session.add(
                User(username="george", password=generate_password_hash("Soccer10"))
            )
        db.session.commit()
    app.run(debug=True)
