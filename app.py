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

app = Flask(__name__)
app.config["SECRET_KEY"] = "super_secret_blog_key"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///blog.db"
app.config["UPLOAD_FOLDER"] = os.path.join("static", "uploads")

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# ==========================================
# DATABASE MODELS
# ==========================================


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)


post_tags = db.Table(
    "post_tags",
    db.Column("post_id", db.Integer, db.ForeignKey("post.id"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("tag.id"), primary_key=True),
)

# Association table for Many-to-Many relationship between Posts and Categories
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

    # Self-referential relationship for nesting
    parent_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=True)
    subcategories = db.relationship(
        "Category", backref=db.backref("parent", remote_side=[id]), lazy="dynamic"
    )


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    slug = db.Column(db.String(150), nullable=False, unique=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    tags = db.relationship(
        "Tag", secondary=post_tags, backref=db.backref("posts", lazy=True)
    )


class Tag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    slug = db.Column(db.String(150), nullable=False, unique=True)
    content = db.Column(db.Text, nullable=False)  # Stores raw HTML from JS editor
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=False)
    tags = db.relationship(
        "Tag",
        secondary=post_tags,
        lazy="subquery",
        backref=db.backref("posts", lazy=True),
    )


class Media(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200), nullable=False)
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# Helper to create URL-friendly slugs
def generate_slug(title):
    slug = re.sub(r"[^\w\s-]", "", title).strip().lower()
    return re.sub(r"[-\s]+", "-", slug)


# ==========================================
# PUBLIC ROUTES
# ==========================================


@app.route("/")
def index():
    search_query = request.args.get("q", "")
    category_filter = request.args.get("category", "")

    query = Post.query

    if category_filter:
        query = query.join(Category).filter(Category.name == category_filter)

    if search_query:
        query = query.filter(
            Post.title.ilike(f"%{search_query}%")
            | Post.content.ilike(f"%{search_query}%")
        )

    posts = query.order_by(Post.created_at.desc()).all()
    categories = Category.query.all()

    return render_template(
        "index.html",
        posts=posts,
        categories=categories,
        current_cat=category_filter,
        search_query=search_query,
    )


@app.route("/admin/edit/<int:post_id>", methods=["GET", "POST"])
@login_required
def edit_post(post_id):
    post = Post.query.get_or_404(post_id)

    if request.method == "POST":
        post.title = request.form.get("title")
        post.content = request.form.get("content")
        category_name = request.form.get("category")
        tags_input = request.form.get("tags", "")

        # Update Category
        category = Category.query.filter_by(name=category_name).first()
        if not category:
            category = Category(name=category_name)
            db.session.add(category)
        post.category = category

        # Update Tags
        post.tags = []  # Clear existing associations
        if tags_input:
            tag_names = [t.strip().lower() for t in tags_input.split(",")]
            for t_name in tag_names:
                tag = Tag.query.filter_by(name=t_name).first() or Tag(name=t_name)
                post.tags.append(tag)

        db.session.commit()
        flash("Post updated successfully!")
        return redirect(url_for("view_post", slug=post.slug))

    categories = Category.query.all()
    # Join tags back into a comma-separated string for the input field
    tag_string = ", ".join([t.name for t in post.tags])
    return render_template(
        "editor.html", post=post, categories=categories, tag_string=tag_string
    )


@app.route("/admin/category/add", methods=["POST"])
@login_required
def add_category():
    name = request.form.get("cat_name")
    parent_id = request.form.get("parent_id")  # Can be None

    new_cat = Category(name=name, parent_id=parent_id if parent_id else None)
    db.session.add(new_cat)
    db.session.commit()
    return redirect(url_for("manage_media"))  # Or a dedicated settings page


@app.route("/post/<slug>")
def view_post(slug):
    post = Post.query.filter_by(slug=slug).first_or_404()
    return render_template("post.html", post=post)


# ==========================================
# ADMIN & EDITOR ROUTES
# ==========================================


@app.route("/admin/new_post", methods=["GET", "POST"])
@login_required
def new_post():
    if request.method == "POST":
        title = request.form.get("title")
        content = request.form.get("content")  # The HTML from Quill/TinyMCE
        category_name = request.form.get("category")
        tags_input = request.form.get("tags", "")

        # Handle Category
        category = Category.query.filter_by(name=category_name).first()
        if not category:
            category = Category(name=category_name)
            db.session.add(category)

        # Handle Post Creation
        slug = generate_slug(title)

        # Ensure unique slug
        base_slug = slug
        counter = 1
        while Post.query.filter_by(slug=slug).first():
            slug = f"{base_slug}-{counter}"
            counter += 1

        new_post = Post(title=title, slug=slug, content=content, category=category)

        # Handle Tags (comma separated)
        if tags_input:
            tag_names = [t.strip().lower() for t in tags_input.split(",")]
            for t_name in tag_names:
                if t_name:
                    tag = Tag.query.filter_by(name=t_name).first()
                    if not tag:
                        tag = Tag(name=t_name)
                        db.session.add(tag)
                    new_post.tags.append(tag)

        db.session.add(new_post)
        db.session.commit()

        flash("Post published successfully!")
        return redirect(url_for("view_post", slug=new_post.slug))

    categories = Category.query.all()
    return render_template("editor.html", categories=categories)


# --- JS Editor Image Upload API ---
# The JS text editor will send an AJAX POST request here when you insert an image.
@app.route("/admin/upload_media", methods=["POST"])
@login_required
def upload_media():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)

    # Avoid overwriting files with the same name
    base, ext = os.path.splitext(filename)
    counter = 1
    while os.path.exists(filepath):
        filename = f"{base}_{counter}{ext}"
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        counter += 1

    file.save(filepath)

    # Save to media library DB
    new_media = Media(filename=filename)
    db.session.add(new_media)
    db.session.commit()

    # Return the URL so the JS editor can insert the <img> tag
    image_url = url_for("static", filename=f"uploads/{filename}")
    return jsonify({"url": image_url})


@app.route("/admin/media", methods=["GET", "POST"])
@login_required
def manage_media():
    if request.method == "POST":
        if "file" not in request.files:
            flash("No file part")
            return redirect(request.url)

        file = request.files["file"]
        if file.filename == "":
            flash("No selected file")
            return redirect(request.url)

        if file:
            filename = secure_filename(file.filename)
            # Handle duplicate names
            base, ext = os.path.splitext(filename)
            counter = 1
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            while os.path.exists(filepath):
                filename = f"{base}_{counter}{ext}"
                filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                counter += 1

            file.save(filepath)

            new_media = Media(filename=filename)
            db.session.add(new_media)
            db.session.commit()
            flash(f"Successfully uploaded {filename}")
            return redirect(url_for("manage_media"))

    all_media = Media.query.order_by(Media.upload_date.desc()).all()
    return render_template("media.html", media_items=all_media)


# ==========================================
# AUTHENTICATION ROUTES
# ==========================================


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for("new_post"))
        flash("Invalid credentials")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))


# ==========================================
# INITIALIZATION
# ==========================================

if __name__ == "__main__":
    with app.app_context():
        db.create_all()

        # Setup initial admin account
        if not User.query.filter_by(username="admin").first():
            hashed_pw = generate_password_hash("admin", method="pbkdf2:sha256")
            admin = User(username="admin", password=hashed_pw)
            db.session.add(admin)

        # Setup default category to prevent errors
        if not Category.query.filter_by(name="Uncategorized").first():
            default_cat = Category(name="Uncategorized")
            db.session.add(default_cat)

        db.session.commit()

    app.run(debug=True)
