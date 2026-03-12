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
from bson import ObjectId
import cloudinary
import cloudinary.uploader
import cloudinary.api

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

DEFAULT_CATEGORIES = [
    "Living Room",
    "Bedroom",
    "Dining",
    "Office Furniture",
]


def init_db(app):
    global db
    client = MongoClient(app.config["MONGO_URI"])
    db = client["firstsample"]   # explicitly selecting database


def ensure_default_categories():
    categories_collection = db.categories
    for name in DEFAULT_CATEGORIES:
        categories_collection.update_one(
            {"name": name},
            {
                "$setOnInsert": {
                    "name": name,
                    "created_at": datetime.datetime.utcnow(),
                }
            },
            upsert=True,
        )


def require_admin_claims(claims):
    if claims.get("role") != "admin":
        return jsonify({"message": "Access forbidden: Admins only"}), 403
    return None


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

    # Admin creation safeguard (Commented out for development testing)
    # if role == "admin":
    #     role = "user"

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
# Blueprint: Products
# ==========================================
products_bp = Blueprint("products", __name__)


def parse_product_payload(data, image_url=None):
    # Helper to safely convert to int/float
    def safe_int(val, default=0):
        try:
            return int(val) if val and str(val).strip() else default
        except Exception:
            return default

    def safe_float(val, default=0.0):
        try:
            return float(val) if val and str(val).strip() else default
        except Exception:
            return default

    categories_collection = db.categories
    raw_category = (data.get("category") or "").strip()
    if not raw_category:
        return None, (jsonify({"message": "Category is required"}), 400)

    existing_category = categories_collection.find_one({"name": raw_category})
    if not existing_category:
        return None, (jsonify({"message": "Invalid category. Create category first."}), 400)

    parsed = {
        "name": data.get("name", "Unnamed Product"),
        "amountInStock": safe_int(data.get("amountInStock")),
        "currentPrice": safe_float(data.get("currentPrice")),
        "previousPrice": safe_float(data.get("previousPrice"), safe_float(data.get("currentPrice"))),
        "deliveryPrice": safe_float(data.get("deliveryPrice")),
        "deliveryInDays": safe_int(data.get("deliveryInDays", 7)),
        "isAmazonChoice": data.get("isAmazonChoice", "false").lower() == "true",
        "category": raw_category,
        "sku": data.get("sku", ""),
        "description": data.get("description", ""),
        "imageUrl": image_url,
        "model3DUrl": None,
        "created_at": datetime.datetime.utcnow(),
    }
    return parsed, None

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
    admin_error = require_admin_claims(claims)
    if admin_error:
        print(f"DEBUG: Access denied for role: {claims.get('role')}")
        return admin_error
        
    data = request.form
    image = request.files.get("image")
    print(f"DEBUG: Received product data: {data}")
    
    image_url = None
    if image:
        try:
            print(f"DEBUG: Uploading image to Cloudinary...")
            upload_result = cloudinary.uploader.upload(image)
            image_url = upload_result.get("secure_url")
            print(f"DEBUG: Cloudinary URL: {image_url}")
        except Exception as e:
            print(f"ERROR: Cloudinary upload failed: {str(e)}")
            return jsonify({"message": f"Image upload failed: {str(e)}"}), 500
            
    try:
        new_product, parse_error = parse_product_payload(data, image_url=image_url)
        if parse_error:
            return parse_error
        
        products_collection = db.products
        result = products_collection.insert_one(new_product)
        new_product["id"] = str(result.inserted_id)
        new_product.pop("_id", None)
        if "created_at" in new_product:
            new_product["created_at"] = new_product["created_at"].isoformat()
        
        print(f"DEBUG: Product created successfully: {new_product['id']}")
        return jsonify(new_product), 201
    except Exception as e:
        print(f"ERROR: Product creation failed: {str(e)}")
        return jsonify({"message": f"Server error: {str(e)}"}), 500


