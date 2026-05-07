import os
import re
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
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


class Category(db.Model):
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
    query = Post.query
    if search_query:
        query = query.filter(
            Post.title.contains(search_query) | Post.content.contains(search_query)
        )
    if cat_name:
        query = (
            query.join(post_categories).join(Category).filter(Category.name == cat_name)
        )

    posts = query.order_by(Post.created_at.desc()).all()
    return render_template("index.html", posts=posts, current_cat=cat_name)


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

        db.session.commit()
        return redirect(url_for("view_post", slug=post.slug))

    categories = Category.query.order_by(Category.name).all()
    files = (
        os.listdir(app.config["UPLOAD_FOLDER"])
        if os.path.exists(app.config["UPLOAD_FOLDER"])
        else []
    )
    return render_template("editor.html", post=post, categories=categories, files=files)


@app.route("/admin/delete/<int:post_id>", methods=["POST"])
@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)

    # Remove associations in the many-to-many table first (SQLAlchemy usually handles this, but being explicit is safer)
    post.categories = []

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
    if request.method == "POST":
        user = User.query.filter_by(username=request.form.get("username")).first()
        if user and check_password_hash(user.password, request.form.get("password")):
            login_user(user)
            next_page = request.args.get("next")
            if not next_page or urlparse(next_page).netloc != "":
                next_page = url_for("index")
            return redirect(next_page)
    return render_template("login.html")


@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("index"))


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username="admin").first():
            db.session.add(
                User(username="admin", password=generate_password_hash("admin"))
            )
        db.session.commit()
    app.run(debug=True)
