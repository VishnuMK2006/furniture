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
import cloudinary
import cloudinary.uploader

# ==========================================
# Config
# ==========================================
class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "super-secret-key-change-in-production")
    MONGO_URI = "mongodb+srv://vishnu:tvmk2006@firstsample.c9yehfj.mongodb.net/firstsample?retryWrites=true&w=majority&appName=firstsample"
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "jwt-super-secret-key-change-in-production")

    CLOUDINARY_CLOUD_NAME = "dpebzsbtj"
    CLOUDINARY_API_KEY = "317852785236772"
    CLOUDINARY_API_SECRET = "GQO2xD1SO-hYiJjzl54CPPK_lTQ"


# ==========================================
# Database setup
# ==========================================
db = None

def init_db(app):
    global db
    client = MongoClient(app.config["MONGO_URI"])
    db = client["firstsample"]


# ==========================================
# Blueprint: Auth
# ==========================================
auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/signup", methods=["POST"])
def signup():

    data = request.get_json()

    if not data:
        return jsonify({"message": "Request body must be JSON"}), 400

    if not data.get("username") or not data.get("email") or not data.get("password"):
        return jsonify({"message": "Missing fields"}), 400

    users_collection = db.users

    if users_collection.find_one({"username": data["username"]}):
        return jsonify({"message": "Username already exists"}), 400

    if users_collection.find_one({"email": data["email"]}):
        return jsonify({"message": "Email already exists"}), 400

    hashed_password = generate_password_hash(data["password"])
    role = data.get("role", "user")

    if role == "admin":
        role = "user"

    new_user = {
        "username": data["username"],
        "email": data["email"],
        "password_hash": hashed_password,
        "role": role,
        "created_at": datetime.datetime.utcnow()
    }

    result = users_collection.insert_one(new_user)

    return jsonify({
        "message": "User created successfully",
        "user_id": str(result.inserted_id),
        "role": role
    }), 201


@auth_bp.route("/login", methods=["POST"])
def login():

    data = request.get_json()

    if not data.get("email") or not data.get("password"):
        return jsonify({"message": "Missing email or password"}), 400

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
        return jsonify({"message": "Admins only"}), 403

    current_user_id = get_jwt_identity()

    return jsonify({
        "message": f"Welcome Admin! User ID: {current_user_id}"
    }), 200


# ==========================================
# Blueprint: Products
# ==========================================
products_bp = Blueprint("products", __name__)


@products_bp.route("/", methods=["GET"])
def get_products():

    products_collection = db.products

    category = request.args.get("category")
    query = {}

    if category and category != "All":
        query["category"] = category

    products = []

    for p in products_collection.find(query):
        p["id"] = str(p.pop("_id"))
        products.append(p)

    return jsonify(products), 200


@products_bp.route("/", methods=["POST"])
@jwt_required()
def create_product():

    claims = get_jwt()

    if claims.get("role") != "admin":
        return jsonify({"message": "Admins only"}), 403

    data = request.form
    image = request.files.get("image")

    image_url = None

    if image:
        try:
            upload = cloudinary.uploader.upload(image)
            image_url = upload.get("secure_url")
        except Exception as e:
            return jsonify({"message": f"Image upload failed: {str(e)}"}), 500

    try:

        new_product = {
            "name": data.get("name", ""),
            "amountInStock": int(data.get("amountInStock", 0)),
            "currentPrice": float(data.get("currentPrice", 0)),
            "previousPrice": float(data.get("previousPrice", data.get("currentPrice", 0))),
            "deliveryPrice": float(data.get("deliveryPrice", 0)),
            "deliveryInDays": int(data.get("deliveryInDays", 7)),
            "isAmazonChoice": data.get("isAmazonChoice", "false").lower() == "true",
            "category": data.get("category", "Sofas"),
            "sku": data.get("sku", ""),
            "description": data.get("description", ""),
            "imageUrl": image_url,
            "model3DUrl": None
        }

        products_collection = db.products
        result = products_collection.insert_one(new_product)

        new_product["id"] = str(result.inserted_id)

        return jsonify(new_product), 201

    except ValueError as e:
        return jsonify({"message": f"Invalid numeric value: {str(e)}"}), 400


# ==========================================
# Application Factory
# ==========================================
def create_app():

    app = Flask(__name__)
    app.config.from_object(Config)

    CORS(app)
    JWTManager(app)

    cloudinary.config(
        cloud_name=app.config["CLOUDINARY_CLOUD_NAME"],
        api_key=app.config["CLOUDINARY_API_KEY"],
        api_secret=app.config["CLOUDINARY_API_SECRET"]
    )

    init_db(app)

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(products_bp, url_prefix="/api/products")

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
