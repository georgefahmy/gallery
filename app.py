import os
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
from PIL import Image as PILImage
from PIL.ExifTags import TAGS

app = Flask(__name__)
app.config["SECRET_KEY"] = "your_super_secret_key_change_this"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///gallery.db"
app.config["UPLOAD_FOLDER"] = os.path.join("static", "images")

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


# --- DATABASE MODELS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)


class ImageMeta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200), nullable=False)
    subfolder = db.Column(db.String(200), default="Root")
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    resolution = db.Column(db.String(50))
    camera_model = db.Column(db.String(100))


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# --- ROUTES ---
@app.route("/")
@login_required
def index():
    # Search and Filter logic
    search_query = request.args.get("search", "")
    folder_filter = request.args.get("folder", "Root")

    query = ImageMeta.query

    if folder_filter and folder_filter != "All":
        query = query.filter_by(subfolder=folder_filter)
    if search_query:
        query = query.filter(ImageMeta.filename.ilike(f"%{search_query}%"))

    images = query.order_by(ImageMeta.upload_date.desc()).all()

    # Get distinct folders for the filter dropdown
    folders = [f[0] for f in db.session.query(ImageMeta.subfolder).distinct().all()]

    return render_template(
        "index.html", images=images, folders=folders, current_folder=folder_filter
    )


@app.route("/upload", methods=["POST"])
@login_required
def upload_image():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    files = request.files.getlist("file")
    subfolder = request.form.get("subfolder", "Root")

    # Create subfolder directory if it doesn't exist
    target_dir = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(subfolder))
    os.makedirs(target_dir, exist_ok=True)

    for file in files:
        if file and file.filename:
            filename = secure_filename(file.filename)
            filepath = os.path.join(target_dir, filename)
            file.save(filepath)

            # Extract Metadata
            resolution = "Unknown"
            camera = "Unknown"
            try:
                with PILImage.open(filepath) as img:
                    resolution = f"{img.width}x{img.height}"
                    exif_data = img.getexif()
                    if exif_data:
                        for tag_id, value in exif_data.items():
                            tag = TAGS.get(tag_id, tag_id)
                            if tag == "Model":
                                camera = str(value)
            except Exception:
                pass  # Skip metadata if file is not a valid image or lacks EXIF

            # Save to Database
            new_image = ImageMeta(
                filename=filename,
                subfolder=secure_filename(subfolder),
                resolution=resolution,
                camera_model=camera,
            )
            db.session.add(new_image)

    db.session.commit()
    return jsonify({"success": "Files uploaded successfully"})


@app.route("/delete/<int:image_id>", methods=["POST"])
@login_required
def delete_image(image_id):
    image = ImageMeta.query.get_or_404(image_id)
    file_path = os.path.join(
        app.config["UPLOAD_FOLDER"], image.subfolder, image.filename
    )

    if os.path.exists(file_path):
        os.remove(file_path)

    db.session.delete(image)
    db.session.commit()
    return redirect(url_for("index"))


# --- AUTH ROUTES ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for("index"))
        flash("Invalid credentials")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        # Create a default admin user if none exists (Password: admin)
        if not User.query.filter_by(username="admin").first():
            hashed_pw = generate_password_hash("admin", method="pbkdf2:sha256")
            admin = User(username="admin", password=hashed_pw)
            db.session.add(admin)
            db.session.commit()

    app.run(debug=True)