@products_bp.route("/<product_id>", methods=["DELETE"])
@jwt_required()
def delete_product(product_id):
    claims = get_jwt()
    admin_error = require_admin_claims(claims)
    if admin_error:
        return admin_error

    try:
        oid = ObjectId(product_id)
    except Exception:
        return jsonify({"message": "Invalid product id"}), 400

    products_collection = db.products
    result = products_collection.delete_one({"_id": oid})

    if result.deleted_count == 0:
        return jsonify({"message": "Product not found"}), 404

    return jsonify({"message": "Product deleted successfully"}), 200


# ==========================================
# Blueprint: Categories
# ==========================================
categories_bp = Blueprint("categories", __name__)


@categories_bp.route("/", methods=["GET"])
def get_categories():
    categories_collection = db.categories
    categories = []
    for category in categories_collection.find().sort("name", 1):
        category["id"] = str(category.pop("_id"))
        if "created_at" in category and hasattr(category["created_at"], "isoformat"):
            category["created_at"] = category["created_at"].isoformat()
        categories.append(category)
    return jsonify(categories), 200


@categories_bp.route("/", methods=["POST"])
@jwt_required()
def create_category():
    claims = get_jwt()
    admin_error = require_admin_claims(claims)
    if admin_error:
        return admin_error

    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"message": "Category name is required"}), 400

    categories_collection = db.categories
    if categories_collection.find_one({"name": name}):
        return jsonify({"message": "Category already exists"}), 400

    new_category = {
        "name": name,
        "created_at": datetime.datetime.utcnow(),
    }
    result = categories_collection.insert_one(new_category)
    return jsonify({
        "id": str(result.inserted_id),
        "name": name,
        "created_at": new_category["created_at"].isoformat(),
    }), 201


@categories_bp.route("/<category_id>", methods=["PUT"])
@jwt_required()
def update_category(category_id):
    claims = get_jwt()
    admin_error = require_admin_claims(claims)
    if admin_error:
        return admin_error

    data = request.get_json() or {}
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return jsonify({"message": "Category name is required"}), 400

    try:
        oid = ObjectId(category_id)
    except Exception:
        return jsonify({"message": "Invalid category id"}), 400

    categories_collection = db.categories
    existing = categories_collection.find_one({"_id": oid})
    if not existing:
        return jsonify({"message": "Category not found"}), 404

    duplicate = categories_collection.find_one({"name": new_name, "_id": {"$ne": oid}})
    if duplicate:
        return jsonify({"message": "Another category with this name already exists"}), 400

    categories_collection.update_one({"_id": oid}, {"$set": {"name": new_name}})
    db.products.update_many(
        {"category": existing["name"]},
        {"$set": {"category": new_name}},
    )

    return jsonify({"id": category_id, "name": new_name}), 200


@categories_bp.route("/<category_id>", methods=["DELETE"])
@jwt_required()
def delete_category(category_id):
    claims = get_jwt()
    admin_error = require_admin_claims(claims)
    if admin_error:
        return admin_error

    try:
        oid = ObjectId(category_id)
    except Exception:
        return jsonify({"message": "Invalid category id"}), 400

    categories_collection = db.categories
    existing = categories_collection.find_one({"_id": oid})
    if not existing:
        return jsonify({"message": "Category not found"}), 404

    products_using_category = db.products.count_documents({"category": existing["name"]})
    if products_using_category > 0:
        return jsonify({
            "message": "Category is in use by products. Reassign products before deleting.",
            "products_using_category": products_using_category,
        }), 400

    categories_collection.delete_one({"_id": oid})
    return jsonify({"message": "Category deleted successfully"}), 200


# ==========================================
# Application Factory
# ==========================================
def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Extensions
    CORS(app)
    JWTManager(app)

    cloudinary.config(
        cloud_name=app.config["CLOUDINARY_CLOUD_NAME"],
        api_key=app.config["CLOUDINARY_API_KEY"],
        api_secret=app.config["CLOUDINARY_API_SECRET"]
    )

    # Database
    init_db(app)
    ensure_default_categories()

    # Blueprints
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(products_bp, url_prefix="/api/products")
    app.register_blueprint(categories_bp, url_prefix="/api/categories")

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
