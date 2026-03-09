import os
import datetime
from flask import Flask, request, jsonify, Blueprint
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    jwt_required,
    get_jwt_identity,
    get_jwt
)
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient

# ==========================================
# Config
# ==========================================
class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "super-secret-key-change-in-production")
    MONGO_URI = "mongodb+srv://vishnu:tvmk2006@firstsample.c9yehfj.mongodb.net/firstsample?retryWrites=true&w=majority&appName=firstsample"
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "jwt-super-secret-key-change-in-production")


# ==========================================
# Database setup
# ==========================================
db = None


def init_db(app):
    global db
    client = MongoClient(app.config["MONGO_URI"])
    db = client["firstsample"]   # explicitly selecting database


# ==========================================
# Routes
# ==========================================
auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/signup", methods=["POST"])
def signup():
    data = request.get_json()

    if not data:
        return jsonify({"message": "Request body must be JSON"}), 400

    if not data.get("username") or not data.get("email") or not data.get("password"):
        return jsonify({"message": "Missing required fields (username, email, password)"}), 400

    users_collection = db.users

    if users_collection.find_one({"username": data["username"]}):
        return jsonify({"message": "Username already exists"}), 400

    if users_collection.find_one({"email": data["email"]}):
        return jsonify({"message": "Email already exists"}), 400

    hashed_password = generate_password_hash(data["password"])
    role = data.get("role", "user")

    # Admin creation safeguard
    if role == "admin":
        role = "user"

    new_user = {
        "username": data["username"],
        "email": data["email"],
        "password_hash": hashed_password,
        "role": role,
        "created_at": datetime.datetime.utcnow(),
    }

    result = users_collection.insert_one(new_user)

    return jsonify({
        "message": "User created successfully",
        "role": role,
        "user_id": str(result.inserted_id)
    }), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json()

    if not data:
        return jsonify({"message": "Request body must be JSON"}), 400

    if not data.get("email") or not data.get("password"):
        return jsonify({"message": "Missing required fields (email, password)"}), 400

    users_collection = db.users
    user = users_collection.find_one({"email": data["email"]})

    if not user or not check_password_hash(user["password_hash"], data["password"]):
        return jsonify({"message": "Invalid credentials"}), 401

    additional_claims = {"role": user["role"]}

    access_token = create_access_token(
        identity=str(user["_id"]),
        additional_claims=additional_claims
    )

    return jsonify({
        "message": "Login successful",
        "access_token": access_token,
        "user": {
            "id": str(user["_id"]),
            "username": user["username"],
            "email": user["email"],
            "role": user["role"]
        }
    }), 200


@auth_bp.route("/admin-only", methods=["GET"])
@jwt_required()
def admin_only():
    claims = get_jwt()

    if claims.get("role") != "admin":
        return jsonify({"message": "Access forbidden: Admins only"}), 403

    current_user_id = get_jwt_identity()

    return jsonify({
        "message": f"Welcome Admin! (User ID: {current_user_id})"
    }), 200


# ==========================================
# Application Factory
# ==========================================
def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Extensions
    CORS(app)
    JWTManager(app)

    # Database
    init_db(app)

    # Blueprints
    app.register_blueprint(auth_bp, url_prefix="/api/auth")

    @app.route("/health", methods=["GET"])
    def health_check():
        return {"status": "healthy"}

    return app


# ==========================================
# Run Server
# ==========================================
if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=5000)